"""The demo accounts, one per role plus a second tutor (#195 A, C, E2-E4, E9;
#303 A1, E8; ADR-0007 D12)."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.passwords import needs_rehash, verify_password
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.identity import DEMO_ACCOUNTS, DemoAccountGenerator
from tests.integration.auth_support import (
    auth_client,
    login,
    only_set_cookie,
)
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    ACCOUNT_GENERATORS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    RERUN_LINES,
    UntouchedSession,
    count,
    demo_env,
    migrate,
)

PRIVACY_NOTICE = "/api/v1/privacy-notice"
ACCEPTANCE = "/api/v1/privacy-notice/acceptance"
STUDENTS = "/api/v1/students"

STUDENTS_VISIBLE = {
    "demo.owner": 5,
    "demo.admin": 5,
    "demo.coordinator": 5,
    "demo.finance": 5,
    # #299 G4: S, Sib and A, the students of the tutor's demo sessions.
    "demo.tutor": 3,
    "demo.student": 1,
    "demo.guardian": 2,
    # #303: S and A, the cohort's students; #353: and B, of the finished
    # cohort (its last class is within the 30-day assignment lookback).
    "demo.tutor2": 3,
}
"""C5: the ``GET /api/v1/students`` total of each account after the seed."""


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def account_rows(url: str) -> dict[str, Any]:
    rows = run_sql(
        url,
        "SELECT u.username, u.display_name, u.state, u.password_hash,"
        " array_agg(r.code ORDER BY r.code) AS roles,"
        " p.language, p.theme,"
        " (SELECT count(*) FROM privacy_notice_acceptances a"
        "  WHERE a.user_id = u.id) AS acceptances"
        " FROM users u"
        " LEFT JOIN user_roles ur ON ur.user_id = u.id"
        " LEFT JOIN roles r ON r.id = ur.role_id"
        " LEFT JOIN user_preferences p ON p.user_id = u.id"
        " GROUP BY u.id, p.language, p.theme",
    )
    return {row.username: row for row in rows}


def test_the_accounts_are_the_documented_ones():
    # A1: the values the README table lists, in order.
    assert [
        (a.username, a.password, a.role, a.display_name) for a in DEMO_ACCOUNTS
    ] == [
        ("demo.owner", "classora-demo-owner", "owner", "Demo Owner"),
        ("demo.admin", "classora-demo-admin", "administrator", "Demo Administrator"),
        (
            "demo.coordinator",
            "classora-demo-coordinator",
            "academic_coordinator",
            "Demo Coordinator",
        ),
        ("demo.finance", "classora-demo-finance", "finance_staff", "Demo Finance"),
        ("demo.tutor", "classora-demo-tutor", "tutor", "Demo Tutor"),
        ("demo.student", "classora-demo-student", "student", "Demo Student"),
        ("demo.guardian", "classora-demo-guardian", "guardian", "Demo Guardian"),
        ("demo.tutor2", "classora-demo-tutor2", "tutor", "Demo Second Tutor"),
    ]
    assert all(len(a.password) >= 12 for a in DEMO_ACCOUNTS)


def test_local_creates_one_active_account_per_role(
    migrated_url, env_for, capsys, clock
):
    # The #299 frozen day: the M6-demo-b counts depend on the center's today.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    rows = account_rows(migrated_url)
    assert set(rows) == {a.username for a in DEMO_ACCOUNTS}
    for account in DEMO_ACCOUNTS:
        row = rows[account.username]
        assert (row.display_name, row.state, row.roles) == (
            account.display_name,
            "active",
            [account.role],
        ), account.username
        assert verify_password(row.password_hash, account.password)
        assert not needs_rehash(row.password_hash)
        assert (row.language, row.theme) == ("es", "system")
        assert row.acceptances == 0


def test_a_second_run_creates_no_account(migrated_url, env_for, capsys):
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert count(migrated_url, "users") == 8
    assert count(migrated_url, "user_roles") == 8


def test_dev_creates_no_account(migrated_url, env_for, capsys):
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    assert count(migrated_url, "users") == 0


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("account", DEMO_ACCOUNTS, ids=lambda a: a.username)
def test_an_account_generator_writes_nothing_outside_local(account, app_env):
    generator = DemoAccountGenerator(account, app_env=lambda: app_env)

    result = asyncio.run(generator.generate(UntouchedSession()))  # type: ignore[arg-type]

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


def test_the_generator_names_follow_the_usernames():
    assert ACCOUNT_GENERATORS == [
        "identity." + a.username.replace(".", "_") for a in DEMO_ACCOUNTS
    ]


def accept_the_notice(client: TestClient, cookie: Any) -> None:
    notice = get(client, cookie, PRIVACY_NOTICE)
    assert notice.status_code == 200, notice.text
    accepted = post(client, cookie, ACCEPTANCE, {"version": notice.json()["version"]})
    assert accepted.status_code == 200, accepted.text


def test_every_account_signs_in_and_sees_what_its_role_allows(migrated_url, env_for):
    # C5 / E9: the documented password, the one role, the privacy notice
    # first, then the students its role lets it see.
    env_for("local", migrated_url)
    assert demo.main([]) == 0

    totals: dict[str, int] = {}
    with auth_client(migrated_url) as client:
        for account in DEMO_ACCOUNTS:
            client.cookies.clear()
            response = login(client, account.username, account.password)
            assert response.status_code == 200, (account.username, response.text)
            body = response.json()
            assert body["roles"] == [account.role]
            assert body["privacy_notice_required"] is True
            client.cookies.clear()
            cookie = only_set_cookie(response)

            accept_the_notice(client, cookie)
            students = get(client, cookie, STUDENTS, limit=100)
            assert students.status_code == 200, (account.username, students.text)
            totals[account.username] = students.json()["total"]

    assert totals == STUDENTS_VISIBLE
