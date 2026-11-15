"""Guardian profiles: create, edit, read and list (#34 B, C).

Real logins through the real app; the account picker's rule (B2) is checked
through ``identity.service.get_account_reference`` (ADR-0002).
"""

import asyncio
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.pagination import OffsetParams
from app.modules.families import service
from app.modules.families.errors import GuardianAccountInUseError
from app.modules.families.models import Guardian
from app.modules.families.schemas import GuardianCreate
from app.modules.identity import service as identity_service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.factories.identity import persist_user
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.guardian_support import (
    GUARDIANS,
    create_guardian,
    guardian_body,
    guardian_count,
    guardian_event_count,
    guardian_path,
    guardian_row,
    ids,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.student_support import suffix

MISSING = "00000000-0000-4000-8000-000000000999"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


def account(url: str, *roles: str, **overrides: Any) -> CreatedUser:
    return create_user(url, roles=roles or (GUARDIAN,), **overrides)


def field_error(response) -> tuple[int, str, list[Any]]:
    body = response.json()
    [error] = body["errors"]
    return response.status_code, body["code"], error["path"]


def update(client, cookie, guardian: dict[str, Any], body: dict[str, Any]):
    return patch(client, cookie, guardian_path(guardian["id"]), body)


# --- C1: create ---------------------------------------------------------------------


def test_create_with_only_names(module_seeded_url, client, cookie):
    response = post(
        client,
        cookie,
        GUARDIANS,
        {"given_names": "  Laura ", "family_names": " Ñúñez Prueba", "email": ""},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["given_names"] == "Laura"
    assert body["family_names"] == "Ñúñez Prueba"
    assert (body["email"], body["phone"]) == (None, None)
    assert (body["version"], body["is_active"]) == (1, True)
    assert (body["has_account"], body["account"], body["students"]) == (
        False,
        None,
        [],
    )
    row = guardian_row(module_seeded_url, body["id"])
    assert (row.user_id, row.version) == (None, 1)


def test_create_with_every_field_and_an_account(module_seeded_url, client, cookie):
    user = account(module_seeded_url, display_name="Laura Cuenta")

    body = create_guardian(
        client,
        cookie,
        email="laura@example.com",
        phone="+52 00 1234 5678",
        user_id=str(user.id),
    )

    assert body["email"] == "laura@example.com"
    assert body["phone"] == "+52 00 1234 5678"
    assert body["has_account"] is True
    assert body["account"] == {
        "id": str(user.id),
        "username": user.username,
        "display_name": "Laura Cuenta",
        "state": "active",
    }
    # The account's own contact fields are never copied or shown.
    assert set(body["account"]) == {"id", "username", "display_name", "state"}


def test_duplicate_names_are_allowed(client, cookie):
    first = create_guardian(client, cookie, family_names="Igual Igual")
    second = create_guardian(client, cookie, family_names="Igual Igual")

    assert first["id"] != second["id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"given_names": "   "},
        {"family_names": ""},
        {"phone": "12"},
        {"email": "nope"},
        {"user_id": "not-a-uuid"},
        {"account": None},
    ],
    ids=str,
)
def test_invalid_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, overrides
):
    before = (
        guardian_count(module_seeded_url),
        guardian_event_count(module_seeded_url),
    )

    response = post(client, cookie, GUARDIANS, guardian_body(**overrides))

    assert code_of(response) == (422, "validation_error")
    assert (
        guardian_count(module_seeded_url),
        guardian_event_count(module_seeded_url),
    ) == before


@pytest.mark.parametrize("kind", ["unknown", "archived", "tutor", "no_role"])
def test_an_unavailable_account_is_422_and_writes_nothing(
    module_seeded_url, client, cookie, kind
):
    user_id = {
        "unknown": lambda: MISSING,
        "archived": lambda: account(module_seeded_url, state="archived").id,
        "tutor": lambda: account(module_seeded_url, TUTOR).id,
        "no_role": lambda: create_user(module_seeded_url, roles=()).id,
    }[kind]()
    before = (
        guardian_count(module_seeded_url),
        guardian_event_count(module_seeded_url),
    )

    response = post(client, cookie, GUARDIANS, guardian_body(user_id=str(user_id)))

    assert field_error(response) == (
        422,
        "guardian_account_unavailable",
        ["body", "user_id"],
    )
    assert (
        guardian_count(module_seeded_url),
        guardian_event_count(module_seeded_url),
    ) == before


@pytest.mark.parametrize(
    "overrides",
    [{"state": "pending", "password_hash": None}, {"state": "suspended"}],
    ids=["pending-invitation", "suspended"],
)
def test_a_pending_or_suspended_guardian_account_can_be_linked(
    module_seeded_url, client, cookie, overrides
):
    user = account(module_seeded_url, **overrides)

    body = create_guardian(client, cookie, user_id=str(user.id))

    assert body["account"]["state"] == overrides["state"]


def test_a_tutor_who_is_also_a_guardian_can_be_linked(
    module_seeded_url, client, cookie
):
    user = account(module_seeded_url, TUTOR, GUARDIAN)

    assert create_guardian(client, cookie, user_id=str(user.id))["has_account"]


def test_an_account_linked_elsewhere_is_409(module_seeded_url, client, cookie):
    user = account(module_seeded_url)
    create_guardian(client, cookie, user_id=str(user.id))
    before = guardian_count(module_seeded_url)

    response = post(client, cookie, GUARDIANS, guardian_body(user_id=str(user.id)))

    assert code_of(response) == (409, "guardian_account_in_use")
    assert guardian_count(module_seeded_url) == before


def test_concurrent_creates_with_one_account_give_one_201_and_one_409(
    module_seeded_url, owner
):
    user = account(module_seeded_url)
    results: list[int] = []
    start = threading.Barrier(2)

    def send() -> None:
        with auth_client(module_seeded_url) as own_client:
            own_cookie = sign_in(own_client, owner.username)
            start.wait(timeout=10)
            response = post(
                own_client, own_cookie, GUARDIANS, guardian_body(user_id=str(user.id))
            )
            results.append(response.status_code)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [201, 409]
    [row] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM guardians WHERE user_id = :id",
        {"id": user.id},
    )
    assert row.n == 1


@pytest.mark.anyio
async def test_the_unique_violation_of_a_racing_create_is_409(seeded_url):
    # The pre-check of the second create can't see the first's uncommitted
    # row; its insert waits on uq_guardians_user_id and then fails, which
    # maps to the same 409.
    setup = short_lived_engine(seeded_url)
    first_engine = short_lived_engine(seeded_url)
    second_engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(setup)() as db, db.begin():
            owner_user = await persist_user(db, roles=[OWNER])
            user = await persist_user(db, roles=[GUARDIAN])
            caller = principal_for(owner_user.id, OWNER)
            user_id = user.id
        body = GuardianCreate(
            given_names="Ana", family_names="Carrera", user_id=user_id
        )
        async with (
            create_sessionmaker(first_engine)() as first,
            create_sessionmaker(second_engine)() as second,
        ):
            await service.create_guardian(first, principal=caller, body=body)
            waiting = asyncio.create_task(
                service.create_guardian(second, principal=caller, body=body)
            )
            await asyncio.sleep(0.5)
            assert not waiting.done()
            await first.commit()
            with pytest.raises(GuardianAccountInUseError):
                await asyncio.wait_for(waiting, timeout=10)
            await second.rollback()
        async with create_sessionmaker(setup)() as db:
            count = await db.scalar(
                select(func.count(Guardian.id)).where(Guardian.user_id == user_id)
            )
        assert count == 1
    finally:
        for engine in (setup, first_engine, second_engine):
            await engine.dispose()


@pytest.mark.anyio
async def test_another_integrity_error_is_not_swallowed(seeded_url, monkeypatch):
    # An account id no user has, with the account rule skipped: the foreign
    # key fails, which must not pass for guardian_account_in_use.
    from sqlalchemy.exc import IntegrityError

    from app.modules.families import guardians

    async def skip(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guardians, "_ensure_account_available", skip)
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as session:
            body = GuardianCreate(
                given_names="Ana", family_names="Nadie", user_id=uuid.uuid4()
            )
            with pytest.raises(IntegrityError, match="fk_guardians_user_id_users"):
                await service.create_guardian(
                    session, principal=principal_for(uuid.uuid4(), OWNER), body=body
                )
    finally:
        await engine.dispose()


def principal_for(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


# --- C3/C4: edit ----------------------------------------------------------------------


def test_rename_clear_email_and_no_op(module_seeded_url, client, cookie):
    guardian = create_guardian(client, cookie, email="a@example.com")

    renamed = update(
        client, cookie, guardian, {"version": 1, "given_names": "Lucía", "email": None}
    )

    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["given_names"], renamed.json()["email"]) == ("Lucía", None)
    assert renamed.json()["version"] == 2
    events = guardian_event_count(module_seeded_url)
    same = update(client, cookie, guardian, {"version": 2, "given_names": "Lucía"})
    assert same.status_code == 200
    assert same.json()["version"] == 2
    assert guardian_event_count(module_seeded_url) == events


def test_a_stale_version_is_409(module_seeded_url, client, cookie):
    guardian = create_guardian(client, cookie)
    update(client, cookie, guardian, {"version": 1, "phone": "55 1234 5678"})

    response = update(client, cookie, guardian, {"version": 1, "given_names": "Otra"})

    assert code_of(response) == (409, "version_conflict")
    assert guardian_row(module_seeded_url, guardian["id"]).given_names == "Laura"


def test_move_the_account_and_clear_it(module_seeded_url, client, cookie):
    first, second = account(module_seeded_url), account(module_seeded_url)
    guardian = create_guardian(client, cookie, user_id=str(first.id))

    moved = update(client, cookie, guardian, {"version": 1, "user_id": str(second.id)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["account"]["id"] == str(second.id)
    # The first account is free again.
    create_guardian(client, cookie, user_id=str(first.id))

    cleared = update(client, cookie, guardian, {"version": 2, "user_id": None})
    assert cleared.status_code == 200
    assert (cleared.json()["has_account"], cleared.json()["account"]) == (False, None)
    assert guardian_row(module_seeded_url, guardian["id"]).user_id is None


def test_moving_to_an_unavailable_or_used_account_is_refused(
    module_seeded_url, client, cookie
):
    used = account(module_seeded_url)
    create_guardian(client, cookie, user_id=str(used.id))
    guardian = create_guardian(client, cookie)
    tutor = account(module_seeded_url, TUTOR)

    unavailable = update(
        client, cookie, guardian, {"version": 1, "user_id": str(tutor.id)}
    )
    in_use = update(client, cookie, guardian, {"version": 1, "user_id": str(used.id)})

    assert field_error(unavailable) == (
        422,
        "guardian_account_unavailable",
        ["body", "user_id"],
    )
    assert code_of(in_use) == (409, "guardian_account_in_use")
    assert guardian_row(module_seeded_url, guardian["id"]).version == 1


def test_the_version_check_comes_before_the_account_rule(
    module_seeded_url, client, cookie
):
    guardian = create_guardian(client, cookie)
    update(client, cookie, guardian, {"version": 1, "phone": "55 1234 5678"})

    response = update(client, cookie, guardian, {"version": 1, "user_id": MISSING})

    assert code_of(response) == (409, "version_conflict")


def test_a_now_archived_linked_account_is_kept_on_an_unrelated_rename(
    module_seeded_url, client, cookie
):
    user = account(module_seeded_url)
    guardian = create_guardian(client, cookie, user_id=str(user.id))
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": user.id},
    )

    response = update(
        client,
        cookie,
        guardian,
        {"version": 1, "family_names": "Renombrada", "user_id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["account"]["state"] == "archived"


def test_deactivate_and_reactivate(module_seeded_url, client, cookie):
    guardian = create_guardian(client, cookie)

    off = update(client, cookie, guardian, {"version": 1, "is_active": False})
    on = update(client, cookie, guardian, {"version": 2, "is_active": True})

    assert off.json()["is_active"] is False
    assert on.json()["is_active"] is True
    assert guardian_row(module_seeded_url, guardian["id"]).version == 3


@pytest.mark.parametrize("field", ["given_names", "family_names", "is_active"])
def test_null_for_a_required_field_is_422(client, cookie, field):
    guardian = create_guardian(client, cookie)

    response = update(client, cookie, guardian, {"version": 1, field: None})

    assert code_of(response) == (422, "validation_error")


def test_an_unknown_guardian_is_404_before_the_version(client, cookie):
    response = patch(client, cookie, guardian_path(MISSING), {"version": 99})

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_only_family_managers_may_create_or_edit(
    module_seeded_url, client, cookie, role
):
    guardian = create_guardian(client, cookie)
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)

    created = post(client, other, GUARDIANS, guardian_body())
    edited = update(client, other, guardian, {"version": 1, "given_names": "X"})

    assert code_of(created) == (403, "permission_denied")
    assert code_of(edited) == (403, "permission_denied")
    assert guardian_row(module_seeded_url, guardian["id"]).version == 1


def test_an_administrator_may_create_and_edit(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)

    guardian = create_guardian(client, admin_cookie)
    edited = update(
        client, admin_cookie, guardian, {"version": 1, "phone": "55 1234 5678"}
    )

    assert edited.status_code == 200


# --- C5/C6: list and detail -------------------------------------------------------


@pytest.fixture(scope="module")
def dataset(module_seeded_url, client, owner) -> dict[str, dict[str, Any]]:
    """Four guardians that share a unique word, so searches are exact."""
    cookie = sign_in(client, owner.username)
    word = suffix()
    user = account(module_seeded_url)
    made = {
        "A": create_guardian(
            client,
            cookie,
            given_names="Laura",
            family_names=f"Ñúñez {word}",
            user_id=str(user.id),
        ),
        "B": create_guardian(
            client,
            cookie,
            given_names="Mario",
            family_names=f"González {word}",
        ),
        "C": create_guardian(
            client,
            cookie,
            given_names="Ana",
            family_names=f"Álvarez {word}",
        ),
        "D": create_guardian(
            client,
            cookie,
            given_names="Inés",
            family_names=f"Zamora {word}",
        ),
    }
    set_inactive = patch(
        client,
        cookie,
        guardian_path(made["D"]["id"]),
        {"version": 1, "is_active": False},
    )
    assert set_inactive.status_code == 200
    return {"word": {"value": word}, **made}


def named(dataset, *keys: str) -> list[str]:
    return [dataset[k]["id"] for k in keys]


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("nunez", ["A"]),
        ("ÑÚÑEZ laura", ["A"]),
        ("laura nunez", ["A"]),
        ("Gonzales", ["B"]),
        ("alvarez", ["C"]),
    ],
)
def test_search(client, cookie, dataset, q, found):
    # Other tests of the module add guardians too: only the dataset counts.
    mine = set(named(dataset, "A", "B", "C", "D"))

    response = get(client, cookie, GUARDIANS, q=q, status="all", limit=100)

    assert [i for i in ids(response) if i in mine] == named(dataset, *found)


def test_status_filter_order_and_pagination(client, cookie, dataset):
    word = dataset["word"]["value"]

    active = get(client, cookie, GUARDIANS, q=word)
    inactive = get(client, cookie, GUARDIANS, q=word, status="inactive")
    page = get(client, cookie, GUARDIANS, q=word, status="all", limit=2, offset=0)

    assert sorted(ids(active)) == sorted(named(dataset, "A", "B", "C"))
    assert ids(inactive) == named(dataset, "D")
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2


def test_list_items_carry_no_contact_data(client, cookie, dataset):
    word = dataset["word"]["value"]

    [item] = [
        i
        for i in get(client, cookie, GUARDIANS, q=word, limit=100).json()["items"]
        if i["id"] == dataset["A"]["id"]
    ]

    assert item == {
        "id": dataset["A"]["id"],
        "given_names": "Laura",
        "family_names": f"Ñúñez {word}",
        "is_active": True,
        "has_account": True,
        "student_count": 0,
    }


@pytest.mark.parametrize("q", ["", " ", "x" * 101, "a b c d e f"])
def test_invalid_searches_are_422(client, cookie, q):
    assert code_of(get(client, cookie, GUARDIANS, q=q)) == (422, "validation_error")


def test_unknown_status_is_422(client, cookie):
    assert code_of(get(client, cookie, GUARDIANS, status="gone")) == (
        422,
        "validation_error",
    )


def test_the_list_makes_two_statements(module_seeded_url, owner):
    async def thirty(session) -> None:
        for n in range(30):
            session.add(Guardian(given_names="Cuenta", family_names=f"Lista {n:02d}"))
        await session.flush()

    in_uow(module_seeded_url, thirty)
    caller = principal_for(owner.id, OWNER)

    for q in (None, "lista"):
        statements, page = count_statements(
            module_seeded_url,
            lambda session, q=q: service.list_guardians(
                session,
                principal=caller,
                q=q,
                status="all",
                page=OffsetParams(limit=30, offset=0),
            ),
        )
        assert statements == 2, q
        assert len(page.items) == 30


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR])
def test_staff_read_without_family_manage_get_no_account(
    module_seeded_url, client, dataset, role
):
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)

    detail = get(client, other, guardian_path(dataset["A"]["id"]))

    assert detail.status_code == 200
    body = detail.json()
    assert body["has_account"] is True
    expected_account = role == ADMINISTRATOR
    assert (body["account"] is not None) is expected_account


def test_a_missing_guardian_is_404(client, cookie):
    assert code_of(get(client, cookie, guardian_path(MISSING))) == (404, "not_found")


# --- B1: the identity lookup -------------------------------------------------------


def test_account_references_in_one_statement(module_seeded_url):
    tutor_guardian = account(module_seeded_url, TUTOR, GUARDIAN, email="x@example.com")
    roleless = create_user(module_seeded_url, roles=())

    statements, refs = count_statements(
        module_seeded_url,
        lambda session: identity_service.get_account_references(
            session, [tutor_guardian.id, roleless.id, uuid.UUID(MISSING)]
        ),
    )

    assert statements == 1
    assert set(refs) == {tutor_guardian.id, roleless.id}
    assert refs[tutor_guardian.id].roles == frozenset({TUTOR, GUARDIAN})
    assert refs[roleless.id].roles == frozenset()
    assert set(type(refs[roleless.id]).model_fields) == {
        "id",
        "username",
        "display_name",
        "state",
        "roles",
    }
    none_statements, empty = count_statements(
        module_seeded_url,
        lambda session: identity_service.get_account_references(session, []),
    )
    assert (none_statements, empty) == (0, {})
    _, missing = count_statements(
        module_seeded_url,
        lambda session: identity_service.get_account_reference(
            session, uuid.UUID(MISSING)
        ),
    )
    assert missing is None
