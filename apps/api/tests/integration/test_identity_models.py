"""Identity database constraints, with raw INSERTs (M2-02 D2, D5)."""

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.conftest import run_sql_async

pytestmark = pytest.mark.anyio


async def add_user(
    dsn: str, username: str = "ana", state: str = "active", display_name: str = "Ana"
) -> str:
    rows = await run_sql_async(
        dsn,
        "INSERT INTO users (username, display_name, state) "
        "VALUES (:username, :display_name, :state) RETURNING id",
        {"username": username, "display_name": display_name, "state": state},
    )
    return str(rows[0].id)


async def role_id(dsn: str, code: str) -> str:
    rows = await run_sql_async(dsn, "SELECT id FROM roles WHERE code = :c", {"c": code})
    return str(rows[0].id)


async def permission_id(dsn: str, code: str) -> str:
    rows = await run_sql_async(
        dsn, "SELECT id FROM permissions WHERE code = :c", {"c": code}
    )
    return str(rows[0].id)


async def add_user_role(dsn: str, user_id: str, role: str) -> None:
    await run_sql_async(
        dsn,
        "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)",
        {"u": user_id, "r": await role_id(dsn, role)},
    )


async def test_a_valid_user_row_is_accepted(seeded_url):
    user_id = await add_user(seeded_url, "ana.lopez_2-x")

    rows = await run_sql_async(
        seeded_url,
        "SELECT state, version, password_hash FROM users WHERE id = :id",
        {"id": user_id},
    )
    assert tuple(rows[0]) == ("active", 1, None)


async def test_state_defaults_to_pending(seeded_url):
    rows = await run_sql_async(
        seeded_url,
        "INSERT INTO users (username, display_name) VALUES ('ana', 'Ana') "
        "RETURNING state",
    )

    assert rows[0].state == "pending"


async def test_duplicate_username_fails(seeded_url):
    await add_user(seeded_url, "ana")

    with pytest.raises(IntegrityError, match="uq_users_username"):
        await add_user(seeded_url, "ana")


@pytest.mark.parametrize(
    "username", ["Ana", "ab", "ana lopez", "ana@x", "josé", "ana\n", "ANA"]
)
async def test_username_check_rejects_unnormalized_values(seeded_url, username):
    with pytest.raises(IntegrityError, match="ck_users_username_format"):
        await add_user(seeded_url, username)


async def test_username_longer_than_64_fails(seeded_url):
    with pytest.raises(DBAPIError, match="value too long"):
        await add_user(seeded_url, "a" * 65)


@pytest.mark.parametrize("display_name", ["", "   ", "\t"])
async def test_blank_display_name_fails(seeded_url, display_name):
    with pytest.raises(IntegrityError, match="ck_users_display_name_not_blank"):
        await add_user(seeded_url, display_name=display_name)


async def test_unknown_state_fails(seeded_url):
    with pytest.raises(IntegrityError, match="ck_users_state_valid"):
        await add_user(seeded_url, "ana", state="deleted")


async def test_duplicate_user_role_fails(seeded_url):
    user_id = await add_user(seeded_url)
    await add_user_role(seeded_url, user_id, "tutor")

    with pytest.raises(IntegrityError, match="uq_user_roles_user_id"):
        await add_user_role(seeded_url, user_id, "tutor")


async def test_deleting_a_user_with_a_role_fails(seeded_url):
    user_id = await add_user(seeded_url)
    await add_user_role(seeded_url, user_id, "tutor")

    with pytest.raises(IntegrityError, match="fk_user_roles_user_id_users"):
        await run_sql_async(
            seeded_url, "DELETE FROM users WHERE id = :id", {"id": user_id}
        )


async def test_deleting_a_role_in_use_fails(seeded_url):
    with pytest.raises(IntegrityError, match="fk_role_permissions_role_id_roles"):
        await run_sql_async(seeded_url, "DELETE FROM roles WHERE code = 'tutor'")


# --- D2: only delegable permissions can be delegated -------------------------------


async def delegate(dsn: str, user: str, permission: str, granted_by: str) -> None:
    await run_sql_async(
        dsn,
        "INSERT INTO user_delegations (user_id, permission_id, granted_by_user_id) "
        "VALUES (:u, :p, :g)",
        {"u": user, "p": permission, "g": granted_by},
    )


async def test_delegation_of_a_delegable_permission_is_accepted(seeded_url):
    admin = await add_user(seeded_url, "admin")
    owner = await add_user(seeded_url, "owner")

    await delegate(
        seeded_url, admin, await permission_id(seeded_url, "payroll.finalize"), owner
    )

    rows = await run_sql_async(
        seeded_url, "SELECT permission_delegable FROM user_delegations"
    )
    assert [row.permission_delegable for row in rows] == [True]


@pytest.mark.parametrize(
    "code", ["user.delegation.manage", "user.owner_role.assign", "user.view"]
)
async def test_delegation_of_a_non_delegable_permission_fails(seeded_url, code):
    admin = await add_user(seeded_url, "admin")
    owner = await add_user(seeded_url, "owner")
    permission = await permission_id(seeded_url, code)

    with pytest.raises(
        IntegrityError, match="fk_user_delegations_permission_id_permissions"
    ):
        await delegate(seeded_url, admin, permission, owner)

    rows = await run_sql_async(seeded_url, "SELECT count(*) FROM user_delegations")
    assert rows[0][0] == 0


async def test_permission_delegable_column_cannot_be_false(seeded_url):
    admin = await add_user(seeded_url, "admin")

    with pytest.raises(
        IntegrityError, match="ck_user_delegations_permission_delegable"
    ):
        await run_sql_async(
            seeded_url,
            "INSERT INTO user_delegations "
            "(user_id, permission_id, permission_delegable, granted_by_user_id) "
            "VALUES (:u, :p, false, :u)",
            {"u": admin, "p": await permission_id(seeded_url, "user.view")},
        )


async def test_making_a_delegated_permission_non_delegable_fails(seeded_url):
    admin = await add_user(seeded_url, "admin")
    await delegate(
        seeded_url, admin, await permission_id(seeded_url, "payroll.finalize"), admin
    )

    with pytest.raises(IntegrityError, match="fk_user_delegations_permission_id"):
        await run_sql_async(
            seeded_url,
            "UPDATE permissions SET delegable = false WHERE code = 'payroll.finalize'",
        )
