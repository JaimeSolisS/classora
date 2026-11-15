"""Payslips (#113; spec §2, §7; ADR-0017 E12, E14; ADR-0006 E13; ADR-0015;
data-model §11.12, P10). The pattern is ``billing/receipts.py`` (#98).

**Issuing** (:func:`issue_payslip`). ``payroll_workflow.finalize`` calls it as
its last step, after the D5 writes (status, event, approval decision,
notification), in the same transaction, for a period with at least one line:
a finalized period with no lines gets no payslip. A finalize replay returns
the stored response and issues nothing; ``payslips.payroll_period_id`` is
UNIQUE as the backstop. The order (B1):

1. the snapshot inputs: the workflow reads the center's settings, the tutor's
   name and language (other modules, ADR-0002) and passes them in
   (:class:`PayslipInputs`); this function reads the lines through the
   projection (:func:`earnings_lines`, one statement);
2. ``issued_at = clock.now()``; the number's year is its year in the center's
   time zone;
3. :func:`app.core.numbering.next_number` for ``P`` and that year: the
   ``number_series`` row lock, the **last lock** (data-model §15), after the
   period and approval-request locks finalize holds, held for the insert
   alone;
4. the ``payslips`` row (an audited ORM insert, ``snapshot`` masked) → flush.

A rollback gives the number back (gap-free). Never commits.

**Language** (B4): the tutor's stored ``user_preferences.language`` at issue,
else ``es`` (``i18n.resolve_language``). It is stored and every download
renders in it, whoever downloads (ADR-0015).

**Snapshot** (B5): :class:`PayslipSnapshotV1` (``"v": 1``), validated on write
and on read; money as strings. The center, the tutor (id, name), the period
(id, dates, frequency, center-local ``finalized_on``), ``issued_on``, one
entry per line through the projection, the summary and the total. No student
or subject names, no ids but the period's and the tutor's (E6), no payments
(they are shown live, ADR-0017 E14) and no actor names. Later changes (a pay
item type rename, a name, the settings, an adjustment correcting a line of
this period, a payment or reversal) never change a row: ``payslips`` is
append-only.

**The projection** (:func:`earnings_lines`, E4, E6) is one statement: the
lines in :data:`~app.modules.payroll.lines.LINE_ORDER`, with the session pay
rule, the pay item and its type, the adjustment, the origin line, and the
origin and paired periods. The tutor's reads use the same projection live, so
the screen and the payslip agree on every figure; only a pay item type
renamed after finalize differs (the screen shows the current name).

**Templates are versioned** (D2; receipts P8). A payslip stores
``template_version`` (:data:`PAYSLIP_TEMPLATE_VERSION` when issued) and is
always rendered with that version's template (:func:`template_for`; an unknown
version is a logged ``500``). Version ``N`` is ``app/templates/payslips/vN/``
plus the catalog keys ``payslips.vN.*``. The rule: a wording or layout change
that would alter what an issued payslip says creates ``v(N+1)`` and leaves
``vN`` intact; only styling fixes may edit ``vN``.

**Rendering** (:func:`render_payslip_document`) is synchronous and CPU-bound
(``app.core.pdf``): the route calls it through ``anyio.to_thread.run_sync``
after its read transaction, from the row alone (:class:`StoredPayslip`).
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core import clock, i18n, pdf
from app.core.money import Money, Rate
from app.core.numbering import next_number
from app.modules.payroll.lines import LINE_ORDER
from app.modules.payroll.models import (
    ADJUSTMENT,
    MANUAL,
    PAY_ITEM,
    SALARY,
    SESSION_LINE_KINDS,
    PayItem,
    PayItemType,
    PayrollAdjustment,
    PayrollLine,
    PayrollPeriod,
    Payslip,
    SessionPayRule,
)
from app.modules.payroll.schemas import (
    AdjustmentKind,
    ClassKind,
    LineKind,
    PayFrequency,
    PayItemDirection,
    PayrollPeriodFacts,
    PayRuleEvent,
    PayRuleMode,
    ProrationBasis,
    RateModel,
)

PAYSLIP_SERIES: Final = "P"
"""The ``number_series`` code of payslips."""

PAYSLIP_TEMPLATE_VERSION: Final = 1
"""The template version new payslips are issued with (D2)."""

PAYSLIP_TEMPLATES: Final[Mapping[int, str]] = {1: "payslips/v1/payslip.html"}
"""Each template version's template, under ``app/templates/``."""

SESSION: Final = "session"


class UnknownPayslipTemplateError(LookupError):
    """A payslip names a template version with no template (a ``500``)."""


class PayslipTotalMismatchError(ValueError):
    """The lines don't add up to the period's stored total (a ``500``)."""


# --- The snapshot (B5) ---------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PayslipPeriodDates(_Frozen):
    """Another period an adjustment names: its dates only."""

    starts_on: date
    ends_on: date


class PayslipPayRule(_Frozen):
    """The rule an event line was priced on."""

    event: PayRuleEvent
    mode: PayRuleMode
    percent: Rate | None
    amount: Money | None


class PayslipSalary(_Frozen):
    """How a salary piece was reached: ``amount`` × ``numerator`` /
    ``denominator`` for ``starts_on`` to ``ends_on``, counted in ``basis``."""

    amount: Money
    starts_on: date
    ends_on: date
    numerator: int
    denominator: int
    basis: ProrationBasis


class PayslipPayItem(_Frozen):
    """A bonus or deduction: the type's name (at issue on the payslip, current
    on the screen), its direction and the item's description."""

    type_name: str
    direction: PayItemDirection
    description: str


class PayslipAdjustment(_Frozen):
    """A correction after lock: its kind, the session's date and class kind it
    corrects, the period it comes from, the other period of a carry pair, and
    the reason on ``manual`` only (E6)."""

    kind: AdjustmentKind
    reason: str | None
    service_date: date | None
    class_kind: ClassKind | None
    origin_period: PayslipPeriodDates | None
    paired_period: PayslipPeriodDates | None


class PayslipLine(_Frozen):
    """One line as the tutor sees it (E4, E6): no ids and no student data. A
    "covered by salary" line is a ``0.00`` ``session`` line with no rate."""

    kind: LineKind
    amount: Money
    service_date: date | None
    class_kind: ClassKind | None
    minutes: int | None
    rate_model: RateModel | None
    rate: Rate | None
    covered_by_salary: bool
    cumulative_sessions: int | None
    cohort_sessions: int | None
    pay_rule: PayslipPayRule | None
    salary: PayslipSalary | None
    pay_item: PayslipPayItem | None
    adjustment: PayslipAdjustment | None


class PayslipSummary(_Frozen):
    """The class lines (:data:`SESSION_LINE_KINDS`) counted, their minutes, and
    the subtotals: ``bonuses`` are the pay items above zero, ``deductions``
    those below (negative)."""

    class_line_count: int
    class_minutes: int
    classes: Money
    salary: Money
    bonuses: Money
    deductions: Money
    adjustments: Money

    def subtotal(self) -> Money:
        """The sum of the five subtotals."""
        return Money.total(
            (self.classes, self.salary, self.bonuses, self.deductions, self.adjustments)
        )


def summarize(lines: Sequence[PayslipLine]) -> PayslipSummary:
    """The summary of these lines (B5)."""
    classes = [line for line in lines if line.kind in SESSION_LINE_KINDS]
    items = [line.amount for line in lines if line.kind == PAY_ITEM]
    return PayslipSummary(
        class_line_count=len(classes),
        class_minutes=sum(line.minutes or 0 for line in classes),
        classes=Money.total(line.amount for line in classes),
        salary=Money.total(line.amount for line in lines if line.kind == SALARY),
        bonuses=Money.total(amount for amount in items if amount.is_positive()),
        deductions=Money.total(amount for amount in items if amount.is_negative()),
        adjustments=Money.total(
            line.amount for line in lines if line.kind == ADJUSTMENT
        ),
    )


class PayslipCenter(_Frozen):
    name: str
    address: str | None
    phone: str | None
    email: str | None


class PayslipTutor(_Frozen):
    user_id: uuid.UUID
    display_name: str


class PayslipPeriod(_Frozen):
    id: uuid.UUID
    starts_on: date
    ends_on: date
    pay_frequency: PayFrequency
    finalized_on: date


class PayslipSnapshotV1(_Frozen):
    """What a payslip shows, frozen at finalize (#113 B5)."""

    v: Literal[1]
    center: PayslipCenter
    tutor: PayslipTutor
    period: PayslipPeriod
    issued_on: date
    lines: tuple[PayslipLine, ...]
    summary: PayslipSummary
    total: Money

    @model_validator(mode="after")
    def _totals_add_up(self) -> PayslipSnapshotV1:
        if Money.total(line.amount for line in self.lines) != self.total:
            raise ValueError("total is not the sum of the lines")
        if self.summary.subtotal() != self.total:
            raise ValueError("total is not the sum of the subtotals")
        if self.summary != summarize(self.lines):
            raise ValueError("the summary doesn't describe the lines")
        return self


def parse_snapshot(data: Mapping[str, Any]) -> PayslipSnapshotV1:
    """A stored snapshot, validated (B5 "on read"); an unknown ``v`` or a
    malformed one is a :class:`pydantic.ValidationError`."""
    return PayslipSnapshotV1.model_validate(data)


def payslip_number(year: int, value: int) -> str:
    """``P-2026-000123``."""
    if not 1 <= value <= 999_999:
        raise ValueError(f"payslip number {value} is out of range")
    return f"{PAYSLIP_SERIES}-{year:04d}-{value:06d}"


# --- The projection (E4, E6) ---------------------------------------------------------


def _dates(starts_on: date | None, ends_on: date | None) -> PayslipPeriodDates | None:
    if starts_on is None or ends_on is None:
        return None
    return PayslipPeriodDates(starts_on=starts_on, ends_on=ends_on)


def _line(row: Any) -> PayslipLine:
    pay_rule = None
    if row.rule_event is not None:
        pay_rule = PayslipPayRule(
            event=row.rule_event,
            mode=row.rule_mode,
            percent=row.rule_percent,
            amount=row.rule_amount,
        )
    salary = None
    if row.kind == SALARY:
        salary = PayslipSalary(
            amount=row.salary_amount,
            starts_on=row.salary_starts_on,
            ends_on=row.salary_ends_on,
            numerator=row.proration_numerator,
            denominator=row.proration_denominator,
            basis=row.proration_basis,
        )
    pay_item = None
    if row.kind == PAY_ITEM:
        pay_item = PayslipPayItem(
            type_name=row.pay_item_type_name,
            direction=row.pay_item_direction,
            description=row.pay_item_description,
        )
    adjustment = None
    if row.kind == ADJUSTMENT:
        adjustment = PayslipAdjustment(
            kind=row.adjustment_kind,
            reason=row.adjustment_reason,
            service_date=row.adjustment_service_date,
            class_kind=row.adjustment_class_kind,
            origin_period=_dates(row.origin_starts_on, row.origin_ends_on),
            paired_period=_dates(row.paired_starts_on, row.paired_ends_on),
        )
    return PayslipLine(
        kind=row.kind,
        amount=row.amount,
        service_date=row.service_date,
        class_kind=row.class_kind,
        minutes=row.minutes,
        rate_model=row.rate_model,
        rate=row.rate,
        covered_by_salary=row.kind == SESSION and row.rate_model is None,
        cumulative_sessions=row.cumulative_sessions,
        cohort_sessions=row.cohort_sessions,
        pay_rule=pay_rule,
        salary=salary,
        pay_item=pay_item,
        adjustment=adjustment,
    )


async def earnings_lines(
    session: AsyncSession, period_id: uuid.UUID
) -> list[PayslipLine]:
    """The period's lines through the projection (module docstring), in
    :data:`LINE_ORDER`. One statement, no lock."""
    origin_line = aliased(PayrollLine)
    origin_period = aliased(PayrollPeriod)
    paired = aliased(PayrollAdjustment)
    paired_period = aliased(PayrollPeriod)
    adjustment = PayrollAdjustment
    rows = await session.execute(
        select(
            PayrollLine.kind,
            PayrollLine.amount,
            PayrollLine.service_date,
            PayrollLine.class_kind,
            PayrollLine.minutes,
            PayrollLine.rate_model,
            PayrollLine.rate,
            PayrollLine.cumulative_sessions,
            PayrollLine.cohort_sessions,
            PayrollLine.salary_amount,
            PayrollLine.salary_starts_on,
            PayrollLine.salary_ends_on,
            PayrollLine.proration_numerator,
            PayrollLine.proration_denominator,
            PayrollLine.proration_basis,
            SessionPayRule.event.label("rule_event"),
            SessionPayRule.mode.label("rule_mode"),
            SessionPayRule.percent.label("rule_percent"),
            SessionPayRule.amount.label("rule_amount"),
            PayItemType.name.label("pay_item_type_name"),
            PayItemType.direction.label("pay_item_direction"),
            PayItem.description.label("pay_item_description"),
            adjustment.kind.label("adjustment_kind"),
            case((adjustment.kind == MANUAL, adjustment.reason), else_=None).label(
                "adjustment_reason"
            ),
            func.coalesce(adjustment.service_date, origin_line.service_date).label(
                "adjustment_service_date"
            ),
            func.coalesce(adjustment.class_kind, origin_line.class_kind).label(
                "adjustment_class_kind"
            ),
            origin_period.starts_on.label("origin_starts_on"),
            origin_period.ends_on.label("origin_ends_on"),
            paired_period.starts_on.label("paired_starts_on"),
            paired_period.ends_on.label("paired_ends_on"),
        )
        .outerjoin(SessionPayRule, SessionPayRule.id == PayrollLine.session_pay_rule_id)
        .outerjoin(PayItem, PayItem.id == PayrollLine.pay_item_id)
        .outerjoin(PayItemType, PayItemType.id == PayItem.pay_item_type_id)
        .outerjoin(adjustment, adjustment.id == PayrollLine.payroll_adjustment_id)
        .outerjoin(origin_line, origin_line.id == adjustment.origin_line_id)
        .outerjoin(
            origin_period,
            origin_period.id
            == func.coalesce(
                adjustment.origin_period_id, origin_line.payroll_period_id
            ),
        )
        .outerjoin(
            paired,
            or_(
                paired.id == adjustment.paired_adjustment_id,
                paired.paired_adjustment_id == adjustment.id,
            ),
        )
        .outerjoin(paired_period, paired_period.id == paired.target_period_id)
        .where(PayrollLine.payroll_period_id == period_id)
        .order_by(*LINE_ORDER)
    )
    return [_line(row) for row in rows]


# --- Issuing (B) ---------------------------------------------------------------------


@dataclass(frozen=True)
class PayslipInputs:
    """What other modules give a payslip (B1 step 1): the center's contact
    block and time zone, the tutor's name and the document language."""

    center: PayslipCenter
    time_zone: str
    tutor_display_name: str
    language: i18n.Language


@dataclass(frozen=True)
class IssuedPayslip:
    """The payslip :func:`issue_payslip` wrote."""

    payslip_id: uuid.UUID
    number: str


async def issue_payslip(
    session: AsyncSession, period: PayrollPeriodFacts, inputs: PayslipInputs
) -> IssuedPayslip:
    """Issue the payslip of this just-finalized period (B1; the module
    docstring order). The caller holds the period ``FOR UPDATE`` and has
    checked it has lines; lines that don't add up to its stored total →
    :class:`PayslipTotalMismatchError`. Five statements and the audit row;
    never commits."""
    if period.finalized_at is None or period.total_amount is None:
        raise ValueError(f"period {period.id} is not finalized")
    lines = await earnings_lines(session, period.id)
    if not lines:
        raise ValueError(f"period {period.id} has no lines")
    total = Money.total(line.amount for line in lines)
    if total != period.total_amount:
        raise PayslipTotalMismatchError(f"period {period.id}")
    zone = ZoneInfo(inputs.time_zone)
    issued_at = clock.now()
    local = issued_at.astimezone(zone)
    snapshot = PayslipSnapshotV1(
        v=1,
        center=inputs.center,
        tutor=PayslipTutor(
            user_id=period.tutor_user_id, display_name=inputs.tutor_display_name
        ),
        period=PayslipPeriod(
            id=period.id,
            starts_on=period.starts_on,
            ends_on=period.ends_on,
            pay_frequency=period.pay_frequency,
            finalized_on=period.finalized_at.astimezone(zone).date(),
        ),
        issued_on=local.date(),
        lines=tuple(lines),
        summary=summarize(lines),
        total=total,
    )
    value = await next_number(session, PAYSLIP_SERIES, local.year)
    payslip = Payslip(
        number=payslip_number(local.year, value),
        payroll_period_id=period.id,
        tutor_user_id=period.tutor_user_id,
        issued_at=issued_at,
        language=inputs.language,
        template_version=PAYSLIP_TEMPLATE_VERSION,
        snapshot=snapshot.model_dump(mode="json"),
    )
    session.add(payslip)
    await session.flush()
    return IssuedPayslip(payslip_id=payslip.id, number=payslip.number)


# --- Reading (C4) --------------------------------------------------------------------


@dataclass(frozen=True)
class StoredPayslip:
    """A payslip row as rendered."""

    id: uuid.UUID
    number: str
    payroll_period_id: uuid.UUID
    issued_at: datetime
    language: str
    template_version: int
    snapshot: Mapping[str, Any]


async def get_payslip(
    session: AsyncSession, tutor_user_id: uuid.UUID, payslip_id: uuid.UUID
) -> StoredPayslip | None:
    """This tutor's payslip ``payslip_id``, or ``None`` when the tutor has no
    such payslip. One statement, no lock."""
    row = (
        await session.execute(
            select(
                Payslip.id,
                Payslip.number,
                Payslip.payroll_period_id,
                Payslip.issued_at,
                Payslip.language,
                Payslip.template_version,
                Payslip.snapshot,
            ).where(Payslip.id == payslip_id, Payslip.tutor_user_id == tutor_user_id)
        )
    ).first()
    if row is None:
        return None
    return StoredPayslip(**row._mapping)


# --- Rendering (D) -------------------------------------------------------------------


def template_for(version: int) -> str:
    """The template of ``version``; an unknown one →
    :class:`UnknownPayslipTemplateError`."""
    try:
        return PAYSLIP_TEMPLATES[version]
    except KeyError:
        raise UnknownPayslipTemplateError(
            f"no payslip template version {version}"
        ) from None


def _trimmed(value: Decimal) -> str:
    """At least 2 and at most 4 decimals, trailing zeros trimmed past 2, with
    comma thousands."""
    whole, fraction = f"{abs(value):,.4f}".split(".")
    fraction = fraction.rstrip("0").ljust(2, "0")
    sign = "-" if value < 0 else ""
    return f"{sign}{whole}.{fraction}"


def format_rate(rate: Rate) -> str:
    """``$120.00``, ``$120.125``, ``$1,000.1234`` (D1)."""
    text = _trimmed(rate.amount)
    if text.startswith("-"):
        return f"-${text[1:]}"
    return f"${text}"


def format_percent(percent: Rate) -> str:
    """``50.00%``, ``12.5%`` → ``12.50%``, ``33.3333%``."""
    return f"{_trimmed(percent.amount)}%"


PREFIX: Final = "payslips.v1"


def _period_text(lang: i18n.Language, dates: PayslipPeriodDates) -> str:
    return i18n.t(
        lang,
        f"{PREFIX}.period_range",
        start=i18n.format_date(lang, dates.starts_on),
        end=i18n.format_date(lang, dates.ends_on),
    )


def _rule_text(lang: i18n.Language, rule: PayslipPayRule) -> str:
    if rule.mode == "percent" and rule.percent is not None:
        return i18n.t(
            lang, f"{PREFIX}.rules.percent", percent=format_percent(rule.percent)
        )
    if rule.mode == "fixed" and rule.amount is not None:
        return i18n.t(
            lang, f"{PREFIX}.rules.fixed", amount=i18n.format_money(rule.amount)
        )
    return i18n.t(lang, f"{PREFIX}.rules.none")


def _class_row(lang: i18n.Language, line: PayslipLine) -> dict[str, object]:
    event = i18n.t(lang, f"{PREFIX}.kinds.{line.kind}")
    if line.pay_rule is not None:
        event = i18n.t(
            lang,
            f"{PREFIX}.event_with_rule",
            event=event,
            rule=_rule_text(lang, line.pay_rule),
        )
    if line.covered_by_salary:
        rate = i18n.t(lang, f"{PREFIX}.covered_by_salary")
    elif line.rate is not None and line.rate_model is not None:
        rate = i18n.t(
            lang,
            f"{PREFIX}.rate",
            rate=format_rate(line.rate),
            model=i18n.t(lang, f"{PREFIX}.models.{line.rate_model}"),
        )
        if line.cumulative_sessions is not None and line.cohort_sessions is not None:
            rate = i18n.t(
                lang,
                f"{PREFIX}.course_share",
                rate=rate,
                k=line.cumulative_sessions,
                n=line.cohort_sessions,
            )
    else:
        rate = "—"
    return {
        "date": i18n.format_date(lang, line.service_date) if line.service_date else "—",
        "class_kind": (
            i18n.t(lang, f"{PREFIX}.class_kinds.{line.class_kind}")
            if line.class_kind
            else "—"
        ),
        "event": event,
        "minutes": line.minutes,
        "rate": rate,
        "amount": line.amount,
    }


def _salary_row(lang: i18n.Language, line: PayslipLine) -> dict[str, object]:
    salary = line.salary
    if salary is None:  # pragma: no cover - a salary line has its piece
        raise ValueError("a salary line without its piece")
    return {
        "piece": _period_text(
            lang,
            PayslipPeriodDates(starts_on=salary.starts_on, ends_on=salary.ends_on),
        ),
        "detail": i18n.t(
            lang,
            f"{PREFIX}.salary.detail",
            numerator=salary.numerator,
            denominator=salary.denominator,
            amount=i18n.format_money(salary.amount),
            basis=i18n.t(lang, f"{PREFIX}.bases.{salary.basis}"),
        ),
        "amount": line.amount,
    }


def _item_row(line: PayslipLine) -> dict[str, object]:
    item = line.pay_item
    if item is None:  # pragma: no cover - an item line has its item
        raise ValueError("a pay item line without its item")
    return {
        "type_name": item.type_name,
        "description": item.description,
        "amount": line.amount,
    }


def _adjustment_row(lang: i18n.Language, line: PayslipLine) -> dict[str, object]:
    adjustment = line.adjustment
    if adjustment is None:  # pragma: no cover - an adjustment line has its row
        raise ValueError("an adjustment line without its adjustment")
    details: list[str] = []
    if adjustment.service_date is not None:
        work = i18n.format_date(lang, adjustment.service_date)
        if adjustment.class_kind is not None:
            details.append(
                i18n.t(
                    lang,
                    f"{PREFIX}.adjustments.work_kind",
                    date=work,
                    class_kind=i18n.t(
                        lang, f"{PREFIX}.class_kinds.{adjustment.class_kind}"
                    ),
                )
            )
        else:
            details.append(i18n.t(lang, f"{PREFIX}.adjustments.work", date=work))
    if adjustment.origin_period is not None:
        details.append(
            i18n.t(
                lang,
                f"{PREFIX}.adjustments.origin",
                period=_period_text(lang, adjustment.origin_period),
            )
        )
    if adjustment.paired_period is not None:
        details.append(
            i18n.t(
                lang,
                f"{PREFIX}.adjustments.paired",
                period=_period_text(lang, adjustment.paired_period),
            )
        )
    return {
        "kind": i18n.t(lang, f"{PREFIX}.adjustment_kinds.{adjustment.kind}"),
        "details": details,
        "reason": adjustment.reason,
        "amount": line.amount,
    }


def _sections(
    lang: i18n.Language, lines: Iterable[PayslipLine]
) -> dict[str, list[dict[str, object]]]:
    sections: dict[str, list[dict[str, object]]] = {
        "classes": [],
        "salary": [],
        "bonuses": [],
        "deductions": [],
        "adjustments": [],
    }
    for line in lines:
        if line.kind in SESSION_LINE_KINDS:
            sections["classes"].append(_class_row(lang, line))
        elif line.kind == SALARY:
            sections["salary"].append(_salary_row(lang, line))
        elif line.kind == PAY_ITEM:
            key = "bonuses" if line.amount.is_positive() else "deductions"
            sections[key].append(_item_row(line))
        else:
            sections["adjustments"].append(_adjustment_row(lang, line))
    return sections


def _language(stored: StoredPayslip) -> i18n.Language:
    language = i18n.resolve_language(stored.language)
    if language != stored.language:
        raise ValueError(f"payslip {stored.id} has an unknown language")
    return language


def payslip_context(stored: StoredPayslip) -> dict[str, object]:
    """The template context of a stored payslip (the snapshot validated)."""
    lang = _language(stored)
    snapshot = parse_snapshot(stored.snapshot)
    period = snapshot.period
    return {
        "center": snapshot.center.model_dump(),
        "payslip": {
            "number": stored.number,
            "issued_on": snapshot.issued_on,
            "tutor_name": snapshot.tutor.display_name,
            "period": _period_text(
                lang,
                PayslipPeriodDates(starts_on=period.starts_on, ends_on=period.ends_on),
            ),
            "frequency": i18n.t(lang, f"{PREFIX}.frequencies.{period.pay_frequency}"),
            "finalized_on": period.finalized_on,
            "total": snapshot.total,
        },
        "sections": _sections(lang, snapshot.lines),
        "summary": snapshot.summary.model_dump(),
    }


def payslip_title(stored: StoredPayslip) -> str:
    """The PDF title metadata: ``Comprobante de pago P-2026-000123``."""
    return f"{i18n.t(_language(stored), f'{PREFIX}.title')} {stored.number}"


def payslip_html(stored: StoredPayslip) -> str:
    """The payslip's HTML in its stored language and template version."""
    template = template_for(stored.template_version)
    return pdf.render_html(
        template,
        {**payslip_context(stored), "document_title": payslip_title(stored)},
        lang=_language(stored),
    )


def render_payslip_document(stored: StoredPayslip) -> pdf.RenderedPdf:
    """The payslip's PDF with its page count and refused URLs (synchronous,
    CPU-bound; see the module docstring)."""
    template = template_for(stored.template_version)
    return pdf.render_pdf_document(
        template,
        payslip_context(stored),
        lang=_language(stored),
        title=payslip_title(stored),
    )


def render_payslip(stored: StoredPayslip) -> bytes:
    """The payslip's PDF bytes."""
    return render_payslip_document(stored).data
