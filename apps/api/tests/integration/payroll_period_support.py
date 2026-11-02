"""Helpers of the #106 tests: the settings row, the terms and the periods by
SQL, one job run, and the state a test starts from."""

import asyncio
import uuid
from datetime import date
from typing import Any

from app.workflows import payroll_periods
from app.workflows.payroll_periods import OpenPeriodsRunResult
from tests.integration.conftest import run_sql, settings_for


def d(text: str) -> date:
    """``"11-04"`` is in 2026."""
    return date.fromisoformat(text if len(text) > 5 else f"2026-{text}")


def clear_pay_items(url: str) -> None:
    """No items and no types. The lines are gone already."""
    run_sql(url, "DELETE FROM pay_items")
    run_sql(url, "DELETE FROM pay_item_types")


def reset(url: str) -> None:
    """No lines, terms or periods, payroll off, the seeded anchor and
    basis."""
    lines = "ALTER TABLE payroll_lines {} TRIGGER payroll_lines_no_finalized_change"
    run_sql(url, lines.format("DISABLE"))
    run_sql(url, "DELETE FROM payroll_lines")
    run_sql(url, lines.format("ENABLE"))
    clear_pay_items(url)
    guard = "ALTER TABLE payroll_periods {} TRIGGER payroll_periods_no_finalized_change"
    run_sql(url, guard.format("DISABLE"))
    run_sql(url, "DELETE FROM payroll_periods")
    run_sql(url, guard.format("ENABLE"))
    run_sql(url, "DELETE FROM tutor_pay_terms")
    run_sql(
        url,
        "UPDATE payroll_settings SET payroll_starts_on = NULL,"
        " biweekly_anchor_on = '2026-01-05',"
        " salary_proration_basis = 'calendar_days'",
    )


def set_floor(url: str, floor: str | None) -> None:
    run_sql(
        url,
        "UPDATE payroll_settings SET payroll_starts_on = :floor",
        {"floor": None if floor is None else d(floor)},
    )


def add_terms_row(
    url: str, tutor: uuid.UUID, on: str, frequency: str | None
) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO tutor_pay_terms (tutor_user_id, effective_from, pay_frequency)"
        " VALUES (:u, :on, :frequency) RETURNING id",
        {"u": tutor, "on": d(on), "frequency": frequency},
    )
    return uuid.UUID(str(row.id))


def add_period_row(
    url: str,
    tutor: uuid.UUID,
    starts: str,
    ends: str,
    frequency: str = "weekly",
    *,
    first: bool = True,
) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO payroll_periods (tutor_user_id, starts_on, ends_on,"
        " pay_frequency, is_first_period)"
        " VALUES (:u, :starts, :ends, :frequency, :first) RETURNING id",
        {
            "u": tutor,
            "starts": d(starts),
            "ends": d(ends),
            "frequency": frequency,
            "first": first,
        },
    )
    return uuid.UUID(str(row.id))


def periods_of(url: str, tutor: uuid.UUID) -> list[tuple[str, str, str]]:
    """``(starts_on, ends_on, pay_frequency)``, oldest first."""
    rows = run_sql(
        url,
        "SELECT starts_on, ends_on, pay_frequency FROM payroll_periods"
        " WHERE tutor_user_id = :u ORDER BY starts_on",
        {"u": tutor},
    )
    return [(str(r.starts_on), str(r.ends_on), r.pay_frequency) for r in rows]


def run_job(url: str) -> OpenPeriodsRunResult:
    return asyncio.run(payroll_periods.run_open_periods(settings_for(url)))


def finalize(url: str, period: uuid.UUID, by: uuid.UUID) -> None:
    """Move a period to ``finalized`` by SQL (#110 owns the workflow)."""
    params: dict[str, Any] = {"id": period, "by": by}
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :by WHERE id = :id",
        params,
    )
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'finalized', finalized_at = now(),"
        " finalized_by_user_id = :by, total_amount = 0 WHERE id = :id",
        params,
    )
