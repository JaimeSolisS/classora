"""The runtime permission matrix (M2-03 E4, E5).

One test per matrix entry and actor (``METHOD path as role``): anonymous, each
of the seven roles and, for a delegable permission, an Administrator holding
that delegation. Every actor is a real, active user in a migrated, seeded
Testcontainers database (``harness.matrix_user_for``).

E4 runs the real app (``build_app(include_dev_routes=True)`` with its lifespan
on the test database): each case logs its actor in through
``POST /api/v1/auth/login`` and sends that fresh session's cookie (#23 H2).
E5 runs the same runner against the test-only app of
``tests/unit/authz_app.py`` and its test-local matrix, with the actor's
principal overriding ``get_current_principal`` (that app has no login route),
so every outcome (allowed, 401, 403, delegation) is exercised.
"""

import asyncio
import copy
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.main import build_app, lifespan
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    PAYROLL_FINALIZE,
    ROLE_CODES,
    TUTOR,
)
from app.seeds import runner
from tests.authz.harness import (
    ANONYMOUS,
    Actor,
    Case,
    MatrixUser,
    all_actors,
    load_matrix,
    login_cookie,
    matrix_cases,
    matrix_users_for,
    parse_matrix,
    run_case,
    run_cases,
)
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.test_migrations import alembic_config
from tests.unit.authz_app import SAMPLE_MATRIX, build_authz_app

REAL_CASES = matrix_cases(load_matrix())
SAMPLE_CASES = matrix_cases(parse_matrix(SAMPLE_MATRIX))
ACTORS = all_actors([*REAL_CASES, *SAMPLE_CASES])


@pytest.fixture(scope="module")
def matrix_database_url(database_url: str) -> Iterator[str]:
    """One migrated, reference-seeded database for this module."""
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        command.upgrade(alembic_config(url), "head")

        async def _seed() -> None:
            engine = runner.create_seed_engine(settings_for(url))
            try:
                await runner.run_reference_seeds(engine)
            finally:
                await engine.dispose()

        asyncio.run(_seed())
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


@pytest.fixture(scope="module")
def users(matrix_database_url: str) -> dict[Actor, MatrixUser]:
    """One real, committed, active user per actor (with ``load_principal``)."""

    async def _create() -> dict[Actor, MatrixUser]:
        engine = short_lived_engine(matrix_database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                loaded = await matrix_users_for(session, ACTORS)
                await session.commit()
                return loaded
        finally:
            await engine.dispose()

    return asyncio.run(_create())


@pytest.fixture(scope="module")
def real_client(matrix_database_url: str) -> Iterator[TestClient]:
    app = build_app(lifespan=lifespan, include_dev_routes=True)
    app.dependency_overrides[get_settings] = lambda: settings_for(matrix_database_url)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="module")
def sample_client() -> Iterator[TestClient]:
    with TestClient(build_authz_app(), raise_server_exceptions=False) as client:
        yield client


# --- E4: the real app ---------------------------------------------------------


@pytest.mark.parametrize("case", REAL_CASES, ids=[c.id for c in REAL_CASES])
def test_real_app(real_client: TestClient, users, case: Case) -> None:
    problems = run_case(real_client, case, users)

    assert not problems, "\n".join(problems)


def test_real_app_cases_cover_every_entry_and_actor():
    entries = load_matrix()

    assert len(REAL_CASES) == len(entries) * (1 + len(ROLE_CODES))
    assert {c.actor for c in REAL_CASES} == {ANONYMOUS, *map(Actor, ROLE_CODES)}


def test_principals_come_from_real_users(users):
    principals = {actor: user.principal for actor, user in users.items()}
    for actor, principal in principals.items():
        assert principal.roles == {actor.role}
    delegated = principals[Actor(ADMINISTRATOR, delegation=PAYROLL_FINALIZE)]
    assert PAYROLL_FINALIZE in delegated.permissions
    assert PAYROLL_FINALIZE not in principals[Actor(ADMINISTRATOR)].permissions
    assert len({p.user_id for p in principals.values()}) == len(principals)


def test_real_app_cases_log_in_for_real(real_client: TestClient, users):
    """Each case gets a fresh session: the same actor's cookies differ, and a
    logout in one case leaves the next case signed in."""
    me = next(c for c in REAL_CASES if c.entry.path == "/api/v1/auth/me")
    logout = next(c for c in REAL_CASES if c.entry.path == "/api/v1/auth/logout")
    owner = Actor("owner")
    first = login_cookie(real_client, users[owner].username)
    second = login_cookie(real_client, users[owner].username)

    assert first != second
    assert run_case(real_client, Case(logout.entry, owner), users) == []
    assert run_case(real_client, Case(me.entry, owner), users) == []
    response = real_client.get("/api/v1/auth/me", headers={"Cookie": first})
    assert response.status_code == 200
    assert response.json()["username"] == users[owner].username


# --- E5: the runner on the sample app ------------------------------------------


@pytest.mark.parametrize("case", SAMPLE_CASES, ids=[c.id for c in SAMPLE_CASES])
def test_sample_app(sample_client: TestClient, users, case: Case) -> None:
    problems = run_case(sample_client, case, users, sign_in="override")

    assert not problems, "\n".join(problems)


def test_sample_cases_exercise_every_outcome():
    actors = {c.actor for c in SAMPLE_CASES}
    expected = {c.expected for c in SAMPLE_CASES}

    assert actors == {
        ANONYMOUS,
        *map(Actor, ROLE_CODES),
        Actor(ADMINISTRATOR, delegation=PAYROLL_FINALIZE),
    }
    assert expected == {"allowed", "denied", "unauthenticated"}


def sample_matrix_with(path: str, **changes: Any) -> list[Case]:
    document = copy.deepcopy(SAMPLE_MATRIX)
    for table in document["route"]:
        if table["path"] == path:
            table.update(changes)
    return matrix_cases(parse_matrix(document))


FINALIZE = "/sample/payroll/{period_id}/finalize"


def test_a_wrong_allow_entry_is_reported(sample_client, users):
    cases = sample_matrix_with(FINALIZE, allow=["owner", TUTOR])

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {FINALIZE} as tutor: expected access, got 403 permission_denied"
    ]


def test_a_missing_allow_entry_is_reported(sample_client, users):
    cases = sample_matrix_with(FINALIZE, allow=[])

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {FINALIZE} as owner: expected 403 permission_denied, got 200"
    ]


def test_a_protected_route_listed_as_public_is_reported(sample_client, users):
    cases = sample_matrix_with("/sample/me", access="public", reason="wrongly public")

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        "GET /sample/me as anonymous: expected access, got 401 authentication_required"
    ]


def test_a_public_route_listed_as_protected_is_reported(sample_client, users):
    cases = sample_matrix_with("/sample/public", access="authenticated", reason=None)

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        "GET /sample/public as anonymous: expected 401 authentication_required, got 200"
    ]
