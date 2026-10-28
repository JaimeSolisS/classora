"""``GET /api/v1/billing/unbilled-items`` (#91 D, F15) through the real app,
and ``list_unbilled_items`` directly for the statement bound.

Both reasons and the precedence (``no_billing_account`` first); the
``reason`` (single, repeated, invalid) and ``student_id`` filters; the
order; pagination; billing off; charged, package-covered and billable but
unposted items excluded; the D4 bound (30 candidates on 3 price keys: six
statements plus three lookups); list ⇔ sweeper consistency; the access
matrix. Each test starts from ``reset`` (billing off, earlier uncharged
sessions cancelled), so a page holds the test's own items only.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import OffsetParams
from app.modules.billing import service as billing
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import session_charges
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    PRICE_FROM,
    Item,
    add_price,
    charges_of,
    family_name,
    item,
    local,
    reset,
    run,
    set_floor,
)

UNBILLED = "/api/v1/billing/unbilled-items"
DAY = FLOOR + timedelta(days=9)
ITEM_KEYS = {
    "class_session_id",
    "starts_at",
    "ends_at",
    "service_date",
    "state",
    "scheduled_minutes",
    "subject",
    "student",
    "education_level",
    "family",
    "reason",
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(client, create_user(module_seeded_url, roles=(OWNER,)).username)


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    reset(module_seeded_url)


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> dict[str, Any]:
    response = get(client, cookie, UNBILLED, **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def keys(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (entry["class_session_id"], entry["student"]["id"]) for entry in body["items"]
    ]


def key(it: Item) -> tuple[str, str]:
    return (str(it.session), str(it.student))


def names(url: str, it: Item) -> tuple[str, str]:
    [row] = run_sql(
        url,
        "SELECT s.given_names || ' ' || s.family_names AS student, j.name AS subject"
        " FROM students s, subjects j WHERE s.id = :st AND j.id = :sj",
        {"st": it.student, "sj": it.subject},
    )
    return row.student, row.subject


def test_both_reasons_with_the_item_shape(client, staff, module_seeded_url):
    url = module_seeded_url
    orphan = item(url, starts_at=local(DAY, 9), family=False)
    unpriced = item(url, starts_at=local(DAY, 10), price=None, minutes=45)
    item(url, starts_at=local(DAY, 11))  # priced, not yet posted: not listed
    set_floor(url, FLOOR)

    body = listed(client, staff)

    assert body["total"] == 2
    first, second = body["items"]
    assert set(first) == ITEM_KEYS
    assert key(orphan) == (first["class_session_id"], first["student"]["id"])
    assert first["reason"] == "no_billing_account"
    assert first["family"] is None
    student_name, subject_name = names(url, orphan)
    assert first["student"] == {"id": str(orphan.student), "name": student_name}
    assert first["subject"] == {"id": str(orphan.subject), "name": subject_name}
    assert first["education_level"]["id"] == str(orphan.level)
    assert first["service_date"] == DAY.isoformat()
    assert first["state"] == "confirmed"
    assert first["scheduled_minutes"] == 60
    assert second["reason"] == "no_price"
    assert second["scheduled_minutes"] == 45
    assert unpriced.family is not None
    assert second["family"] == {
        "id": str(unpriced.family),
        "name": family_name(url, unpriced.family),
    }


def test_no_family_comes_before_no_price(client, staff, module_seeded_url):
    url = module_seeded_url
    both = item(url, starts_at=local(DAY), family=False, price=None)
    set_floor(url, FLOOR)

    [entry] = listed(client, staff)["items"]

    assert (entry["class_session_id"], entry["reason"]) == (
        str(both.session),
        "no_billing_account",
    )


def test_a_student_without_a_level_is_priced_at_any_level(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), level=False, price=None)
    add_price(url, it.subject, [(PRICE_FROM, "100.00")], education_level_id=it.level)
    set_floor(url, FLOOR)

    [entry] = listed(client, staff)["items"]

    assert entry["reason"] == "no_price"
    assert entry["education_level"] is None


def test_filters_by_reason_and_student(client, staff, module_seeded_url):
    url = module_seeded_url
    orphan = item(url, starts_at=local(DAY, 9), family=False)
    unpriced = item(url, starts_at=local(DAY, 10), price=None)
    set_floor(url, FLOOR)

    assert keys(listed(client, staff, reason="no_price")) == [key(unpriced)]
    assert keys(listed(client, staff, reason="no_billing_account")) == [key(orphan)]
    assert keys(listed(client, staff, reason=["no_price", "no_billing_account"])) == [
        key(orphan),
        key(unpriced),
    ]
    assert keys(listed(client, staff, student_id=str(unpriced.student))) == [
        key(unpriced)
    ]
    assert listed(client, staff, student_id=str(uuid.uuid4()))["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"reason": "package_covered"}, id="reason"),
        pytest.param({"student_id": "nope"}, id="student"),
        pytest.param({"limit": 0}, id="limit"),
    ],
)
def test_an_invalid_query_is_422(client, staff, params):
    response = get(client, staff, UNBILLED, **params)

    assert code_of(response) == (422, "validation_error")


def test_order_and_pagination(client, staff, module_seeded_url):
    url = module_seeded_url
    late = item(url, starts_at=local(DAY + timedelta(days=1), 8), family=False)
    early = item(url, starts_at=local(DAY, 15), family=False)

    async def _shared(session: AsyncSession) -> list[Item]:
        # One session, two students named so that "Ana" sorts first.
        subject = await persist_subject(session)
        row = await persist_class_session(
            session,
            subject_id=subject.id,
            starts_at=local(DAY, 12),
            ends_at=local(DAY, 13),
        )
        made = []
        for given in ("Zoe", "ana"):
            student = await persist_student(
                session, contacts=0, given_names=given, family_names="Pérez"
            )
            await add_participant(session, row, student.id)
            made.append(
                Item(
                    row.id,
                    student.id,
                    subject.id,
                    subject.education_level_id,
                    None,
                    None,
                )
            )
        return made

    zoe, ana = in_uow(url, _shared)
    set_floor(url, FLOOR)

    whole = listed(client, staff)
    assert keys(whole) == [key(ana), key(zoe), key(early), key(late)]
    page = listed(client, staff, limit=2, offset=1)
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)
    assert keys(page) == [key(zoe), key(early)]


def test_billing_off_is_an_empty_page(client, staff, module_seeded_url):
    item(module_seeded_url, starts_at=local(DAY), family=False)

    assert listed(client, staff) == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_charged_and_package_covered_items_are_not_listed(
    client, staff, module_seeded_url, monkeypatch
):
    url = module_seeded_url
    charged = item(url, starts_at=local(DAY, 9))
    set_floor(url, FLOOR)
    run(url)
    assert len(charges_of(url, charged.session)) == 1
    # The family is removed after the charge: it stays charged, not listed.
    run_sql(
        url, "DELETE FROM family_students WHERE student_id = :s", {"s": charged.student}
    )
    covered = item(url, starts_at=local(DAY, 10), family=False)

    async def _covered(session: AsyncSession, items: Any) -> set[Any]:
        return {(covered.session, covered.student)}

    monkeypatch.setattr(billing, "package_covered_keys", _covered)

    assert listed(client, staff)["total"] == 0


def test_the_statement_bound(module_seeded_url):
    url = module_seeded_url

    async def _many(session: AsyncSession) -> None:
        subjects = [await persist_subject(session) for _ in range(3)]
        for n in range(30):
            subject = subjects[n % 3]
            starts_at = local(DAY, 6) + timedelta(minutes=30 * n)
            row = await persist_class_session(
                session,
                subject_id=subject.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=30),
            )
            student = await persist_student(
                session, contacts=0, education_level_id=subject.education_level_id
            )
            await add_participant(session, row, student.id)
            family = FamilyFactory.build()
            session.add(family)
            await session.flush()
            await add_to_family(session, family, student)

    in_uow(url, _many)
    set_floor(url, FLOOR)

    count, page = count_statements(
        url,
        lambda s: session_charges.list_unbilled_items(
            s, page=OffsetParams(limit=100, offset=0)
        ),
    )

    assert page.total == 30
    assert {entry.reason for entry in page.items} == {"no_price"}
    assert count <= 6 + 3, count


def test_the_list_matches_what_the_sweeper_skips(client, staff, module_seeded_url):
    url = module_seeded_url
    orphans = [item(url, starts_at=local(DAY, 8 + n), family=False) for n in range(2)]
    unpriced = [item(url, starts_at=local(DAY, 11 + n), price=None) for n in range(3)]
    priced = item(url, starts_at=local(DAY, 15))
    set_floor(url, FLOOR)
    by_reason: dict[str, set[tuple[str, str]]] = {
        "no_billing_account": set(),
        "no_price": set(),
    }
    for entry in listed(client, staff)["items"]:
        by_reason[entry["reason"]].add(
            (entry["class_session_id"], entry["student"]["id"])
        )

    result = run(url)

    assert by_reason == {
        "no_billing_account": {key(it) for it in orphans},
        "no_price": {key(it) for it in unpriced},
    }
    assert (result.no_billing_account, result.no_price, result.posted) == (2, 3, 1)
    assert len(charges_of(url, priced.session)) == 1
    for it in [*orphans, *unpriced]:
        assert charges_of(url, it.session) == []


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (FINANCE_STAFF, True),
        (ACADEMIC_COORDINATOR, False),
        (TUTOR, False),
        (STUDENT, False),
        (GUARDIAN, False),
    ],
)
def test_access(client, module_seeded_url, role, allowed):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    response = get(client, cookie, UNBILLED)

    if allowed:
        assert response.status_code == 200
    else:
        assert code_of(response) == (403, "permission_denied")


def test_anonymous_is_401(client):
    client.cookies.clear()
    assert client.get(UNBILLED).status_code == 401
