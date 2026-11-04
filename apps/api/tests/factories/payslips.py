"""Payslip snapshots for the #113 tests without a database: one line of each
kind the PDF shows (a session, a covered session, a per-course session, a
late cancellation under a percent rule, a salary piece, a bonus, a deduction
and a ``manual`` adjustment with its reason), and a :class:`StoredPayslip`
around them. Money is text, as stored."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.money import Money
from app.modules.payroll.payslips import (
    PayslipLine,
    StoredPayslip,
    summarize,
)


def line(
    kind: str = "session", amount: str = "100.00", **overrides: Any
) -> dict[str, Any]:
    """A stored line; a session line of 60 minutes at 100 an hour by default."""
    made: dict[str, Any] = {
        "kind": kind,
        "amount": amount,
        "service_date": "2031-03-03",
        "class_kind": "individual",
        "minutes": 60,
        "rate_model": "per_hour",
        "rate": "100.0000",
        "covered_by_salary": False,
        "cumulative_sessions": None,
        "cohort_sessions": None,
        "pay_rule": None,
        "salary": None,
        "pay_item": None,
        "adjustment": None,
    }
    if kind not in {"session", "late_cancellation", "no_show", "makeup"}:
        made.update(
            service_date=None,
            class_kind=None,
            minutes=None,
            rate_model=None,
            rate=None,
        )
    made.update(overrides)
    return made


def every_kind() -> list[dict[str, Any]]:
    """A line of each kind the PDF shows; they total 1,250.00."""
    return [
        line(amount="1000.00", rate="1000.0000", minutes=600),
        line(
            amount="0.00",
            service_date="2031-03-04",
            rate_model=None,
            rate=None,
            covered_by_salary=True,
        ),
        line(
            amount="100.00",
            service_date="2031-03-05",
            class_kind="course",
            rate_model="per_course",
            rate="1000.1250",
            cumulative_sessions=3,
            cohort_sessions=10,
        ),
        line(
            "late_cancellation",
            amount="50.00",
            service_date="2031-03-06",
            pay_rule={
                "event": "late_cancellation",
                "mode": "percent",
                "percent": "50.0000",
                "amount": None,
            },
        ),
        line(
            "salary",
            amount="150.00",
            salary={
                "amount": "600.00",
                "starts_on": "2031-03-01",
                "ends_on": "2031-03-07",
                "numerator": 7,
                "denominator": 28,
                "basis": "calendar_days",
            },
        ),
        line(
            "pay_item",
            amount="200.00",
            pay_item={
                "type_name": "Bono de puntualidad",
                "direction": "bonus",
                "description": "Marzo sin retardos",
            },
        ),
        line(
            "pay_item",
            amount="-300.00",
            pay_item={
                "type_name": "Préstamo",
                "direction": "deduction",
                "description": "Abono al préstamo",
            },
        ),
        line(
            "adjustment",
            amount="50.00",
            adjustment={
                "kind": "manual",
                "reason": "Pago acordado con la tutora",
                "service_date": "2031-02-20",
                "class_kind": "group",
                "origin_period": {"starts_on": "2031-02-16", "ends_on": "2031-02-22"},
                "paired_period": None,
            },
        ),
    ]


def snapshot(
    lines: list[dict[str, Any]] | None = None, **overrides: Any
) -> dict[str, Any]:
    """A valid stored snapshot (``"v": 1``) of these lines, summary and total
    computed from them."""
    lines = every_kind() if lines is None else lines
    parsed = [PayslipLine.model_validate(item) for item in lines]
    summary = summarize(parsed)
    total = Money.total(item.amount for item in parsed)
    made: dict[str, Any] = {
        "v": 1,
        "center": {
            "name": "Centro de Tutorías Aurora",
            "address": "Av. Siempre Viva 742, CDMX",
            "phone": "+52 55 1234 5678",
            "email": "hola@aurora.example",
        },
        "tutor": {"user_id": str(uuid.uuid4()), "display_name": "Tutora Núñez Peña"},
        "period": {
            "id": str(uuid.uuid4()),
            "starts_on": "2031-03-01",
            "ends_on": "2031-03-07",
            "pay_frequency": "weekly",
            "finalized_on": "2031-03-09",
        },
        "issued_on": "2031-03-10",
        "lines": lines,
        "summary": summary.model_dump(mode="json"),
        "total": str(total),
    }
    made.update(overrides)
    return made


def stored(
    lang: str = "es",
    data: dict[str, Any] | None = None,
    *,
    template_version: int = 1,
    number: str = "P-2031-000123",
) -> StoredPayslip:
    return StoredPayslip(
        id=uuid.uuid4(),
        number=number,
        payroll_period_id=uuid.uuid4(),
        issued_at=datetime(2031, 3, 10, 18, 0, tzinfo=UTC),
        language=lang,
        template_version=template_version,
        snapshot=snapshot() if data is None else data,
    )


TOTAL = Decimal("1250.00")
