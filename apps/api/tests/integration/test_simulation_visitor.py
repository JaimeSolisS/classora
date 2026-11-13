"""The DEV visitor step against PostgreSQL (#674 AC2): after an English
simulation (``small`` profile, the real clock frozen at
:data:`~tests.integration.simulation_support.FROZEN_NOW`),
``python -m tests.factories.simulation.visitor`` adds ``visitor`` as an
active Owner with only that role, the current privacy notice accepted and the
published password; a rerun changes nothing; a database without the English
Owner is refused with nothing written."""

import io
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout

import pytest
from sqlalchemy.engine import make_url

from app.modules.identity.passwords import verify_password
from tests.authz.csrf import origin_for
from tests.conftest import frozen_clock
from tests.factories.simulation import visitor as visitor_command
from tests.integration.conftest import run_sql
from tests.integration.simulation_support import (
    FROZEN_NOW,
    migrate,
    run_command,
    simulation_env,
    table_counts,
)


def _fresh(database_url: str) -> Iterator[str]:
    import uuid

    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


def run_visitor(url: str, *, app_env: str = "local") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with (
        simulation_env(url),
        pytest.MonkeyPatch.context() as patch,
        frozen_clock(FROZEN_NOW),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        patch.setenv("APP_ENV", app_env)
        patch.setenv("APP_ORIGIN", origin_for(app_env))
        code = visitor_command.main([])
    return code, out.getvalue().strip(), err.getvalue().strip()


@pytest.fixture(scope="module")
def en_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, _, _ = run_command(url, "en")
        assert code == 0
        yield url


def _visitor(url: str) -> dict:
    rows = run_sql(
        url,
        "SELECT u.id, u.display_name, u.state, u.password_hash,"
        " array(SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        "       WHERE ur.user_id = u.id ORDER BY r.code) AS roles"
        " FROM users u WHERE u.username = 'visitor'",
    )
    assert len(rows) == 1
    user_id, display_name, state, password_hash, roles = rows[0]
    return {
        "id": user_id,
        "display_name": display_name,
        "state": state,
        "password_hash": password_hash,
        "roles": list(roles),
    }


def test_visitor_is_an_active_owner_with_the_published_password(en_url):
    code, out, err = run_visitor(en_url)

    assert (code, err) == (0, "")
    assert out == visitor_command.CREATED
    visitor = _visitor(en_url)
    assert visitor["display_name"] == "Demo visitor"
    assert visitor["state"] == "active"
    assert visitor["roles"] == ["owner"]
    assert verify_password(visitor["password_hash"], "visitor-classora")
    assert not verify_password(visitor["password_hash"], "classora-sim-owner")
    # The current privacy notice is accepted.
    accepted = run_sql(
        en_url,
        "SELECT count(*) FROM privacy_notice_acceptances a"
        " WHERE a.user_id = :uid AND a.notice_version_id = ("
        "   SELECT id FROM privacy_notice_versions WHERE published_at <= :now"
        "   ORDER BY version DESC LIMIT 1)",
        {"uid": visitor["id"], "now": FROZEN_NOW},
    )[0][0]
    assert accepted == 1
    language = run_sql(
        en_url,
        "SELECT language FROM user_preferences WHERE user_id = :uid",
        {"uid": visitor["id"]},
    )[0][0]
    assert language == "en"
    # The simulated Owner keeps its published password (#674 D6).
    owner_hash = run_sql(
        en_url, "SELECT password_hash FROM users WHERE username = 'owner'"
    )[0][0]
    assert verify_password(owner_hash, "classora-sim-owner")


def test_a_rerun_changes_nothing_also_as_dev(en_url):
    # Runs after the test above (module order): the visitor exists.
    before = table_counts(en_url)

    code, out, err = run_visitor(en_url, app_env="dev")

    assert (code, out, err) == (0, visitor_command.UNCHANGED, "")
    assert table_counts(en_url) == before


def test_a_database_without_the_english_owner_is_refused(migrated_url):
    before = table_counts(migrated_url)

    code, out, err = run_visitor(migrated_url)

    assert code == 3
    assert out == ""
    assert err.startswith("visitor: no active simulated Owner 'owner'")
    assert table_counts(migrated_url) == before


@pytest.fixture
def migrated_url(head_url: str) -> str:
    return head_url
