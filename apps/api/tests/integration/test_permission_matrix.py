"""The runtime permission matrix (M2-03 E4, E5).

One test per matrix entry and actor (``METHOD path as role``): anonymous, each
of the seven roles and, for a delegable permission, an Administrator holding
that delegation. Principals come from real users in a migrated, seeded
Testcontainers database (``harness.principal_for``).

E4 runs the real app (``build_app(include_dev_routes=True)`` with its lifespan
on the test database). E5 runs the same runner against the test-only app of
``tests/unit/authz_app.py`` and its test-local matrix, so every outcome
(allowed, 401, 403, delegation) is exercised although the real app has no
protected route yet.
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

from app.core.authz import Principal
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
    all_actors,
    load_matrix,
    matrix_cases,
    parse_matrix,
    principals_for,
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
def principals(matrix_database_url: str) -> dict[Actor, Principal]:
    """One real, committed user per actor, loaded with ``load_principal``."""

    async def _create() -> dict[Actor, Principal]:
        engine = short_lived_engine(matrix_database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                loaded = await principals_for(session, ACTORS)
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
def test_real_app(real_client: TestClient, principals, case: Case) -> None:
    problems = run_case(real_client, case, principals)

    assert not problems, "\n".join(problems)


def test_real_app_cases_cover_every_entry_and_actor():
    entries = load_matrix()

    assert len(REAL_CASES) == len(entries) * (1 + len(ROLE_CODES))
    assert {c.actor for c in REAL_CASES} == {ANONYMOUS, *map(Actor, ROLE_CODES)}


def test_principals_come_from_real_users(principals):
    for actor, principal in principals.items():
        assert principal.roles == {actor.role}
    delegated = principals[Actor(ADMINISTRATOR, delegation=PAYROLL_FINALIZE)]
    assert PAYROLL_FINALIZE in delegated.permissions
    assert PAYROLL_FINALIZE not in principals[Actor(ADMINISTRATOR)].permissions
    assert len({p.user_id for p in principals.values()}) == len(principals)


# --- E5: the runner on the sample app ------------------------------------------


@pytest.mark.parametrize("case", SAMPLE_CASES, ids=[c.id for c in SAMPLE_CASES])
def test_sample_app(sample_client: TestClient, principals, case: Case) -> None:
    problems = run_case(sample_client, case, principals)

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


def test_a_wrong_allow_entry_is_reported(sample_client, principals):
    cases = sample_matrix_with(FINALIZE, allow=["owner", TUTOR])

    problems = run_cases(sample_client, cases, principals)

    assert problems == [
        f"POST {FINALIZE} as tutor: expected access, got 403 permission_denied"
    ]


def test_a_missing_allow_entry_is_reported(sample_client, principals):
    cases = sample_matrix_with(FINALIZE, allow=[])

    problems = run_cases(sample_client, cases, principals)

    assert problems == [
        f"POST {FINALIZE} as owner: expected 403 permission_denied, got 200"
    ]


def test_a_protected_route_listed_as_public_is_reported(sample_client, principals):
    cases = sample_matrix_with("/sample/me", access="public", reason="wrongly public")

    problems = run_cases(sample_client, cases, principals)

    assert problems == [
        "GET /sample/me as anonymous: expected access, got 401 authentication_required"
    ]


def test_a_public_route_listed_as_protected_is_reported(sample_client, principals):
    cases = sample_matrix_with("/sample/public", access="authenticated", reason=None)

    problems = run_cases(sample_client, cases, principals)

    assert problems == [
        "GET /sample/public as anonymous: expected 401 authentication_required, got 200"
    ]
