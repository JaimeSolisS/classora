"""Identity reference seeds against PostgreSQL (M2-02 F1-F5)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.identity.permissions import (
    PERMISSIONS,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    TUTOR,
)
from app.seeds import runner
from app.seeds.registry import REFERENCE_SEEDS, UpsertSeed
from app.seeds.role_permissions import RolePermissionsSeed
from app.seeds.upsert import UpsertResult
from tests.authz.csrf import origin_for
from tests.integration.conftest import run_sql_async, settings_for

pytestmark = pytest.mark.anyio

PERMISSIONS_SEED, ROLES_SEED, ROLE_PERMISSIONS_SEED = REFERENCE_SEEDS[:3]


@pytest.fixture
async def engine(head_url: str) -> AsyncIterator[AsyncEngine]:
    seed_engine = runner.create_seed_engine(settings_for(head_url))
    yield seed_engine
    await seed_engine.dispose()


async def mapping(dsn: str) -> dict[str, frozenset[str]]:
    rows = await run_sql_async(
        dsn,
        "SELECT r.code AS role, p.code AS permission FROM role_permissions rp "
        "JOIN roles r ON r.id = rp.role_id "
        "JOIN permissions p ON p.id = rp.permission_id",
    )
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row.role, set()).add(row.permission)
    return {role: frozenset(codes) for role, codes in result.items()}


async def snapshot(dsn: str) -> dict[str, list[Any]]:
    return {
        table: [
            tuple(row)
            for row in await run_sql_async(
                dsn,
                # Table names are test constants, never user input.
                f"SELECT id, updated_at FROM {table} ORDER BY id",  # noqa: S608
            )
        ]
        for table in ("permissions", "roles", "role_permissions")
    }


async def counts(dsn: str) -> tuple[int, int, int]:
    rows = await run_sql_async(
        dsn,
        "SELECT (SELECT count(*) FROM permissions), (SELECT count(*) FROM roles), "
        "(SELECT count(*) FROM role_permissions)",
    )
    return tuple(rows[0])  # type: ignore[return-value]


async def test_first_run_seeds_the_catalog(engine, head_url):
    results = await runner.run_reference_seeds(engine)

    assert results[:3] == [
        ("identity.permissions", UpsertResult(inserted=27, updated=0)),
        ("identity.roles", UpsertResult(inserted=7, updated=0)),
        ("identity.role_permissions", UpsertResult(inserted=66, updated=0)),
    ]
    assert await mapping(head_url) == dict(ROLE_PERMISSIONS)
    delegable = await run_sql_async(
        head_url, "SELECT code, delegable FROM permissions ORDER BY code"
    )
    assert {row.code: row.delegable for row in delegable} == {
        p.code: p.delegable for p in PERMISSIONS
    }
    roles = await run_sql_async(head_url, "SELECT code FROM roles")
    assert {row.code for row in roles} == set(ROLE_CODES)


async def test_second_run_changes_nothing(engine, head_url):
    await runner.run_reference_seeds(engine)
    before = await snapshot(head_url)

    results = await runner.run_reference_seeds(engine)

    assert [result for _, result in results[:3]] == [UpsertResult(0, 0, 0)] * 3
    assert await snapshot(head_url) == before


async def test_changed_delegable_flag_updates_one_row(engine, head_url):
    await runner.run_reference_seeds(engine)
    before = await snapshot(head_url)
    changed = UpsertSeed(
        name="identity.permissions",
        model=PERMISSIONS_SEED.model,  # type: ignore[attr-defined]
        rows=[
            {"code": p.code, "delegable": p.code == "audit.view" or p.delegable}
            for p in PERMISSIONS
        ],
    )

    result = await runner.run_reference_seeds(engine, [changed])

    assert result == [("identity.permissions", UpsertResult(inserted=0, updated=1))]
    rows = await run_sql_async(
        head_url, "SELECT delegable FROM permissions WHERE code = 'audit.view'"
    )
    assert rows[0].delegable is True
    after = await snapshot(head_url)
    assert after["roles"] == before["roles"]
    assert after["role_permissions"] == before["role_permissions"]
    changed_rows = [
        row for row in after["permissions"] if row not in before["permissions"]
    ]
    assert len(changed_rows) == 1


async def test_shrunk_mapping_removes_exactly_that_pair(engine, head_url):
    await runner.run_reference_seeds(engine)
    before = await snapshot(head_url)
    shrunk = dict(ROLE_PERMISSIONS)
    shrunk[TUTOR] = frozenset()  # the tutor loses student.view and tutor.view
    seed = RolePermissionsSeed(name="identity.role_permissions", mapping=shrunk)

    result = await runner.run_reference_seeds(engine, [seed])

    assert result == [
        ("identity.role_permissions", UpsertResult(inserted=0, updated=0, deleted=2))
    ]
    expected = {role: codes for role, codes in shrunk.items() if codes}
    assert await mapping(head_url) == expected
    after = await snapshot(head_url)
    assert len(after["role_permissions"]) == len(before["role_permissions"]) - 2
    assert set(after["role_permissions"]) < set(before["role_permissions"])
    # Roles and permissions are never deleted.
    assert after["roles"] == before["roles"]
    assert after["permissions"] == before["permissions"]

    # Restoring the code mapping re-inserts the pair.
    restored = await runner.run_reference_seeds(engine, [ROLE_PERMISSIONS_SEED])
    assert restored[0][1] == UpsertResult(inserted=2, updated=0, deleted=0)
    assert await mapping(head_url) == dict(ROLE_PERMISSIONS)


async def test_pairs_of_unmapped_roles_are_left_alone(engine, head_url):
    await runner.run_reference_seeds(engine)
    only_tutor = RolePermissionsSeed(name="t", mapping={TUTOR: frozenset()})

    result = await runner.run_reference_seeds(engine, [only_tutor])

    assert result[0][1].deleted == 2
    assert await mapping(head_url) == {
        role: codes for role, codes in ROLE_PERMISSIONS.items() if role != TUTOR
    }


async def test_codes_removed_from_the_catalog_stay_in_their_tables(engine, head_url):
    await runner.run_reference_seeds(engine)
    await run_sql_async(
        head_url,
        "INSERT INTO permissions (code, delegable) VALUES ('retired.code', false)",
    )

    results = await runner.run_reference_seeds(engine)

    assert [result for _, result in results[:3]] == [UpsertResult(0, 0, 0)] * 3
    assert await counts(head_url) == (28, 7, 66)


class FailingRolePermissions(RolePermissionsSeed):
    async def apply(self, session: AsyncSession) -> UpsertResult:
        await super().apply(session)
        raise RuntimeError("seed failed")


async def test_a_failure_in_the_third_seed_rolls_back_all_three(engine, head_url):
    failing = FailingRolePermissions(
        name="identity.role_permissions", mapping=ROLE_PERMISSIONS
    )

    with pytest.raises(RuntimeError, match="seed failed"):
        await runner.run_reference_seeds(
            engine, [PERMISSIONS_SEED, ROLES_SEED, failing]
        )

    assert await counts(head_url) == (0, 0, 0)


async def test_unknown_codes_in_the_mapping_fail_and_roll_back(engine, head_url):
    bad = RolePermissionsSeed(name="bad", mapping={TUTOR: {"no.such_permission"}})

    with pytest.raises(ValueError, match="1 permission"):
        await runner.run_reference_seeds(engine, [PERMISSIONS_SEED, ROLES_SEED, bad])

    assert await counts(head_url) == (0, 0, 0)


async def test_reference_seeds_create_no_users(engine, head_url):
    await runner.run_reference_seeds(engine)

    rows = await run_sql_async(
        head_url,
        "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM user_roles), "
        "(SELECT count(*) FROM user_delegations)",
    )
    assert tuple(rows[0]) == (0, 0, 0)


def test_make_seed_prints_counts_only(head_url, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "prod")  # reference seeds run everywhere
    monkeypatch.setenv("APP_ORIGIN", origin_for("prod"))
    monkeypatch.setenv("DATABASE_URL", head_url)
    monkeypatch.setenv("DATABASE_DIRECT_URL", head_url)

    assert runner.main() == 0
    assert runner.main() == 0

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("identity.")]
    assert lines == [
        "identity.permissions: 27 inserted, 0 updated",
        "identity.roles: 7 inserted, 0 updated",
        "identity.role_permissions: 66 inserted, 0 updated",
        "identity.permissions: 0 inserted, 0 updated",
        "identity.roles: 0 inserted, 0 updated",
        "identity.role_permissions: 0 inserted, 0 updated",
    ]
