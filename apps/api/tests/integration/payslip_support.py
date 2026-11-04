"""Helpers of the #113 tests (``test_payroll_payslips*``,
``test_payroll_earnings*``): the three read routes, the committed payslip rows
and the ``P`` series, and a period with every kind of line, finalized through
the routes (Finance prepares, the Owner finalizes, oldest first).

- :func:`earnings`, :func:`earnings_period`, :func:`download`: the routes as a
  signed-in user (anonymous for ``None``).
- :func:`payslip_of`, :func:`payslips_of_tutor`, :func:`series_value`: rows.
- :func:`rich_periods`: a finalized first period with a session line, and a
  second one with a session, a salary piece, a bonus, a deduction and a
  ``manual`` adjustment on the first period's line, finalized too.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import CreatedUser, SetCookie, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    approved_lesson,
    paid_tutor,
)
from tests.integration.payroll_manual_support import manual
from tests.integration.payroll_payment_support import STARTS
from tests.integration.payroll_workflow_support import code, locked
from tests.integration.test_attendance import lesson

TUTORS: Final = "/api/v1/payroll/tutors"
NOT_FOUND: Final = (404, "not_found")
REASON: Final = "Clase extra <acordada> & pagada"
BONUS_TYPE: Final = "Bono de puntualidad"
DEDUCTION_TYPE: Final = "Préstamo"


def earnings_path(tutor: Any) -> str:
    return f"{TUTORS}/{tutor}/earnings"


def period_path(tutor: Any, period: Any) -> str:
    return f"{TUTORS}/{tutor}/earnings/{period}"


def pdf_path(tutor: Any, payslip: Any) -> str:
    return f"{TUTORS}/{tutor}/payslips/{payslip}/pdf"


def get_as(client: TestClient, cookie: SetCookie | None, path: str) -> Response:
    client.cookies.clear()
    if cookie is None:
        return client.get(path)
    return client.get(path, headers=signed_headers(cookie))


def earnings(
    client: TestClient, cookie: SetCookie | None, tutor: Any, query: str = ""
) -> Response:
    return get_as(client, cookie, earnings_path(tutor) + query)


def earnings_period(
    client: TestClient, cookie: SetCookie | None, tutor: Any, period: Any
) -> Response:
    return get_as(client, cookie, period_path(tutor, period))


def download(
    client: TestClient, cookie: SetCookie | None, tutor: Any, payslip: Any
) -> Response:
    return get_as(client, cookie, pdf_path(tutor, payslip))


def payslip_of(url: str, period: Any) -> Any | None:
    rows = run_sql(
        url, "SELECT * FROM payslips WHERE payroll_period_id = :p", {"p": period}
    )
    return rows[0] if rows else None


def payslips_of_tutor(url: str, tutor: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payslips WHERE tutor_user_id = :t ORDER BY number",
        {"t": tutor},
    )


def series_value(url: str, year: int) -> int:
    rows = run_sql(
        url,
        "SELECT last_value FROM number_series WHERE code = 'P' AND year = :y",
        {"y": year},
    )
    return int(rows[0].last_value) if rows else 0


def line_id(url: str, period: Any, kind: str = "session") -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT id FROM payroll_lines WHERE payroll_period_id = :p AND kind = :k",
        {"p": period, "k": kind},
    )
    return uuid.UUID(str(row.id))


@dataclass(frozen=True)
class RichPeriods:
    """Two finalized periods of one tutor on 120 an hour and a weekly salary of
    600.00: ``first`` (one 55-minute lesson and its salary piece) and ``second``
    (a 55-minute lesson 110.00, the salary piece, a bonus 200.00, a deduction
    -50.00 and a ``manual`` adjustment 25.00 on ``first``'s session line)."""

    first: uuid.UUID
    second: uuid.UUID
    bonus_type: uuid.UUID


def rich_periods(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> RichPeriods:
    first = paid_tutor(url, tutor.id, salary="600.00")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    locked(client, url, finance, owner, first)
    approved_lesson(
        client,
        lesson(url, starts_at=STARTS + timedelta(days=7), tutor=tutor),
        owner.user,
    )
    bonus_type = add_pay_type(url, BONUS_TYPE)
    add_item(
        url, tutor.id, bonus_type, "200.00", "11-10", description="Noviembre puntual"
    )
    add_item(
        url,
        tutor.id,
        add_pay_type(url, DEDUCTION_TYPE, "deduction"),
        "50.00",
        "11-11",
        description="Abono al préstamo",
    )
    posted = manual(
        client,
        owner,
        tutor.id,
        "25.00",
        reason=REASON,
        origin_line_id=line_id(url, first),
    )
    assert code(posted) == (201, None), posted.text
    locked(client, url, finance, owner, second)
    return RichPeriods(first=first, second=second, bonus_type=bonus_type)
