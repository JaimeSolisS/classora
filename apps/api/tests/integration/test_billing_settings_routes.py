"""``GET``/``PATCH /api/v1/billing/settings`` (#91 B, F14) through the real
app.

The shapes; ``null``, past and future dates; ``409 version_conflict``;
the ``422``s; the ``update`` audit event with the old and new date (none for
a no-op); the after-commit run of ``billing:post_session_charges``, counted
in the module database's ``procrastinate_jobs`` (no worker runs here): one
for a date, none for ``null``, a ``409`` or a ``422``; and the access
matrix. Each test reads the version it sends.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import jobs
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events

SETTINGS = "/api/v1/billing/settings"
TASK = jobs.SESSION_CHARGES_TASK


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
def no_queued_job(module_seeded_url: str) -> None:
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :task",
        {"task": TASK},
    )


def queued(url: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT queueing_lock, args FROM procrastinate_jobs WHERE task_name = :task",
        {"task": TASK},
    )


def current(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, SETTINGS)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def change(
    client: TestClient, cookie: SetCookie, value: str | None, version: int | None = None
) -> Any:
    if version is None:
        version = current(client, cookie)["version"]
    return patch(
        client, cookie, SETTINGS, {"version": version, "billing_starts_on": value}
    )


def settings_id(url: str) -> uuid.UUID:
    [row] = run_sql(url, "SELECT id FROM billing_settings")
    return uuid.UUID(str(row.id))


def test_get_shows_the_seeded_null(client, manager, module_seeded_url):
    run_sql(module_seeded_url, "UPDATE billing_settings SET billing_starts_on = NULL")

    body = current(client, manager)

    assert set(body) == {"billing_starts_on", "version", "updated_at"}
    assert body["billing_starts_on"] is None
    assert isinstance(body["version"], int)


@pytest.mark.parametrize("value", ["2031-03-01", "2020-01-15", "2099-12-31"])
def test_a_date_is_set_audited_and_queues_one_run(
    client, manager, module_seeded_url, value
):
    url = module_seeded_url
    change(client, manager, None)
    before = current(client, manager)
    audit_before = len(events(url, record_type="billing_settings"))

    response = change(client, manager, value, before["version"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["billing_starts_on"] == value
    assert body["version"] == before["version"] + 1
    assert current(client, manager) == body
    found = events(url, record_type="billing_settings", record_id=settings_id(url))
    assert len(found) == audit_before + 1
    event = found[-1]
    assert event.action == "update"
    assert event.changes == {"billing_starts_on": {"old": None, "new": value}}
    [job] = queued(url)
    assert job.queueing_lock == jobs.SESSION_CHARGES_QUEUEING_LOCK
    assert set(job.args) == {"timestamp"}


def test_null_turns_billing_off_and_queues_nothing(client, manager, module_seeded_url):
    url = module_seeded_url
    change(client, manager, "2031-03-01")
    run_sql(url, "DELETE FROM procrastinate_jobs WHERE task_name = :t", {"t": TASK})

    response = change(client, manager, None)

    assert response.status_code == 200, response.text
    assert response.json()["billing_starts_on"] is None
    assert queued(url) == []
    event = events(url, record_type="billing_settings")[-1]
    assert event.changes == {"billing_starts_on": {"old": "2031-03-01", "new": None}}


def test_the_current_value_is_a_no_op(client, manager, module_seeded_url):
    url = module_seeded_url
    change(client, manager, "2031-04-01")
    before = current(client, manager)
    audit_before = len(events(url, record_type="billing_settings"))

    response = change(client, manager, "2031-04-01", before["version"])

    assert response.status_code == 200
    assert response.json() == before
    assert len(events(url, record_type="billing_settings")) == audit_before


def test_a_stale_version_is_409_and_changes_nothing(client, manager, module_seeded_url):
    url = module_seeded_url
    change(client, manager, "2031-05-01")
    before = current(client, manager)
    run_sql(url, "DELETE FROM procrastinate_jobs WHERE task_name = :t", {"t": TASK})

    response = change(client, manager, "2031-06-01", before["version"] - 1)

    assert code_of(response) == (409, "version_conflict")
    assert current(client, manager) == before
    assert queued(url) == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"billing_starts_on": "2031-03-01"}, id="no-version"),
        pytest.param({"version": 1}, id="no-date-key"),
        pytest.param(
            {"version": 1, "billing_starts_on": "2031-03-01", "extra": 1},
            id="unknown-field",
        ),
        pytest.param({"version": 1, "billing_starts_on": "2031-02-30"}, id="bad-date"),
        pytest.param({"version": 1, "billing_starts_on": "1999-12-31"}, id="too-old"),
    ],
)
def test_an_invalid_body_is_422_and_queues_nothing(
    client, manager, module_seeded_url, body
):
    url = module_seeded_url
    before = current(client, manager)
    body = {**body}
    if "version" in body:
        body["version"] = before["version"]

    response = patch(client, manager, SETTINGS, body)

    assert code_of(response) == (422, "validation_error")
    assert current(client, manager) == before
    assert queued(url) == []


def test_a_failing_deferral_still_answers(
    client, manager, module_seeded_url, monkeypatch
):
    class _Broken:
        def configure_task(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("unreachable")

    original = client.app.state.job_app
    monkeypatch.setattr(client.app.state, "job_app", _Broken())
    try:
        change(client, manager, None)
        response = change(client, manager, "2031-07-01")
    finally:
        client.app.state.job_app = original

    assert response.status_code == 200, response.text
    assert current(client, manager)["billing_starts_on"] == "2031-07-01"


@pytest.mark.parametrize(
    ("role", "can_view", "can_manage"),
    [
        (OWNER, True, True),
        (ADMINISTRATOR, True, True),
        (FINANCE_STAFF, True, True),
        (ACADEMIC_COORDINATOR, False, False),
        (TUTOR, False, False),
        (STUDENT, False, False),
        (GUARDIAN, False, False),
    ],
)
def test_access(client, module_seeded_url, manager, role, can_view, can_manage):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
    version = current(client, manager)["version"]

    read = get(client, cookie, SETTINGS)
    write = patch(
        client, cookie, SETTINGS, {"version": version, "billing_starts_on": None}
    )

    assert read.status_code == (200 if can_view else 403)
    assert write.status_code == (200 if can_manage else 403)
    if not can_view:
        assert code_of(read) == code_of(write) == (403, "permission_denied")


def test_anonymous_is_401(client):
    client.cookies.clear()
    assert client.get(SETTINGS).status_code == 401
    assert client.patch(SETTINGS, json={}).status_code == 401
