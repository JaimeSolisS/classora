"""Helpers of the #110 tests: the workflow through its real routes, what it left
behind as rows, and the people. A period is prepared and finalized by the route
here; ``payroll_period_support.finalize`` stays for setups that don't test the
workflow (#110 P4)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from tests.conftest import frozen_clock
from tests.integration.auth_support import signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff

ROOT: Final = "/api/v1/payroll/periods"
APPROVALS: Final = "/api/v1/approval-requests"
REQUEST_TYPE: Final = "payroll.period"
OK: Final = (200, None)
STALE: Final = (409, "payroll_period_stale")
UNRESOLVED: Final = (409, "payroll_unresolved_work")
OUT_OF_ORDER: Final = (409, "payroll_period_out_of_order")
NOT_OPEN: Final = (409, "payroll_period_not_open")
NOT_PREPARED: Final = (409, "payroll_period_not_prepared")
EVENTS: Final = (
    "payroll.period_prepared",
    "payroll.period_returned",
    "payroll.period_finalized",
)


def code(response: Response) -> tuple[int, str | None]:
    """The status and the problem code."""
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


def new_key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _post(
    client: TestClient,
    who: Staff,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Response:
    client.cookies.clear()
    return client.post(
        path, json=body, headers={**signed_headers(who.cookie), **(headers or {})}
    )


def prepare(client: TestClient, who: Staff, period: uuid.UUID) -> Response:
    return _post(client, who, f"{ROOT}/{period}/prepare")


def recalculate(client: TestClient, who: Staff, period: uuid.UUID) -> Response:
    return _post(client, who, f"{ROOT}/{period}/recalculate")


def return_period(
    client: TestClient, who: Staff, period: uuid.UUID, reason: str = "Falta una sesión"
) -> Response:
    return _post(client, who, f"{ROOT}/{period}/return", {"reason": reason})


def prepared_at(url: str, period: uuid.UUID) -> str | None:
    [row] = run_sql(
        url, "SELECT prepared_at FROM payroll_periods WHERE id = :p", {"p": period}
    )
    return None if row.prepared_at is None else row.prepared_at.isoformat()


def finalize(
    client: TestClient,
    url: str,
    who: Staff,
    period: uuid.UUID,
    *,
    at: str | None = None,
    key: str | None = None,
) -> Response:
    """``POST …/finalize``, with the row's ``prepared_at`` unless ``at`` is given."""
    return _post(
        client,
        who,
        f"{ROOT}/{period}/finalize",
        {"prepared_at": at or prepared_at(url, period) or "2026-11-30T12:00:00+00:00"},
        {"Idempotency-Key": key or new_key()},
    )


def locked(
    client: TestClient, url: str, preparer: Staff, finalizer: Staff, period: uuid.UUID
) -> None:
    """Through both routes, by two people."""
    assert code(prepare(client, preparer, period)) == OK
    response = finalize(client, url, finalizer, period)
    assert code(response) == OK, response.text


@dataclass(frozen=True)
class Team:
    """A Finance Staff who prepares and an Owner who finalizes, signed in under
    the clock of the call, for tests whose clock moves."""

    finance: Staff
    owner: Staff


def team_at(url: str, client: TestClient, at: datetime) -> Team:
    with frozen_clock(at):
        return Team(
            finance=staff(url, client, (FINANCE_STAFF,), reauth=False),
            owner=staff(url, client, (OWNER,)),
        )


def lock_at(
    client: TestClient, url: str, team: Team, at: datetime, *periods: uuid.UUID
) -> None:
    """Prepare and finalize these periods, in this order, by route."""
    with frozen_clock(at):
        for period in periods:
            locked(client, url, team.finance, team.owner, period)


def read(client: TestClient, who: Staff, period: uuid.UUID) -> Response:
    client.cookies.clear()
    return client.get(f"{ROOT}/{period}/lines", headers=signed_headers(who.cookie))


def status_of(url: str, period: uuid.UUID) -> str:
    [row] = run_sql(
        url, "SELECT status FROM payroll_periods WHERE id = :p", {"p": period}
    )
    return str(row.status)


def row_of(url: str, period: uuid.UUID) -> Any:
    [row] = run_sql(url, "SELECT * FROM payroll_periods WHERE id = :p", {"p": period})
    return row


def events(url: str, period: uuid.UUID) -> list[Any]:
    """The named events of this period, oldest first."""
    return run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_id = :p AND action = ANY(:actions)"
        " ORDER BY occurred_at, id",
        {"p": period, "actions": list(EVENTS)},
    )


def actions(url: str, period: uuid.UUID) -> list[str]:
    return [event.action for event in events(url, period)]


def request_of(url: str, period: uuid.UUID) -> Any | None:
    rows = run_sql(
        url,
        "SELECT * FROM approval_requests WHERE request_type = :t AND subject_id = :p",
        {"t": REQUEST_TYPE, "p": period},
    )
    return rows[0] if rows else None


def notifications_of(url: str, user: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'payroll.period_finalized'",
        {"u": user},
    )


def name_of(url: str, user: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT display_name FROM users WHERE id = :u", {"u": user})
    return str(row.display_name)


def line_ids(url: str, period: uuid.UUID) -> list[uuid.UUID]:
    return [
        row.id
        for row in run_sql(
            url,
            "SELECT id FROM payroll_lines WHERE payroll_period_id = :p ORDER BY id",
            {"p": period},
        )
    ]
