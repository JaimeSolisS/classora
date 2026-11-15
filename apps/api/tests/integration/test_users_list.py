"""``GET /api/v1/users`` (#31 D) with real logins.

The module shares one database, so every test scopes its rows with a unique
token in the usernames and display names and searches for it.
"""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.db import create_sessionmaker
from app.core.pagination import OffsetParams
from app.core.uow import UnitOfWork
from app.modules.identity import user_admin
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    GUARDIAN,
    OWNER,
    PENDING,
    ROLE_CODES,
    STUDENT,
    SUSPENDED,
    TUTOR,
    USER_STATES,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.registration_support import register
from tests.integration.user_admin_support import list_users, unique_token

OK = (200, None)


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


def named(url: str, username: str, display_name: str, *roles: str, **kw) -> CreatedUser:
    return create_user(
        url, roles=roles, username=username, display_name=display_name, **kw
    )


def usernames(response) -> list[str]:
    assert code_of(response) == OK, response.text
    return [item["username"] for item in response.json()["items"]]


# --- D2: search ---------------------------------------------------------------------


def test_q_matches_part_of_the_username_or_display_name_in_any_case(
    module_seeded_url, client, cookie
):
    token = unique_token()
    by_username = named(module_seeded_url, f"{token}.ana", "Ana Pérez", TUTOR)
    by_name = named(
        module_seeded_url, f"zz.{token[::-1]}", f"Beto {token.upper()} Ruiz", TUTOR
    )

    assert usernames(list_users(client, cookie, q=token[2:8].upper())) == [
        by_username.username,
        by_name.username,
    ]
    assert usernames(list_users(client, cookie, q=f"  {token}.AN  ")) == [
        by_username.username
    ]
    assert usernames(list_users(client, cookie, q=f"beto {token}")) == [
        by_name.username
    ]


@pytest.mark.parametrize("wildcard", ["%", "_", "\\"])
def test_like_wildcards_match_literally(module_seeded_url, client, cookie, wildcard):
    token = unique_token()
    literal = named(module_seeded_url, f"{token}.lit", f"{token} 50{wildcard} off")
    named(module_seeded_url, f"{token}.other", f"{token} 50x off")

    assert usernames(list_users(client, cookie, q=f"50{wildcard}")) == [
        literal.username
    ]
    assert usernames(list_users(client, cookie, q=f"{token} 50{wildcard}")) == [
        literal.username
    ]


def test_q_alone_as_a_percent_sign_finds_only_names_containing_it(
    module_seeded_url, client, cookie
):
    token = unique_token()
    literal = named(module_seeded_url, f"{token}.pct", f"Cien % {token}")

    found = usernames(list_users(client, cookie, q="%", limit=100))

    assert literal.username in found
    response = list_users(client, cookie, q="%", limit=100)
    assert all("%" in item["display_name"] for item in response.json()["items"])


def test_accents_are_not_folded(module_seeded_url, client, cookie):
    token = unique_token()
    jose = named(module_seeded_url, f"{token}.jose", f"José {token}")

    assert usernames(list_users(client, cookie, q=f"josé {token}")) == [jose.username]
    assert usernames(list_users(client, cookie, q=f"jose {token}")) == []


def test_q_longer_than_100_characters_is_422(client, cookie):
    assert code_of(list_users(client, cookie, q="x" * 101)) == (
        422,
        "validation_error",
    )
    assert code_of(list_users(client, cookie, q="x" * 100)) == OK


def test_a_blank_q_is_no_filter(client, cookie):
    blank = list_users(client, cookie, q="   ")
    everyone = list_users(client, cookie)

    assert blank.json()["total"] == everyone.json()["total"] > 0


# --- D1, D5: filters ----------------------------------------------------------------


@pytest.mark.parametrize("role", ROLE_CODES)
def test_filter_by_each_role(module_seeded_url, client, cookie, role):
    token = unique_token()
    other = next(r for r in ROLE_CODES if r != role)
    holder = named(module_seeded_url, f"{token}.a", f"A {token}", role, other)
    named(module_seeded_url, f"{token}.b", f"B {token}", other)
    named(module_seeded_url, f"{token}.c", f"C {token}")

    response = list_users(client, cookie, q=token, role=role)

    assert usernames(response) == [holder.username]
    [item] = response.json()["items"]
    # Catalog order, whatever the order they were granted in.
    assert item["roles"] == [r for r in ROLE_CODES if r in {role, other}]


@pytest.mark.parametrize("state", USER_STATES)
def test_filter_by_each_state(module_seeded_url, client, cookie, state):
    token = unique_token()
    matching = named(module_seeded_url, f"{token}.a", f"A {token}", TUTOR, state=state)
    for index, other in enumerate(s for s in USER_STATES if s != state):
        named(module_seeded_url, f"{token}.o{index}", f"O {token}", TUTOR, state=other)

    response = list_users(client, cookie, q=token, state=state)

    assert usernames(response) == [matching.username]
    assert response.json()["items"][0]["state"] == state


def test_combined_filters(module_seeded_url, client, cookie):
    token = unique_token()
    hit = named(module_seeded_url, f"{token}.hit", f"Hit {token}", GUARDIAN)
    named(module_seeded_url, f"{token}.s", f"S {token}", GUARDIAN, state=SUSPENDED)
    named(module_seeded_url, f"{token}.t", f"T {token}", TUTOR)
    named(module_seeded_url, "zz.unrelated", "Unrelated Guardian", GUARDIAN)

    response = list_users(client, cookie, q=token, role=GUARDIAN, state=ACTIVE)

    assert usernames(response) == [hit.username]
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    ("param", "value"), [("role", "superuser"), ("state", "deleted"), ("role", "")]
)
def test_unknown_role_or_state_is_422(client, cookie, param, value):
    assert code_of(list_users(client, cookie, **{param: value})) == (
        422,
        "validation_error",
    )


# --- D3: order and item shape -------------------------------------------------------


def test_order_is_display_name_case_insensitive_then_username(
    module_seeded_url, client, cookie
):
    token = unique_token()
    b = named(module_seeded_url, f"{token}.b", f"beta {token}")
    a2 = named(module_seeded_url, f"{token}.z", f"Alpha {token}")
    a1 = named(module_seeded_url, f"{token}.y", f"alpha {token}")
    c = named(module_seeded_url, f"{token}.c", f"Gamma {token}")

    assert usernames(list_users(client, cookie, q=token)) == [
        a1.username,
        a2.username,
        b.username,
        c.username,
    ]


def test_the_item_has_exactly_the_list_fields(module_seeded_url, client, cookie):
    token = unique_token()
    person = named(
        module_seeded_url,
        f"{token}.p",
        f"P {token}",
        STUDENT,
        TUTOR,
        email="p@example.com",
        phone="5512345678",
    )

    [item] = list_users(client, cookie, q=token).json()["items"]

    assert item == {
        "id": str(person.id),
        "username": person.username,
        "display_name": f"P {token}",
        "state": ACTIVE,
        "roles": [TUTOR, STUDENT],
        "created_at": item["created_at"],
    }


# --- D1, D5: pagination -------------------------------------------------------------


def test_limit_and_offset_boundaries_with_total(module_seeded_url, client, cookie):
    token = unique_token()
    people = [
        named(module_seeded_url, f"{token}.{i:02d}", f"{token} {i:02d}")
        for i in range(5)
    ]
    names = [p.username for p in people]

    def page(**params):
        response = list_users(client, cookie, q=token, **params)
        assert code_of(response) == OK
        return response.json()

    first = page(limit=2)
    assert [i["username"] for i in first["items"]] == names[:2]
    assert (first["total"], first["limit"], first["offset"]) == (5, 2, 0)
    assert [i["username"] for i in page(limit=2, offset=4)["items"]] == names[4:]
    assert page(limit=2, offset=5)["items"] == []
    assert page(limit=2, offset=5)["total"] == 5
    assert [i["username"] for i in page(limit=100)["items"]] == names
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert code_of(list_users(client, cookie, **bad)) == (422, "validation_error")


def test_the_default_page_size_is_25(client, cookie):
    assert list_users(client, cookie).json()["limit"] == 25


# --- D5: pending users --------------------------------------------------------------


def test_invitations_and_registration_requests_both_appear(
    module_seeded_url, client, cookie
):
    token = unique_token()
    invited = named(
        module_seeded_url,
        f"{token}.inv",
        f"Invited {token}",
        TUTOR,
        state=PENDING,
        password_hash=None,
    )
    requested = register(client, username=f"{token}.req", display_name=f"Req {token}")
    assert requested.status_code == 202

    response = list_users(client, cookie, q=token, state=PENDING)

    assert usernames(response) == [invited.username, f"{token}.req"]
    assert [i["roles"] for i in response.json()["items"]] == [[TUTOR], []]


def test_archived_users_are_listed(module_seeded_url, client, cookie):
    token = unique_token()
    gone = named(module_seeded_url, f"{token}.gone", f"Gone {token}", state=ARCHIVED)

    assert usernames(list_users(client, cookie, q=token)) == [gone.username]


# --- D4: at most three statements ---------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [{}, {"q": "stmt"}, {"role": TUTOR}, {"q": "stmt", "role": TUTOR, "state": ACTIVE}],
)
def test_at_most_three_statements_whatever_the_page_size(module_seeded_url, filters):
    token = unique_token("stmt")
    for i in range(25):
        named(
            module_seeded_url,
            f"{token}.{i:02d}",
            f"{token} {i:02d}",
            TUTOR,
            ADMINISTRATOR if i % 2 else ACADEMIC_COORDINATOR,
        )
    seen: list[str] = []

    async def _run() -> int:
        engine = short_lived_engine(module_seeded_url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                seen.clear()
                page = await user_admin.list_users(
                    uow.session,
                    q=filters.get("q"),
                    role=filters.get("role"),
                    state=filters.get("state"),
                    page=OffsetParams(limit=25, offset=0),
                )
                count = len(seen)
                assert len(page.items) == 25
                assert any(item.roles for item in page.items)
                return count
        finally:
            await engine.dispose()

    statements = asyncio.run(_run())

    assert statements <= 3, seen


# --- Access -------------------------------------------------------------------------


@pytest.mark.parametrize("role", [ADMINISTRATOR])
def test_an_administrator_lists_users(module_seeded_url, client, role):
    admin = create_user(module_seeded_url, roles=(role,))

    response = list_users(client, sign_in(client, admin.username))

    assert code_of(response) == OK
    assert response.json()["total"] >= 1
