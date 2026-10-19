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

#24 F2 adds the CSRF cases (``METHOD path csrf:no-origin`` and
``csrf:no-token``) for every unsafe entry of both matrices, and a self-test
with a planted route that skips the token check.

#26 E3, E4: for an unconditional ``reauth`` entry the allowed actors
reauthenticate before the request (denied actors do not and still get
``permission_denied``), and ``METHOD path reauth:none`` expects ``403
reauth_required``; a self-test plants a route whose reauth check is skipped.
"""

import asyncio
import copy
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from alembic import command
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    ROUTE_MARKER_ATTR,
    AccessRule,
    Principal,
    ResolvedSession,
    get_current_session,
    require,
)
from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.core.reauth import REAUTH_RESPONSES, ReauthRule
from app.main import build_app, lifespan
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    OWNER,
    PAYROLL_FINALIZE,
    ROLE_CODES,
    SCHEDULE_OVERRIDE,
    TUTOR,
)
from app.seeds import runner
from tests.authz.harness import (
    ANONYMOUS,
    Actor,
    Case,
    CsrfCase,
    MatrixUser,
    ReauthCase,
    all_actors,
    csrf_cases,
    load_matrix,
    login_cookie,
    matrix_cases,
    matrix_users_for,
    parse_matrix,
    reauth_cases,
    run_case,
    run_cases,
    run_csrf_case,
    run_csrf_cases,
    run_reauth_case,
    run_reauth_cases,
)
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.test_migrations import alembic_config
from tests.unit.authz_app import SAMPLE_MATRIX, build_authz_app

REAL_CASES = matrix_cases(load_matrix())
SAMPLE_CASES = matrix_cases(parse_matrix(SAMPLE_MATRIX))
REAL_CSRF_CASES = csrf_cases(load_matrix())
SAMPLE_CSRF_CASES = csrf_cases(parse_matrix(SAMPLE_MATRIX))
REAL_REAUTH_CASES = reauth_cases(load_matrix())
SAMPLE_REAUTH_CASES = reauth_cases(parse_matrix(SAMPLE_MATRIX))
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


@pytest.mark.parametrize("case", REAL_CSRF_CASES, ids=[c.id for c in REAL_CSRF_CASES])
def test_real_app_csrf(real_client: TestClient, users, case: CsrfCase) -> None:
    problems = run_csrf_case(real_client, case, users)

    assert not problems, "\n".join(problems)


def test_real_app_csrf_cases_cover_every_unsafe_entry():
    unsafe = [
        e for e in load_matrix() if e.method in {"POST", "PUT", "PATCH", "DELETE"}
    ]

    assert {(c.entry.key, c.check) for c in REAL_CSRF_CASES} == {
        (e.key, check) for e in unsafe for check in ("no-origin", "no-token")
    }
    by_id = {c.id: c for c in REAL_CSRF_CASES}
    logout = by_id["POST /api/v1/auth/logout csrf:no-token"]
    login = by_id["POST /api/v1/auth/login csrf:no-token"]
    assert logout.actor == Actor("owner") and logout.expects_csrf_failed
    assert login.actor == ANONYMOUS and not login.expects_csrf_failed


def test_real_app_reauth(real_client: TestClient, users) -> None:
    """Every unconditional ``reauth`` entry of the real matrix (none until
    #29-#31 add sensitive routes) answers ``reauth_required`` without a
    reauthentication."""
    assert {c.entry.key for c in REAL_REAUTH_CASES} == {
        e.key for e in load_matrix() if e.needs_reauth
    }

    problems = run_reauth_cases(real_client, REAL_REAUTH_CASES, users)

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


@pytest.mark.parametrize(
    "case", SAMPLE_CSRF_CASES, ids=[c.id for c in SAMPLE_CSRF_CASES]
)
def test_sample_app_csrf(sample_client: TestClient, users, case: CsrfCase) -> None:
    problems = run_csrf_case(sample_client, case, users, sign_in="override")

    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "case", SAMPLE_REAUTH_CASES, ids=[c.id for c in SAMPLE_REAUTH_CASES]
)
def test_sample_app_reauth(sample_client: TestClient, users, case: ReauthCase) -> None:
    problems = run_reauth_case(sample_client, case, users, sign_in="override")

    assert not problems, "\n".join(problems)


def test_sample_reauth_cases_reauthenticate_only_allowed_actors():
    finalize = [c for c in SAMPLE_CASES if c.entry.path == FINALIZE]

    assert {c.actor for c in finalize if c.reauthenticates} == {
        Actor(OWNER),
        Actor(ADMINISTRATOR, delegation=PAYROLL_FINALIZE),
    }
    assert [c.id for c in SAMPLE_REAUTH_CASES] == [f"POST {FINALIZE} reauth:none"]
    assert SAMPLE_REAUTH_CASES[0].actor == Actor(OWNER)


def test_a_route_that_skips_the_reauth_check_is_reported(users):
    """A planted route listed with ``reauth`` whose marker checks nothing: its
    ``reauth:none`` case gets 200 instead of ``reauth_required``."""

    async def unchecked_reauth() -> None:
        return None

    setattr(unchecked_reauth, ROUTE_MARKER_ATTR, ReauthRule(PAYROLL_FINALIZE))
    app = build_authz_app()
    router = APIRouter()
    path = "/sample/unchecked-finalize/{period_id}"

    @router.post(
        path,
        responses=REAUTH_RESPONSES,
        dependencies=[require(PAYROLL_FINALIZE), Depends(unchecked_reauth)],
    )
    async def unchecked(period_id: uuid.UUID) -> None:
        return None

    app.include_router(router)
    entries = parse_matrix(
        {
            "route": [
                {
                    "method": "POST",
                    "path": path,
                    "access": "permission",
                    "permission": PAYROLL_FINALIZE,
                    "allow": [OWNER],
                    "reauth": PAYROLL_FINALIZE,
                }
            ]
        }
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        problems = run_reauth_cases(
            client, reauth_cases(entries), users, sign_in="override"
        )
        allowed = run_cases(client, matrix_cases(entries), users, sign_in="override")

    assert problems == [
        f"POST {path} reauth:none: expected 403 reauth_required, got 200"
    ]
    assert allowed == []


def test_a_reauth_entry_without_the_check_on_a_real_route_is_reported(
    sample_client, users
):
    """The sample override route has no reauth marker: listing it with
    ``reauth`` makes its ``reauth:none`` case fail."""
    document = copy.deepcopy(SAMPLE_MATRIX)
    for table in document["route"]:
        if table["path"] == OVERRIDE:
            table["reauth"] = "finance.correction"
    cases = [
        c for c in reauth_cases(parse_matrix(document)) if c.entry.path == OVERRIDE
    ]

    problems = run_reauth_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {OVERRIDE} reauth:none: expected 403 reauth_required, got 200"
    ]


def test_sample_csrf_cases_cover_post_patch_and_delete():
    methods = {c.entry.method for c in SAMPLE_CSRF_CASES}

    assert methods == {"POST", "PATCH", "DELETE"}


def test_a_route_that_skips_the_csrf_token_check_is_reported(users):
    """A planted access dependency marked authenticated() that takes the
    session without going through get_current_principal (so no token check)."""

    async def unchecked_access(
        resolved: Annotated[ResolvedSession, Depends(get_current_session)],
    ) -> Principal:
        return resolved.principal

    setattr(unchecked_access, ROUTE_MARKER_ATTR, AccessRule("authenticated"))
    app = build_authz_app()
    router = APIRouter()

    @router.delete(
        "/sample/unchecked/{item_id}",
        status_code=204,
        responses={**AUTH_RESPONSES, **CSRF_RESPONSES},
        dependencies=[Depends(unchecked_access)],
    )
    async def unchecked(item_id: uuid.UUID) -> None:
        return None

    app.include_router(router)
    path = "/sample/unchecked/{item_id}"
    cases = csrf_cases(
        parse_matrix(
            {"route": [{"method": "DELETE", "path": path, "access": "authenticated"}]}
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        problems = run_csrf_cases(client, cases, users, sign_in="override")

    assert problems == [
        f"DELETE {path} csrf:no-token: expected 403 csrf_failed, got 204"
    ]


def test_sample_cases_exercise_every_outcome():
    actors = {c.actor for c in SAMPLE_CASES}
    expected = {c.expected for c in SAMPLE_CASES}

    assert actors == {
        ANONYMOUS,
        *map(Actor, ROLE_CODES),
        Actor(ADMINISTRATOR, delegation=PAYROLL_FINALIZE),
        Actor(ADMINISTRATOR, delegation=SCHEDULE_OVERRIDE),
    }
    assert expected == {"allowed", "denied", "unauthenticated"}


def sample_matrix_with(path: str, **changes: Any) -> list[Case]:
    document = copy.deepcopy(SAMPLE_MATRIX)
    for table in document["route"]:
        if table["path"] == path:
            table.update(changes)
    return matrix_cases(parse_matrix(document))


FINALIZE = "/sample/payroll/{period_id}/finalize"
OVERRIDE = "/sample/schedule/{period_id}/override"


def test_a_wrong_allow_entry_is_reported(sample_client, users):
    cases = sample_matrix_with(OVERRIDE, allow=["owner", TUTOR])

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {OVERRIDE} as tutor: expected access, got 403 permission_denied"
    ]


def test_a_missing_allow_entry_is_reported(sample_client, users):
    cases = sample_matrix_with(OVERRIDE, allow=[])

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {OVERRIDE} as owner: expected 403 permission_denied, got 200"
    ]


def test_a_reauth_route_allowed_to_a_role_without_the_permission_is_reported(
    sample_client, users
):
    """The tutor is reauthenticated (listed as allowed) yet still denied: the
    permission check comes first."""
    cases = sample_matrix_with(FINALIZE, allow=["owner", TUTOR])

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        f"POST {FINALIZE} as tutor: expected access, got 403 permission_denied"
    ]


def test_a_protected_route_listed_as_public_is_reported(sample_client, users):
    cases = sample_matrix_with("/sample/me", access="public", reason="wrongly public")

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        "GET /sample/me as anonymous: expected access, got 401 authentication_required",
        "PATCH /sample/me as anonymous: expected access,"
        " got 401 authentication_required",
    ]


def test_a_public_route_listed_as_protected_is_reported(sample_client, users):
    cases = sample_matrix_with("/sample/public", access="authenticated", reason=None)

    problems = run_cases(sample_client, cases, users, sign_in="override")

    assert problems == [
        "GET /sample/public as anonymous: expected 401 authentication_required, got 200"
    ]
