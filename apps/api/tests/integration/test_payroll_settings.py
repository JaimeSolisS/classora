"""``GET``/``PATCH /api/v1/payroll/settings`` (#106 B, E6, E7, H3) through the
real app: the shapes, the check order, the locks once periods exist, the
audit event, who may call them, and a ``PATCH`` racing the job."""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import jobs
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    STUDENT,
    TUTOR,
)
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import PayrollStartLockedError
from app.modules.payroll.schemas import PayrollSettingsUpdate
from app.modules.payroll.settings import update_payroll_settings
from tests.integration.auth_support import (
    ORIGIN,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
)
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.payroll_period_support import (
    add_period_row,
    add_terms_row,
    d,
    periods_of,
    reset,
    set_floor,
)

SETTINGS = "/api/v1/payroll/settings"
ANCHOR = "2026-01-05"
HOLD_SECONDS = 0.4


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def manager(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> None:
    reset(module_seeded_url)


@pytest.fixture
def tutor(module_seeded_url: str) -> uuid.UUID:
    return create_user(module_seeded_url, roles=(TUTOR,)).id


def current(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, SETTINGS)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def change(client: TestClient, cookie: SetCookie, **overrides: Any) -> Any:
    now = current(client, cookie)
    body = {
        "version": now["version"],
        "payroll_starts_on": now["payroll_starts_on"],
        "biweekly_anchor_on": now["biweekly_anchor_on"],
        "salary_proration_basis": now["salary_proration_basis"],
        **overrides,
    }
    return patch(client, cookie, SETTINGS, body)


def field_codes(response: Any) -> list[str]:
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    return [error["code"] for error in response.json()["errors"]]


def test_get_shows_the_seeded_row(client, manager):
    body = current(client, manager)

    assert set(body) == {
        "payroll_starts_on",
        "biweekly_anchor_on",
        "salary_proration_basis",
        "start_locked",
        "anchor_locked",
        "version",
        "updated_at",
    }
    assert body["payroll_starts_on"] is None
    assert body["biweekly_anchor_on"] == ANCHOR
    assert body["salary_proration_basis"] == "calendar_days"
    assert body["start_locked"] is body["anchor_locked"] is False


@pytest.mark.parametrize("value", ["2026-11-04", "2020-01-15", "2099-12-31"])
def test_every_field_is_set_audited_and_queues_no_job(
    client, manager, module_seeded_url, value
):
    url = module_seeded_url
    before = current(client, manager)
    audited = len(events(url, record_type="payroll_settings"))

    response = change(
        client,
        manager,
        payroll_starts_on=value,
        biweekly_anchor_on="2026-01-12",
        salary_proration_basis="weekdays",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["payroll_starts_on"] == value
    assert body["biweekly_anchor_on"] == "2026-01-12"
    assert body["salary_proration_basis"] == "weekdays"
    assert body["version"] == before["version"] + 1
    assert current(client, manager) == body
    found = events(url, record_type="payroll_settings")
    assert len(found) == audited + 1
    assert found[-1].action == "update"
    assert found[-1].changes == {
        "payroll_starts_on": {"old": None, "new": value},
        "biweekly_anchor_on": {"old": ANCHOR, "new": "2026-01-12"},
        "salary_proration_basis": {"old": "calendar_days", "new": "weekdays"},
    }
    assert not run_sql(
        url,
        "SELECT 1 FROM procrastinate_jobs WHERE task_name = :task",
        {"task": jobs.PAYROLL_OPEN_PERIODS_TASK},
    )


def test_the_floor_clears_to_null_while_no_period_exists(client, manager):
    set_response = change(client, manager, payroll_starts_on="2026-11-04")
    assert set_response.status_code == 200

    response = change(client, manager, payroll_starts_on=None)

    assert response.status_code == 200
    assert response.json()["payroll_starts_on"] is None


def test_unchanged_writes_nothing(client, manager, module_seeded_url):
    url = module_seeded_url
    before = current(client, manager)
    audited = len(events(url, record_type="payroll_settings"))

    response = change(client, manager)

    assert response.status_code == 200
    assert response.json() == before
    assert len(events(url, record_type="payroll_settings")) == audited


def test_a_stale_version_is_409(client, manager):
    stale = current(client, manager)["version"] - 1

    response = change(client, manager, version=stale, payroll_starts_on="2026-11-04")

    assert code_of(response) == (409, "version_conflict")
    assert current(client, manager)["payroll_starts_on"] is None


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"biweekly_anchor_on": "2026-01-06"}, "anchor_not_monday"),
        ({"biweekly_anchor_on": "1999-12-27"}, "date_out_of_range"),
        ({"payroll_starts_on": "1999-12-31"}, "date_out_of_range"),
        ({"payroll_starts_on": "2100-01-01"}, "date_out_of_range"),
        ({"salary_proration_basis": "hours"}, "literal_error"),
        ({"surprise": 1}, "extra_forbidden"),
    ],
)
def test_the_refused_bodies(client, manager, overrides, code):
    assert code in field_codes(change(client, manager, **overrides))


def test_every_key_is_required(client, manager):
    response = patch(client, manager, SETTINGS, {"version": 1})

    assert len(field_codes(response)) == 3


# --- E6: the locks -------------------------------------------------------------------


def test_any_period_locks_the_floor_but_not_the_anchor(
    client, manager, tutor, module_seeded_url
):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08")

    body = current(client, manager)

    assert (body["start_locked"], body["anchor_locked"]) == (True, False)
    for value in ("2026-11-01", "2026-11-03", None):
        response = change(client, manager, payroll_starts_on=value)
        assert code_of(response) == (409, "payroll_start_locked")
    assert current(client, manager) == body
    # E6: only weekly periods exist, so the anchor moves.
    moved = change(client, manager, biweekly_anchor_on="2026-01-12")
    assert moved.status_code == 200, moved.text
    assert moved.json()["start_locked"] is True


def test_a_biweekly_period_locks_the_anchor(client, manager, tutor, module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08", "biweekly")

    assert current(client, manager)["anchor_locked"] is True
    assert code_of(change(client, manager, biweekly_anchor_on="2026-01-12")) == (
        409,
        "biweekly_anchor_locked",
    )


def test_the_basis_stays_editable(client, manager, tutor, module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08", "biweekly")

    response = change(client, manager, salary_proration_basis="weekdays")

    assert response.status_code == 200, response.text
    assert response.json()["salary_proration_basis"] == "weekdays"


def test_a_refusal_changes_nothing_also_for_a_field_sent_alongside(
    client, manager, tutor, module_seeded_url
):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08", "biweekly")
    before = current(client, manager)
    audited = len(events(url, record_type="payroll_settings"))

    start = change(
        client,
        manager,
        payroll_starts_on="2026-11-09",
        salary_proration_basis="weekdays",
    )
    anchor = change(
        client,
        manager,
        biweekly_anchor_on="2026-01-12",
        salary_proration_basis="weekdays",
    )

    assert code_of(start) == (409, "payroll_start_locked")
    assert code_of(anchor) == (409, "biweekly_anchor_locked")
    assert current(client, manager) == before
    assert len(events(url, record_type="payroll_settings")) == audited


def test_the_same_values_while_locked_are_200_and_write_nothing(
    client, manager, tutor, module_seeded_url
):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08", "biweekly")
    before = current(client, manager)

    response = change(client, manager)

    assert response.status_code == 200
    assert response.json() == before


def test_a_stale_version_while_locked_is_a_version_conflict_first(
    client, manager, tutor, module_seeded_url
):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_period_row(url, tutor, "11-02", "11-08")
    stale = current(client, manager)["version"] - 1

    response = change(client, manager, version=stale, payroll_starts_on=None)

    assert code_of(response) == (409, "version_conflict")


# --- B5: the readers -----------------------------------------------------------------


def test_the_readers(module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-02")

    assert in_uow(url, payroll.payroll_starts_on) == d("11-02")
    assert in_uow(url, lambda s: payroll.payroll_starts_on(s, lock=True)) == d("11-02")
    assert in_uow(url, payroll.biweekly_anchor) == d(ANCHOR)
    assert in_uow(url, payroll.salary_proration_basis) == "calendar_days"


# --- G2: a PATCH racing the job ------------------------------------------------------


def _settings_body(url: str, floor: str | None) -> PayrollSettingsUpdate:
    [row] = run_sql(url, "SELECT version FROM payroll_settings")
    return PayrollSettingsUpdate(
        version=row.version,
        payroll_starts_on=None if floor is None else d(floor),
        biweekly_anchor_on=d(ANCHOR),
        salary_proration_basis="calendar_days",
    )


def test_a_patch_waits_for_the_job_and_then_sees_the_lock(tutor, module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_terms_row(url, tutor, "11-02", "weekly")
    body = _settings_body(url, "11-09")

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        opened = asyncio.Event()

        async def job() -> int:
            async with UnitOfWork(sessions) as uow:
                count = await payroll.open_due_periods(
                    uow.session, tutor, today=d("11-03")
                )
                opened.set()
                await asyncio.sleep(HOLD_SECONDS)
                return count

        async def change_floor() -> Any:
            await opened.wait()
            async with UnitOfWork(sessions) as uow:
                return await update_payroll_settings(uow.session, body)

        try:
            return await asyncio.gather(job(), change_floor(), return_exceptions=True)
        finally:
            await engine.dispose()

    count, refused = asyncio.run(_run())

    assert count == 1
    assert isinstance(refused, PayrollStartLockedError)


def test_the_job_waits_for_a_patch_and_then_reads_the_new_floor(
    tutor, module_seeded_url
):
    url = module_seeded_url
    set_floor(url, "11-02")
    add_terms_row(url, tutor, "11-02", "weekly")
    body = _settings_body(url, "11-09")

    async def _run() -> None:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        changed = asyncio.Event()

        async def change_floor() -> None:
            async with UnitOfWork(sessions) as uow:
                await update_payroll_settings(uow.session, body)
                changed.set()
                await asyncio.sleep(HOLD_SECONDS)

        async def job() -> None:
            await changed.wait()
            async with UnitOfWork(sessions) as uow:
                await payroll.open_due_periods(uow.session, tutor, today=d("11-10"))

        try:
            await asyncio.gather(change_floor(), job())
        finally:
            await engine.dispose()

    asyncio.run(_run())

    assert periods_of(url, tutor) == [("2026-11-09", "2026-11-15", "weekly")]


# --- H3: who may call ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "delegations", "allowed"),
    [
        ((OWNER,), (), True),
        ((FINANCE_STAFF,), (), True),
        ((ADMINISTRATOR,), (), False),
        ((ADMINISTRATOR,), (PAYROLL_FINALIZE,), False),
        ((ACADEMIC_COORDINATOR,), (), False),
        ((TUTOR,), (), False),
        ((GUARDIAN,), (), False),
        ((STUDENT,), (), False),
    ],
)
def test_access(client, module_seeded_url, manager, roles, delegations, allowed):
    user = create_user(module_seeded_url, roles=roles, delegations=delegations)
    cookie = sign_in(client, user.username)
    body = {
        "version": current(client, manager)["version"],
        "payroll_starts_on": None,
        "biweekly_anchor_on": ANCHOR,
        "salary_proration_basis": "calendar_days",
    }

    read = get(client, cookie, SETTINGS)
    write = patch(client, cookie, SETTINGS, body)

    if allowed:
        assert (read.status_code, write.status_code) == (200, 200)
    else:
        assert code_of(read) == code_of(write) == (403, "permission_denied")


def test_anonymous_is_401(client):
    client.cookies.clear()

    assert client.get(SETTINGS).status_code == 401
    assert client.patch(SETTINGS, json={}).status_code == 401


def test_a_missing_csrf_token_is_refused(client, manager):
    client.cookies.clear()
    response = client.patch(
        SETTINGS, json={}, headers={**ORIGIN, **cookie_header(manager)}
    )

    assert code_of(response) == (403, "csrf_failed")
