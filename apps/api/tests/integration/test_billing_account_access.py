"""Billing account record policy (#364 C2, C3; #90b F1 (a); #86 E4, E8).

Real logins through the real app. ``billing.view`` holders (Owner,
Administrator, Finance Staff) see every family; a guardian only the family
of their **active** guardian profile (``family_guardians``); anyone else
gets a ``404`` identical to an unknown family's (``assert_same_not_found``).
Facts load on every request, so a removed membership applies on the next
one. Billing follows the family, not the ``student_guardians`` links.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.families.models import Family
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    BILLING_VIEW,
    DELEGABLE_PERMISSIONS,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.factories import billing as factories
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    link_guardian,
    persist_student,
    student_login,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow

MISSING = "00000000-0000-4000-8000-000000000364"
SUFFIXES = ("", "/charges", "/ledger")


def account_path(family_id: object, suffix: str = "") -> str:
    return f"/api/v1/families/{family_id}/billing-account{suffix}"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass(frozen=True)
class World:
    """Family A (account, a charge on SA1 posted by ``poster``): students
    SA1, SA2; guardian GA (user ``in_family``, linked to SA1); an inactive
    guardian profile GI (user ``inactive``) in A. Family B (no account):
    student SB1, guardian GB (user ``other_family``). Guardians with no
    family: GN (user ``no_family``) and GL (user ``linked_only``, linked to
    SA2 but not in ``family_guardians``). SA1 has a student login
    (``student``)."""

    a: uuid.UUID
    b: uuid.UUID
    users: dict[str, CreatedUser]


def build_world(url: str) -> World:
    names = (
        "in_family",
        "inactive",
        "other_family",
        "no_family",
        "linked_only",
    )
    users = {name: create_user(url, roles=(GUARDIAN,)) for name in names}
    users["student"] = create_user(url, roles=(STUDENT,))
    users["poster"] = create_user(url, roles=(FINANCE_STAFF,))

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        family_a: Family = await persist(session, FamilyFactory)
        family_b: Family = await persist(session, FamilyFactory)
        sa1 = await persist_student(session, contacts=0)
        sa2 = await persist_student(session, contacts=0)
        sb1 = await persist_student(session, contacts=0)
        await student_login(session, sa1, users["student"].id)

        async def guardian(user: str, *, active: bool = True):  # type: ignore[no-untyped-def]
            return await persist(
                session, GuardianFactory, user_id=users[user].id, is_active=active
            )

        ga = await guardian("in_family")
        gi = await guardian("inactive", active=False)
        gb = await guardian("other_family")
        await guardian("no_family")
        gl = await guardian("linked_only")
        await add_to_family(session, family_a, sa1, sa2, ga, gi)
        await add_to_family(session, family_b, sb1, gb)
        await link_guardian(session, sa1, ga)
        await link_guardian(session, sa2, gl)
        return family_a.id, family_b.id, sa1.id

    a, b, sa1 = in_uow(url, _make)

    async def _charge(session: AsyncSession) -> None:
        await factories.charge(session, sa1, "250.00")

    in_uow(url, _charge, actor_id=users["poster"].id)
    return World(a=a, b=b, users=users)


@pytest.fixture(scope="module")
def world(module_seeded_url: str) -> World:
    return build_world(module_seeded_url)


@pytest.fixture(scope="module")
def staff(module_seeded_url: str) -> dict[str, CreatedUser]:
    return {
        OWNER: create_user(module_seeded_url, roles=(OWNER,)),
        ADMINISTRATOR: create_user(module_seeded_url, roles=(ADMINISTRATOR,)),
        FINANCE_STAFF: create_user(module_seeded_url, roles=(FINANCE_STAFF,)),
        "delegated": create_user(
            module_seeded_url,
            roles=(ADMINISTRATOR,),
            delegations=sorted(DELEGABLE_PERMISSIONS),
        ),
        ACADEMIC_COORDINATOR: create_user(
            module_seeded_url, roles=(ACADEMIC_COORDINATOR,)
        ),
        TUTOR: create_user(module_seeded_url, roles=(TUTOR,)),
    }


def _get(client: TestClient, user: CreatedUser, path: str):  # type: ignore[no-untyped-def]
    return get(client, sign_in(client, user.username), path)


# --- C2: staff -----------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, FINANCE_STAFF, "delegated"])
@pytest.mark.parametrize("suffix", SUFFIXES)
def test_billing_view_holders_see_every_family(client, world, staff, role, suffix):
    cookie = sign_in(client, staff[role].username)
    for family in (world.a, world.b):
        response = get(client, cookie, account_path(family, suffix))
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, TUTOR])
@pytest.mark.parametrize("suffix", SUFFIXES)
def test_other_staff_get_the_unknown_family_404(client, world, staff, role, suffix):
    cookie = sign_in(client, staff[role].username)
    missing = get(client, cookie, account_path(MISSING, suffix))
    for family in (world.a, world.b):
        assert_same_not_found(
            get(client, cookie, account_path(family, suffix)), missing
        )


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_a_student_of_the_family_gets_404(client, world, suffix):
    cookie = sign_in(client, world.users["student"].username)
    assert_same_not_found(
        get(client, cookie, account_path(world.a, suffix)),
        get(client, cookie, account_path(MISSING, suffix)),
    )


def test_billing_view_is_not_delegable():
    assert BILLING_VIEW not in DELEGABLE_PERMISSIONS


# --- C2: guardians -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("user", "family"), [("in_family", "a"), ("other_family", "b")]
)
@pytest.mark.parametrize("suffix", SUFFIXES)
def test_a_guardian_sees_their_own_family(client, world, user, family, suffix):
    """With an account (A) and without one (B)."""
    path = account_path(getattr(world, family), suffix)
    response = _get(client, world.users[user], path)
    assert response.status_code == 200, response.text


HIDDEN = [
    ("in_family", "b"),
    ("other_family", "a"),
    ("no_family", "a"),
    ("no_family", "b"),
    ("inactive", "a"),
    # E4: a link to a child (SA2) without family membership is not billing
    # access.
    ("linked_only", "a"),
]


@pytest.mark.parametrize(("user", "family"), HIDDEN)
@pytest.mark.parametrize("suffix", SUFFIXES)
def test_any_other_family_is_hidden_from_a_guardian(
    client, world, user, family, suffix
):
    cookie = sign_in(client, world.users[user].username)
    assert_same_not_found(
        get(client, cookie, account_path(getattr(world, family), suffix)),
        get(client, cookie, account_path(MISSING, suffix)),
    )


def test_a_removed_guardian_loses_access_on_the_next_request(module_seeded_url, client):
    fresh = build_world(module_seeded_url)
    cookie = sign_in(client, fresh.users["in_family"].username)
    assert get(client, cookie, account_path(fresh.a)).status_code == 200

    run_sql(
        module_seeded_url,
        "DELETE FROM family_guardians WHERE family_id = :id AND guardian_id IN"
        " (SELECT id FROM guardians WHERE user_id = :user)",
        {"id": fresh.a, "user": fresh.users["in_family"].id},
    )

    for suffix in SUFFIXES:
        assert_same_not_found(
            get(client, cookie, account_path(fresh.a, suffix)),
            get(client, cookie, account_path(MISSING, suffix)),
        )


# --- C2: anonymous and the privacy notice -------------------------------------------


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_anonymous_is_401(client, world, suffix):
    client.cookies.clear()
    response = client.get(account_path(world.a, suffix))
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_the_privacy_notice_comes_first(module_seeded_url, client, world, suffix):
    pending = create_user(
        module_seeded_url, roles=(OWNER,), privacy_notice_accepted=False
    )
    response = _get(client, pending, account_path(world.a, suffix))
    assert response.status_code == 403
    assert response.json()["code"] == "privacy_notice_required"


# --- C3: staff-only fields ---------------------------------------------------------


def test_a_guardian_gets_no_actor_ids(client, world, staff):
    path = account_path(world.a, "/charges")
    [as_staff] = _get(client, staff[OWNER], path).json()["items"]
    [as_guardian] = _get(client, world.users["in_family"], path).json()["items"]

    assert as_staff["posted_by_user_id"] == str(world.users["poster"].id)
    assert as_guardian["posted_by_user_id"] is None
    assert {k: v for k, v in as_staff.items() if k != "posted_by_user_id"} == {
        k: v for k, v in as_guardian.items() if k != "posted_by_user_id"
    }
    summary = _get(client, world.users["in_family"], account_path(world.a)).json()
    assert summary == _get(client, staff[OWNER], account_path(world.a)).json()
