"""Helpers of the #107 tests: terms, rates, rules and periods by SQL, a run in
its own Unit of Work, the lines as rows, and real closeouts."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payroll.errors import PayrollPeriodNotOpenError
from app.modules.payroll.schemas import PayrollRecalculation
from app.workflows import payroll_calculation
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_period_support import add_period_row, d
from tests.integration.series_edit_support import Ctx, as_user
from tests.integration.test_attendance import Lesson, mark
from tests.integration.test_closeout_review import approve, version_of
from tests.integration.test_session_closeout import marked, submit

OK = (200, None)
CREATED = (201, None)
NOT_OPEN = (409, "payroll_period_not_open")
EMPTY_SUMMARY = {
    "topics": None,
    "performance": None,
    "homework": None,
    "next_steps": None,
}
SEED = "2000-01-01"


def add_terms(
    url: str,
    tutor: uuid.UUID,
    on: str = SEED,
    frequency: str | None = "weekly",
    salary: str | None = None,
) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO tutor_pay_terms (tutor_user_id, effective_from, pay_frequency,"
        " salary_amount) VALUES (:u, :on, :frequency, :salary) RETURNING id",
        {
            "u": tutor,
            "on": d(on),
            "frequency": frequency,
            "salary": None if salary is None else Decimal(salary),
        },
    )
    return uuid.UUID(str(row.id))


def add_rate(
    url: str,
    tutor: uuid.UUID,
    on: str = SEED,
    rate: str | None = "120",
    *,
    model: str | None = "per_hour",
    kind: str = "individual",
) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO compensation_rates (tutor_user_id, class_kind, effective_from,"
        " rate_model, rate) VALUES (:u, :kind, :on, :model, :rate) RETURNING id",
        {
            "u": tutor,
            "kind": kind,
            "on": d(on),
            "model": None if rate is None else model,
            "rate": None if rate is None else Decimal(rate),
        },
    )
    return uuid.UUID(str(row.id))


def add_rule(
    url: str,
    event: str,
    on: str,
    mode: str,
    *,
    percent: str | None = None,
    amount: str | None = None,
) -> uuid.UUID:
    """A rule version by SQL, at any date. :func:`clear` removes it."""
    [row] = run_sql(
        url,
        "INSERT INTO session_pay_rules (event, effective_from, mode, percent, amount)"
        " VALUES (:event, :on, :mode, :percent, :amount) RETURNING id",
        {
            "event": event,
            "on": d(on),
            "mode": mode,
            "percent": None if percent is None else Decimal(percent),
            "amount": None if amount is None else Decimal(amount),
        },
    )
    return uuid.UUID(str(row.id))


def clear(url: str) -> None:
    """No lines, no periods, and only the seeded rules. Rates stay, each of
    its own tutor."""
    triggers = (
        ("payroll_lines", "payroll_lines_no_finalized_change"),
        ("payroll_periods", "payroll_periods_no_finalized_change"),
        ("session_pay_rules", "session_pay_rules_no_change"),
    )
    for table, trigger in triggers:
        run_sql(url, f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    run_sql(url, "DELETE FROM payroll_lines")
    run_sql(url, "DELETE FROM payroll_periods")
    run_sql(
        url,
        "DELETE FROM session_pay_rules WHERE effective_from <> :seed",
        {"seed": d(SEED)},
    )
    for table, trigger in triggers:
        run_sql(url, f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")


def paid_tutor(
    url: str,
    tutor: uuid.UUID,
    starts: str = "11-02",
    ends: str = "11-08",
    *,
    rate: str | None = "120",
    model: str = "per_hour",
    salary: str | None = None,
) -> uuid.UUID:
    """Terms and a rate from long ago, and one period. Returns the period."""
    add_terms(url, tutor, salary=salary)
    if rate is not None:
        add_rate(url, tutor, rate=rate, model=model)
    return add_period_row(url, tutor, starts, ends)


def run(url: str, period: uuid.UUID) -> PayrollRecalculation:
    async def _run(session: AsyncSession) -> PayrollRecalculation:
        return await payroll_calculation.recalculate(session, period)

    return in_uow(url, _run)


def refused_status(url: str, period: uuid.UUID) -> str:
    try:
        run(url, period)
    except PayrollPeriodNotOpenError as exc:
        return str((exc.details or {})["status"])
    raise AssertionError("the run was not refused")


def lines(url: str, period: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_lines WHERE payroll_period_id = :p"
        " ORDER BY service_date, class_session_id",
        {"p": period},
    )


def line_of(url: str, period: uuid.UUID) -> Any:
    [line] = lines(url, period)
    return line


def stable(rows: list[Any]) -> list[dict[str, Any]]:
    """Each line, but for ``id``, ``created_at`` and ``updated_at``."""
    moving = {"id", "created_at", "updated_at"}
    return [
        {key: value for key, value in row._mapping.items() if key not in moving}
        for row in rows
    ]


def approved_lesson(
    client: TestClient,
    made: Lesson,
    owner: CreatedUser,
    *,
    statuses: list[str] | None = None,
    minutes: int = 55,
    summary: Any = None,
) -> dict[str, Any]:
    """A real closeout of ``made``: marked, submitted, approved."""
    cookie, version = marked(client, made, statuses)
    empty = statuses is not None and set(statuses) == {"absent"}
    response = submit(
        client,
        cookie,
        made.id,
        version,
        minutes=minutes,
        summary=EMPTY_SUMMARY if empty and summary is None else summary,
    )
    assert code_of(response) == CREATED, response.text
    staff = sign_in(client, owner.username)
    result = approve(client, staff, made.id, version_of(response.json()))
    assert code_of(result) == OK, result.text
    body: dict[str, Any] = result.json()
    return body


def closed_out(
    ctx: Ctx,
    session: dict[str, Any],
    *,
    tutor: CreatedUser | None = None,
    status: str = "present",
    minutes: int = 55,
) -> None:
    """A real closeout of a real session, two hours after its start."""
    when: datetime = datetime.fromisoformat(session["starts_at"]) + timedelta(hours=2)
    made = Lesson(id=session["id"], tutor=tutor or ctx.tutor, students=[ctx.student])
    cookie = as_user(ctx, made.tutor, when)
    with frozen_clock(when):
        marks = mark(
            ctx.client,
            cookie,
            made.id,
            session["version"],
            (ctx.student, status),
        )
        assert code_of(marks) == OK, marks.text
        response = submit(
            ctx.client,
            cookie,
            made.id,
            marks.json()["version"],
            minutes=minutes,
            summary=EMPTY_SUMMARY if status == "absent" else None,
        )
        assert code_of(response) == CREATED, response.text
        staff = sign_in(ctx.client, ctx.owner.username)
        result = approve(ctx.client, staff, made.id, version_of(response.json()))
        assert code_of(result) == OK, result.text


__all__ = [
    "NOT_OPEN",
    "OK",
    "add_period_row",
    "add_rate",
    "add_rule",
    "add_terms",
    "approved_lesson",
    "clear",
    "closed_out",
    "line_of",
    "lines",
    "paid_tutor",
    "refused_status",
    "run",
    "stable",
]
