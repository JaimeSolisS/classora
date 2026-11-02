"""What a tutor is paid for a period's work (#107 D; ADR-0017 E2, E4, E5;
data-model §11.5, §11.6).

Pure functions on frozen values: no I/O, no ``datetime``, no float. The workflow
:mod:`app.workflows.payroll_calculation` reads the facts, in the tutor's own
period, and computes each service date; this module decides the lines.

**Basis (#107 E6)**, resolved on the service date, the same for every line:
no terms or a stop → no line; a rate version with a model → that rate; else a
salary on the terms → covered; else no line. A rate of ``0.0000`` is a rate.

**An approved closeout (D4):** covered → a ``0.00`` ``session`` line, whatever
happened in the session (#107 E5). Else every participant absent → ``no_show``,
before a makeup (#107 E3). Else a makeup → ``makeup`` (#107 E4: the session's
own link, no chain walk, nothing read from the target). Else ``session``.

**A payable cancellation (D6):** ``late`` → ``late_cancellation``, ``no_show``
→ ``no_show``, on the scheduled minutes, with no revision. No line under
``none``, for a covered tutor or with no basis.

**Amounts:** ``per_hour`` is ``rate.to_money(minutes, 60)`` and ``per_session``
``rate.to_money()``, whatever the minutes. Under a rule, ``none`` pays ``0.00``,
``percent`` passes ``percent=`` to the same call, and ``fixed`` pays the rule's
amount. Every amount is rounded once, inside ``Rate.to_money`` (#107 E2).

**Per-course (#442; ADR-0017 E3, #107 E11).** The rate is the version on the
cohort's first session's date (:func:`course_rate`), for all the tutor's
sessions of the cohort. If that version isn't ``per_course``, each session is
ordinary work on its own date, and a ``per_course`` version there is no line
(``no_rate``, fixed by a back-dated version). :class:`CohortWalk` holds the
tutor's sessions of the cohort in order. Each approved one advances ``k`` and
``cum = max(previous cum, rate.to_money(k, N))``, whatever its event and whichever
period it falls in, and the line is the difference, so a recalculation gives
the same lines in any period order. A finalized line sets ``k`` to its stored
value and ``cum`` to the highest of the finalized lines so far, from each one's
stored ``k``, ``N`` and rate (:class:`FinalizedCourseLine`): a line floored at
``0.00`` stores less than was already paid, and the next line must not pay it
again. Only finalized lines feed that maximum, so sessions walked before a
finalized line can't hide what the sessions after it earn. A course session has
no cancellation request and no makeup link (D3).

**Unresolved work (#443; ADR-0017 E2, #106 E4, #107 E12, E13).**
:func:`unresolved_reason` says why work with no line can't get one. It asks
:func:`has_basis`, which is built from the same helpers the lines use, and a test
feeds the same cases to both, so a line and an item can't disagree. The reason is
the first of ``before_first_period`` (a date no period can ever cover, and the
only reason that doesn't block), ``no_pay_terms`` and ``no_rate``. The period's
status plays no part: an item in a finalized period stays listed until it has a
basis, and #111 pays it then.

Nothing here touches a ``Decimal`` or an ``.amount``, on a ``Rate`` or a
``Money``: ``tests/unit/test_payroll_calculation_pure.py`` reads this file's syntax
tree, and the workflow's.
"""

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol, cast, get_args

from app.core.money import Money, MoneyRangeError, Rate
from app.modules.payroll.errors import PayrollAmountOutOfRangeError
from app.modules.payroll.schemas import (
    RateInEffect,
    SessionPayRuleVersion,
    UnresolvedReason,
    UnresolvedSource,
)

ZERO: Final = Money(0)
PER_HOUR: Final = "per_hour"
PER_SESSION: Final = "per_session"
PER_COURSE: Final = "per_course"
COURSE: Final = "course"
MINUTES_PER_HOUR: Final = 60
WHOLE_LINE: Final = Rate(100)
NOT_PLANNED_STATES: Final = frozenset({"cancelled", "skipped_closure"})
"""The states that leave a cohort's ``N`` (#442 C1). Everything else is a
session still planned, ``conflict``, ``unassigned`` and ``offered`` included."""

ORDINARY_MODELS: Final = frozenset({PER_HOUR, PER_SESSION})
CANCELLATION_KINDS: Final = {"late": "late_cancellation", "no_show": "no_show"}

BEFORE_FIRST_PERIOD: Final[UnresolvedReason] = "before_first_period"
NO_PAY_TERMS: Final[UnresolvedReason] = "no_pay_terms"
NO_RATE: Final[UnresolvedReason] = "no_rate"
UNRESOLVED_REASONS: Final[tuple[UnresolvedReason, ...]] = get_args(
    UnresolvedReason.__value__
)

type Covered = Literal["covered"]
COVERED: Final[Covered] = "covered"


@dataclass(frozen=True)
class RateVersion:
    """What the calculation needs of one ``compensation_rates`` row."""

    id: uuid.UUID
    class_kind: str
    effective_from: date
    rate_model: str | None
    rate: Rate | None


@dataclass(frozen=True)
class TermsBasisVersion:
    """What the calculation needs of one ``tutor_pay_terms`` row."""

    id: uuid.UUID
    effective_from: date
    pay_frequency: str | None
    salary: Money | None


@dataclass(frozen=True)
class ApprovedSession:
    """A current approval revision, with what the workflow worked out."""

    approval_id: uuid.UUID
    class_session_id: uuid.UUID
    class_kind: str
    service_date: date
    approved_minutes: int
    all_absent: bool
    is_makeup: bool


@dataclass(frozen=True)
class PayableCancellation:
    """A family's late or no-show cancellation of the tutor's session."""

    class_session_id: uuid.UUID
    class_kind: str
    service_date: date
    scheduled_minutes: int
    classification: str


@dataclass(frozen=True)
class FinalizedCourseLine:
    """What a line of a finalized period fixes for good (#442 C3)."""

    class_session_id: uuid.UUID
    cumulative_sessions: int
    cohort_sessions: int
    rate: Rate


type CohortStep = ApprovedSession | FinalizedCourseLine


@dataclass(frozen=True)
class CohortWalk:
    """A cohort as one tutor's per-course pay reads it (#442 C).

    ``steps`` are that tutor's approved sessions not in a finalized period and
    their finalized lines, by ``(session_starts_at, class_session_id)``."""

    first_session_date: date
    planned_sessions: int
    steps: Sequence[CohortStep]


@dataclass(frozen=True)
class PayBasis:
    """Every version of the tutor and every rule, any order."""

    terms: Sequence[TermsBasisVersion]
    rates: Sequence[RateVersion]
    rules: Sequence[SessionPayRuleVersion]


@dataclass(frozen=True)
class LineDraft:
    """A line before it is written. Two runs that see the same facts make
    equal drafts, so #110 compares them with no write. A session-sourced line
    fills the session fields, a ``salary`` line (#108) the salary ones, and a
    ``pay_item`` line (#109) only ``pay_item_id``."""

    kind: str
    amount: Money
    class_session_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    service_date: date | None = None
    class_kind: str | None = None
    minutes: int | None = None
    rate_model: str | None = None
    rate: Rate | None = None
    compensation_rate_id: uuid.UUID | None = None
    session_pay_rule_id: uuid.UUID | None = None
    cumulative_sessions: int | None = None
    cohort_sessions: int | None = None
    tutor_pay_terms_id: uuid.UUID | None = None
    salary_amount: Money | None = None
    salary_starts_on: date | None = None
    salary_ends_on: date | None = None
    proration_numerator: int | None = None
    proration_denominator: int | None = None
    proration_basis: str | None = None
    pay_item_id: uuid.UUID | None = None


@dataclass(frozen=True)
class UnresolvedCandidate:
    """Approved work with no line, and what deciding on it needs (#443 A2).

    ``classification`` is a cancellation's final outcome. ``first_session_date``
    is the cohort's first session, for a closeout of a course session."""

    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID
    class_kind: str
    service_date: date
    source: UnresolvedSource
    classification: str | None = None
    first_session_date: date | None = None


@dataclass(frozen=True)
class UnresolvedItem:
    """Work that can't be paid, and why (#443 A3)."""

    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID
    class_kind: str
    service_date: date
    source: UnresolvedSource
    reason: UnresolvedReason

    @property
    def blocking(self) -> bool:
        """``before_first_period`` is the one thing no fix reaches (#106 E4)."""
        return self.reason != BEFORE_FIRST_PERIOD


class ServiceDateOutsidePeriodError(RuntimeError):
    """A fact dated outside the period: the bounds of a read are wrong (D1).
    An error, never a line (P6)."""


class CourseWalkError(RuntimeError):
    """``k > N``: an approved session left ``N``, which can't happen, because an
    approved session is ``completed`` (#442 C5). A bug, never a capped line."""


class _Dated(Protocol):
    @property
    def effective_from(self) -> date: ...


class _Work(Protocol):
    @property
    def class_session_id(self) -> uuid.UUID: ...

    @property
    def service_date(self) -> date: ...


def _in_effect[V: _Dated](versions: Iterable[V], on: date) -> V | None:
    """The version with the latest ``effective_from <= on``."""
    latest: V | None = None
    for version in versions:
        if version.effective_from <= on and (
            latest is None or version.effective_from > latest.effective_from
        ):
            latest = version
    return latest


def _rate_in_effect(basis: PayBasis, class_kind: str, on: date) -> RateInEffect | None:
    """The version of ``class_kind`` on ``on``, if it has a model."""
    version = _in_effect(
        (rate for rate in basis.rates if rate.class_kind == class_kind), on
    )
    if version is None or version.rate_model is None or version.rate is None:
        return None
    return RateInEffect(
        compensation_rate_id=version.id,
        rate_model=version.rate_model,
        rate=version.rate,
        effective_from=version.effective_from,
    )


def _paying_terms(basis: PayBasis, on: date) -> TermsBasisVersion | None:
    """The terms on ``on``, unless there are none or they stop pay (#107 E6)."""
    terms = _in_effect(basis.terms, on)
    return None if terms is None or terms.pay_frequency is None else terms


def resolve_basis(
    basis: PayBasis, class_kind: str, on: date
) -> RateInEffect | Covered | None:
    """What pays work of ``class_kind`` on ``on`` (#107 E6). Terms and rates
    are looked up whatever the tutor's role or state."""
    terms = _paying_terms(basis, on)
    if terms is None:
        return None
    paid = _rate_in_effect(basis, class_kind, on)
    if paid is not None:
        return paid
    return COVERED if terms.salary is not None else None


def _ordinary_basis(
    basis: PayBasis, class_kind: str, on: date
) -> RateInEffect | Covered | None:
    """:func:`resolve_basis`, but for a ``per_course`` version, which pays only
    through a cohort's walk (#442 B3)."""
    paid = resolve_basis(basis, class_kind, on)
    if paid is None or paid == COVERED or paid.rate_model in ORDINARY_MODELS:
        return paid
    return None


def course_rate(basis: PayBasis, first_session_date: date) -> RateInEffect | None:
    """The ``per_course`` version on the cohort's first session's date, if that
    version is one (#442 B1, B2)."""
    paid = _rate_in_effect(basis, COURSE, first_session_date)
    return paid if paid is not None and paid.rate_model == PER_COURSE else None


def planned_sessions(states: Iterable[str]) -> int:
    """``N``: the cohort's sessions not cancelled or skipped (#442 C1)."""
    return sum(1 for state in states if state not in NOT_PLANNED_STATES)


def rule_in_effect(basis: PayBasis, event: str, on: date) -> SessionPayRuleVersion:
    """The rule of ``event`` on the service date ``on``."""
    rule = _in_effect((item for item in basis.rules if item.event == event), on)
    if rule is None:  # pragma: no cover - the seeds are effective from 2000
        raise RuntimeError("no session pay rule is in force (run make migrate)")
    return rule


def ordinary_amount(
    paid: RateInEffect, minutes: int, *, percent: Rate | None = None
) -> Money:
    """The line of ``minutes`` at this rate, or ``percent`` of it, rounded
    once. ``per_session`` ignores the minutes."""
    if paid.rate_model == PER_HOUR:
        return paid.rate.to_money(minutes, MINUTES_PER_HOUR, percent=percent)
    if paid.rate_model == PER_SESSION:
        return paid.rate.to_money(percent=percent)
    raise ValueError(f"No ordinary amount for {paid.rate_model}")


def event_amount(
    rule: SessionPayRuleVersion, paid: RateInEffect, minutes: int
) -> Money:
    """What the rule pays for this event (D5)."""
    if rule.mode == "none":
        return ZERO
    if rule.mode == "percent" and rule.percent is not None:
        return ordinary_amount(paid, minutes, percent=rule.percent)
    fixed = rule.fixed_pay
    if rule.mode == "fixed" and fixed is not None:
        return fixed
    raise ValueError(f"Malformed session pay rule {rule.id}")  # pragma: no cover


def course_event_amount(
    rule: SessionPayRuleVersion, rate: Rate, share: Money, k: int, n: int
) -> Money:
    """What the rule pays for this session of ``k`` of ``n`` (#442 D2). ``share``
    is the line it would have been. Other percents take the difference of two
    amounts rounded once each, never a percent of ``share``. Below 100 that is
    capped at ``share``, so a no-show never pays more than teaching the session
    (#442 D8). Above 100 it isn't, and neither is ``fixed``: the Owner set those on
    purpose, they aren't shares of the course, and they can take the course's
    total above the rate."""
    if rule.mode == "none":
        return ZERO
    if rule.mode == "percent" and rule.percent is not None:
        if rule.percent == WHOLE_LINE:
            return share
        amount = max(
            ZERO,
            rate.to_money(k, n, percent=rule.percent)
            - rate.to_money(k - 1, n, percent=rule.percent),
        )
        return amount if rule.percent > WHOLE_LINE else min(share, amount)
    fixed = rule.fixed_pay
    if rule.mode == "fixed" and fixed is not None:
        return fixed
    raise ValueError(f"Malformed session pay rule {rule.id}")  # pragma: no cover


def _approved_kind(work: ApprovedSession) -> str:
    if work.all_absent:
        return "no_show"
    return "makeup" if work.is_makeup else "session"


def _approved_draft(basis: PayBasis, work: ApprovedSession) -> LineDraft | None:
    paid = _ordinary_basis(basis, work.class_kind, work.service_date)
    if paid is None:
        return None
    minutes = work.approved_minutes
    if paid == COVERED:
        return LineDraft(
            kind="session",
            amount=ZERO,
            class_session_id=work.class_session_id,
            approval_id=work.approval_id,
            service_date=work.service_date,
            class_kind=work.class_kind,
            minutes=minutes,
            rate_model=None,
            rate=None,
            compensation_rate_id=None,
            session_pay_rule_id=None,
        )
    kind = _approved_kind(work)
    rule = None if kind == "session" else rule_in_effect(basis, kind, work.service_date)
    return LineDraft(
        kind=kind,
        amount=(
            ordinary_amount(paid, minutes)
            if rule is None
            else event_amount(rule, paid, minutes)
        ),
        class_session_id=work.class_session_id,
        approval_id=work.approval_id,
        service_date=work.service_date,
        class_kind=work.class_kind,
        minutes=minutes,
        rate_model=paid.rate_model,
        rate=paid.rate,
        compensation_rate_id=paid.compensation_rate_id,
        session_pay_rule_id=None if rule is None else rule.id,
    )


def _course_draft(
    basis: PayBasis,
    paid: RateInEffect,
    work: ApprovedSession,
    share: Money,
    k: int,
    n: int,
) -> LineDraft:
    kind = _approved_kind(work)
    rule = None if kind == "session" else rule_in_effect(basis, kind, work.service_date)
    return LineDraft(
        kind=kind,
        amount=(
            share if rule is None else course_event_amount(rule, paid.rate, share, k, n)
        ),
        class_session_id=work.class_session_id,
        approval_id=work.approval_id,
        service_date=work.service_date,
        class_kind=work.class_kind,
        minutes=work.approved_minutes,
        rate_model=paid.rate_model,
        rate=paid.rate,
        compensation_rate_id=paid.compensation_rate_id,
        session_pay_rule_id=None if rule is None else rule.id,
        cumulative_sessions=k,
        cohort_sessions=n,
    )


def walk_cohort(
    basis: PayBasis,
    paid: RateInEffect,
    cohort: CohortWalk,
    *,
    starts_on: date,
    ends_on: date,
) -> list[LineDraft]:
    """The lines of ``cohort`` in the period (#442 C, D1). Every step counts,
    in any period, and a session with no terms on its date too: it earns no line,
    and the next line doesn't catch it up."""
    n = cohort.planned_sessions
    k = 0
    cum = ZERO
    finalized_peak = ZERO
    drafts: list[LineDraft] = []
    for step in cohort.steps:
        if isinstance(step, FinalizedCourseLine):
            k = step.cumulative_sessions
            finalized_peak = max(
                finalized_peak, step.rate.to_money(k, step.cohort_sessions)
            )
            cum = finalized_peak
            continue
        k += 1
        if k > n:
            raise CourseWalkError(str(step.class_session_id))
        try:
            reached = max(cum, paid.rate.to_money(k, n))
            in_period = starts_on <= step.service_date <= ends_on
            if in_period and _paying_terms(basis, step.service_date) is not None:
                drafts.append(_course_draft(basis, paid, step, reached - cum, k, n))
        except MoneyRangeError:
            raise PayrollAmountOutOfRangeError(step.class_session_id) from None
        cum = reached
    return drafts


def _paying_cancellation_rule(
    basis: PayBasis, classification: str, on: date
) -> SessionPayRuleVersion | None:
    """The rule of this outcome on ``on``, unless nothing is owed under it."""
    kind = CANCELLATION_KINDS.get(classification)
    if kind is None:
        return None
    rule = rule_in_effect(basis, kind, on)
    return None if rule.mode == "none" else rule


def _cancellation_draft(basis: PayBasis, work: PayableCancellation) -> LineDraft | None:
    rule = _paying_cancellation_rule(basis, work.classification, work.service_date)
    paid = _ordinary_basis(basis, work.class_kind, work.service_date)
    if rule is None or paid is None or paid == COVERED:
        return None
    return LineDraft(
        kind=rule.event,
        amount=event_amount(rule, paid, work.scheduled_minutes),
        class_session_id=work.class_session_id,
        approval_id=None,
        service_date=work.service_date,
        class_kind=work.class_kind,
        minutes=work.scheduled_minutes,
        rate_model=paid.rate_model,
        rate=paid.rate,
        compensation_rate_id=paid.compensation_rate_id,
        session_pay_rule_id=rule.id,
    )


def draft_lines(
    *,
    starts_on: date,
    ends_on: date,
    basis: PayBasis,
    approved: Iterable[ApprovedSession],
    cancellations: Iterable[PayableCancellation],
    cohorts: Iterable[CohortWalk] = (),
) -> list[LineDraft]:
    """The period's session lines, by service date, then session. An amount beyond
    the money range refuses the run (D11). A session of a cohort paid
    ``per_course`` takes its line from the walk, all others from ``approved``."""

    def draft_of[W: _Work](
        work: W, draft: Callable[[PayBasis, W], LineDraft | None]
    ) -> LineDraft | None:
        if not starts_on <= work.service_date <= ends_on:
            raise ServiceDateOutsidePeriodError(str(work.class_session_id))
        try:
            return draft(basis, work)
        except MoneyRangeError:
            raise PayrollAmountOutOfRangeError(work.class_session_id) from None

    walked: set[uuid.UUID] = set()
    course_lines: list[LineDraft] = []
    for cohort in cohorts:
        paid = course_rate(basis, cohort.first_session_date)
        if paid is not None:
            walked.update(
                step.class_session_id
                for step in cohort.steps
                if isinstance(step, ApprovedSession)
            )
            course_lines += walk_cohort(
                basis, paid, cohort, starts_on=starts_on, ends_on=ends_on
            )

    candidates = (
        *course_lines,
        *(
            draft_of(work, _approved_draft)
            for work in approved
            if work.class_session_id not in walked
        ),
        *(draft_of(work, _cancellation_draft) for work in cancellations),
    )
    return sorted(
        (draft for draft in candidates if draft is not None),
        key=lambda draft: (
            cast(date, draft.service_date),
            cast(uuid.UUID, draft.class_session_id),
        ),
    )


def has_basis(
    basis: PayBasis,
    class_kind: str,
    on: date,
    first_session_date: date | None = None,
) -> bool:
    """Whether work has what it needs to be paid, or is covered (#443 A1). As
    :func:`draft_lines`: a cohort paid ``per_course`` needs only terms on the
    date, because the rate comes from its first session (:func:`walk_cohort`).
    All other work goes by :func:`_ordinary_basis`."""
    if (
        first_session_date is not None
        and course_rate(basis, first_session_date) is not None
    ):
        return _paying_terms(basis, on) is not None
    return _ordinary_basis(basis, class_kind, on) is not None


def unresolved_reason(
    basis: PayBasis,
    work: UnresolvedCandidate,
    first_period_starts_on: date | None,
) -> UnresolvedReason | None:
    """Why this work, which has no line, can't get one (#443 A2, A3); ``None``
    if it can, or if nothing is owed. A tutor with no periods has no first
    period, so no ``before_first_period``: adding terms opens a period (A4)."""
    on = work.service_date
    if work.source == "cancellation" and (
        work.classification is None
        or _paying_cancellation_rule(basis, work.classification, on) is None
    ):
        return None
    if has_basis(basis, work.class_kind, on, work.first_session_date):
        return None
    if first_period_starts_on is not None and on < first_period_starts_on:
        return BEFORE_FIRST_PERIOD
    return NO_PAY_TERMS if _paying_terms(basis, on) is None else NO_RATE


def classify_unresolved(
    basis: PayBasis,
    work: Iterable[UnresolvedCandidate],
    first_period_starts_on: date | None,
) -> list[UnresolvedItem]:
    """The items among one tutor's candidates."""
    items = []
    for candidate in work:
        reason = unresolved_reason(basis, candidate, first_period_starts_on)
        if reason is not None:
            items.append(
                UnresolvedItem(
                    class_session_id=candidate.class_session_id,
                    tutor_user_id=candidate.tutor_user_id,
                    class_kind=candidate.class_kind,
                    service_date=candidate.service_date,
                    source=candidate.source,
                    reason=reason,
                )
            )
    return items
