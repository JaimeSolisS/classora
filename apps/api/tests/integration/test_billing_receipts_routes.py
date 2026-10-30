"""The receipt download route and the payment reads' ``receipt`` (#98 F1-F3,
G1; P9; the #364 record policy, ADR-0007 D17).

Family A: students SA1, SA2; guardian profiles ``in_family`` (active),
``inactive`` (inactive) and ``linked_only`` (linked to SA1 through
``student_guardians``, not in ``family_guardians``); one receipt. Family B:
guardian ``other_family``; one receipt. Family C: no account.

- F1: ``200 application/pdf``, attachment ``<number>.pdf``, ``no-store``;
- F2: ``401`` anonymous; ``200`` for Owner, Administrator, Finance Staff
  and A's active guardian; the same ``404`` body for everyone else and for
  any family/receipt mismatch; ``422`` for a malformed id;
- F3: a render failure → ``500 internal_error`` and a log line with the
  receipt id only;
- G1: the payment list and detail carry ``receipt`` for staff and the
  guardian.

The ``200`` downloads render PDFs (Pango: the API image and CI). The clock
is frozen at noon on 2031-03-10 (center zone)."""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import service as billing_service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    link_guardian,
    persist_student,
)
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow

STAFF = [OWNER, ADMINISTRATOR, FINANCE_STAFF]
OTHERS = [
    "other_family",
    "inactive",
    "linked_only",
    STUDENT,
    TUTOR,
    ACADEMIC_COORDINATOR,
]


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    made = {
        role: create_user(url, roles=(role,))
        for role in (*STAFF, STUDENT, TUTOR, ACADEMIC_COORDINATOR)
    }
    for name in ("in_family", "other_family", "inactive", "linked_only"):
        made[name] = create_user(url, roles=(GUARDIAN,))
    return made


@dataclass(frozen=True)
class World:
    a: uuid.UUID
    b: uuid.UUID
    c: uuid.UUID
    a_payment: str
    a_receipt: dict[str, str]
    b_receipt: dict[str, str]


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: TestClient, users: Any) -> World:
    url = module_seeded_url

    async def _families(session: AsyncSession) -> tuple[Any, ...]:
        family_a = await persist(session, FamilyFactory)
        family_b = await persist(session, FamilyFactory)
        family_c = await persist(session, FamilyFactory)
        sa1 = await persist_student(session, contacts=0)
        sa2 = await persist_student(session, contacts=0)

        async def guardian(name: str, active: bool = True) -> Any:
            return await persist(
                session, GuardianFactory, user_id=users[name].id, is_active=active
            )

        await add_to_family(
            session,
            family_a,
            sa1,
            sa2,
            await guardian("in_family"),
            await guardian("inactive", active=False),
        )
        await link_guardian(session, sa1, await guardian("linked_only"))
        await add_to_family(session, family_b, await guardian("other_family"))
        return family_a.id, family_b.id, family_c.id, sa1.id

    a, b, c, sa1 = in_uow(url, _families)
    ps.charges(url, (sa1, "100.00", ps.TODAY))
    cookie = sign_in(client, users[FINANCE_STAFF].username)

    def pay(fam: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
        response = ps.pay(client, cookie, fam, body)
        assert response.status_code == 201, response.text
        recorded: dict[str, Any] = response.json()
        return recorded

    paid_a = pay(a, ps.payment_body("100.00", payer_name="Rosa Pérez"))
    paid_b = pay(b, ps.payment_body("40.00", "none"))
    return World(
        a=a,
        b=b,
        c=c,
        a_payment=paid_a["payment"]["id"],
        a_receipt=paid_a["receipt"],
        b_receipt=paid_b["receipt"],
    )


def cookie_of(client: TestClient, users: Any, name: str) -> Any:
    return sign_in(client, users[name].username)


# --- F1, F2 --------------------------------------------------------------------------


@pytest.mark.parametrize("role", STAFF)
def test_staff_download_any_familys_receipt(client, users, world, role):
    cookie = cookie_of(client, users, role)

    for fam, receipt in ((world.a, world.a_receipt), (world.b, world.b_receipt)):
        response = rs.download(client, cookie, fam, receipt["id"])

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="{receipt["number"]}.pdf"'
        )
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith(b"%PDF-")
        assert receipt["number"] in rs.pdf_text(response.content)


def test_the_familys_active_guardian_downloads_it(client, users, world):
    cookie = cookie_of(client, users, "in_family")

    response = rs.download(client, cookie, world.a, world.a_receipt["id"])

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF-")


def test_anonymous_is_401(client, world):
    response = rs.download(client, None, world.a, world.a_receipt["id"])

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def _not_found(response: Any) -> dict[str, Any]:
    assert response.status_code == 404, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == "not_found"
    return {k: v for k, v in body.items() if k != "request_id"}


@pytest.mark.parametrize("name", OTHERS)
def test_everyone_else_gets_the_same_404(client, users, world, name):
    cookie = cookie_of(client, users, name)
    unknown = _not_found(rs.download(client, cookie, uuid.uuid4(), uuid.uuid4()))

    for fam, receipt in (
        (world.a, world.a_receipt["id"]),  # family A's receipt
        (world.b, world.a_receipt["id"]),  # under another family's path
    ):
        assert _not_found(rs.download(client, cookie, fam, receipt)) == unknown


def test_a_guardian_cant_reach_another_familys_receipt(client, users, world):
    cookie = cookie_of(client, users, "other_family")
    own = rs.download(client, cookie, world.b, world.b_receipt["id"])
    assert own.status_code == 200, own.text

    via_own_family = rs.download(client, cookie, world.b, world.a_receipt["id"])
    via_their_family = rs.download(client, cookie, world.a, world.a_receipt["id"])

    assert _not_found(via_own_family) == _not_found(via_their_family)


def test_unknown_ids_and_mismatches_are_404_for_staff(client, users, world):
    cookie = cookie_of(client, users, OWNER)
    unknown = _not_found(rs.download(client, cookie, uuid.uuid4(), uuid.uuid4()))

    for fam, receipt in (
        (world.a, uuid.uuid4()),  # no such receipt
        (uuid.uuid4(), world.a_receipt["id"]),  # no such family
        (world.a, world.b_receipt["id"]),  # B's receipt under A
        (world.c, world.a_receipt["id"]),  # a family without an account
    ):
        assert _not_found(rs.download(client, cookie, fam, receipt)) == unknown


def test_a_malformed_id_is_422(client, users, world):
    cookie = cookie_of(client, users, OWNER)

    response = rs.download(client, cookie, world.a, "not-a-uuid")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- F3 ------------------------------------------------------------------------------


def test_a_render_failure_is_a_logged_500(client, users, world, monkeypatch, caplog):
    def broken(stored: Any) -> bytes:
        raise RuntimeError(f"cannot render for {stored.snapshot['payer_name']}")

    monkeypatch.setattr(billing_service, "render_receipt", broken)
    cookie = cookie_of(client, users, OWNER)

    with caplog.at_level(logging.ERROR, logger="app.workflows.billing_receipts"):
        response = rs.download(client, cookie, world.a, world.a_receipt["id"])

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "internal_error"
    logged = [r for r in caplog.records if r.name == "app.workflows.billing_receipts"]
    assert len(logged) == 1
    message = logged[0].getMessage()
    assert world.a_receipt["id"] in message
    assert "Rosa" not in message
    assert logged[0].exc_info is None


# --- G1 ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [OWNER, "in_family"])
def test_the_payment_reads_carry_the_receipt(client, users, world, name):
    cookie = cookie_of(client, users, name)

    listed = get(client, cookie, ps.account_path(world.a, "/payments")).json()
    detail = get(
        client, cookie, ps.account_path(world.a, f"/payments/{world.a_payment}")
    ).json()

    assert [item["receipt"] for item in listed["items"]] == [world.a_receipt]
    assert detail["receipt"] == world.a_receipt
