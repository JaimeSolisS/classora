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

**Drift (#110 D3).** :func:`line_drift` compares stored lines with fresh drafts,
as multisets and never by id, so their order can neither raise a false alarm nor
hide a change. A difference on the same source (session, salary piece or item) is
``changed``.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
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
    created_at: datetime | None = None
    """When the version was written: #111 E4 ignores a version written after a
    salaried period was finalized. ``None`` counts as always there."""


@dataclass(frozen=True)
class SalaryCover:
    """Dates a ``salary`` line of a **finalized** period paid, and when that
    period was finalized (#111 E4)."""

    starts_on: date
    ends_on: date
    finalized_at: datetime


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
    """What a line of a finalized period fixes for good (#442 C3), or a posted
    per-course ``late_approval``, an anchor of the walk (M7-08d E1): both are
    fixed steps, with the ``k``, ``N`` and rate they stored."""

    class_session_id: uuid.UUID
    cumulative_sessions: int
    cohort_sessions: int
    rate: Rate
    anchor: bool = False
    """A posted anchor, not a line: its jump in ``k`` over the fixed step before
    it is the number of waiting sessions it counted (:func:`course_anchor_draft`)."""


type CohortStep = ApprovedSession | FinalizedCourseLine


@dataclass(frozen=True)
class CohortWalk:
    """A cohort as one tutor's per-course pay reads it (#442 C).

    ``steps`` are that tutor's approved sessions not in a finalized period, their
    finalized lines and their anchors, in :func:`in_walk_order`."""

    first_session_date: date
    planned_sessions: int
    steps: Sequence[CohortStep]
    late: Sequence[ApprovedSession] = ()
    """Approved sessions dated in a finalized period with neither a line nor an
    anchor, by date: never walked by :func:`walk_cohort`, only read by
    :func:`course_anchor_draft` (M7-08d E1)."""
    settled: frozenset[uuid.UUID] = frozenset()
    """The sessions among ``late`` an adjustment names: paid by hand (M7-08c),
    so they never wait for an anchor (M7-08d QA D2)."""


@dataclass(frozen=True)
class PayBasis:
    """Every version of the tutor and every rule, any order."""

    terms: Sequence[TermsBasisVersion]
    rates: Sequence[RateVersion]
    rules: Sequence[SessionPayRuleVersion]
    salary_covers: Sequence[SalaryCover] = ()
    """Work after lock only (#111 E4): the job and the unresolved reads pass
    them, a run of an open period never does."""


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
class FinalizedPricedLine:
    """What the rate refusal needs of a finalized ``course`` line (#110 E5)."""

    class_session_id: uuid.UUID
    service_date: date
    rate_model: str
    rate: Rate


def same_price(
    line_model: str, line_rate: Rate, model: str | None, rate: Rate | None
) -> bool:
    """A version of the line's own model and rate can't change its price (#110 QA
    D4, ruling (a)). ``Rate`` compares its exact value; money is MXN only, so
    there is no currency to compare (ADR-0004)."""
    return model == line_model and rate is not None and rate == line_rate


def repriced_on(
    line: FinalizedPricedLine,
    *,
    first_session_date: date,
    new_model: str | None,
    new_rate: Rate | None,
    effective_from: date,
    until: date | None,
) -> date | None:
    """The newest date on which a new ``course`` version of ``new_model``, in
    force from ``effective_from`` to ``until``, would change this finalized line's
    price, or ``None`` (#110 E5, QA D1, D2, D3). A date after the answer clears the
    refusal: the window shrinks, so it can't take a new date.

    - A ``per_course`` line is priced by the version on the cohort's first date,
      and only there: whatever takes that date over changes it.
    - An ordinary line is priced by the version on its own date, so whatever takes
      that date over changes it. It reads the first date for one thing, that the
      version there isn't ``per_course``, so only a ``per_course`` version can
      change it from there. Any other model, a stop included, leaves it as it is,
      and that is the way out for a ``no_rate`` first session.
    - A version of the line's own model and rate refuses neither (QA D4): it
      changes the version the line names, never its price."""

    if same_price(line.rate_model, line.rate, new_model, new_rate):
        return None

    def taken(day: date) -> bool:
        return effective_from <= day and (until is None or day < until)

    if line.rate_model == PER_COURSE:
        return first_session_date if taken(first_session_date) else None
    if taken(line.service_date):
        return line.service_date
    if new_model == PER_COURSE and taken(first_session_date):
        return first_session_date
    return None


def repriced_by_delete(
    line: FinalizedPricedLine,
    *,
    first_session_date: date,
    previous: RateVersion | None,
    deleted_from: date,
    until: date | None,
) -> date | None:
    """:func:`repriced_on` for a delete (M7-08d E3): deleting the version in
    force from ``deleted_from`` to ``until`` gives that window to ``previous``, or
    to nothing if it was the first. On every date of the window the price is then
    ``previous``'s, exactly as if a version of its model and rate had been added
    there, so the add's rule answers, the same-rate pass (#110 QA D4) included."""
    return repriced_on(
        line,
        first_session_date=first_session_date,
        new_model=None if previous is None else previous.rate_model,
        new_rate=None if previous is None else previous.rate,
        effective_from=deleted_from,
        until=until,
    )


@dataclass(frozen=True)
class LineDrift:
    """How many lines differ between a prepared period and its facts (#110 D3)."""

    added: int
    removed: int
    changed: int

    @property
    def is_clean(self) -> bool:
        return self.added == self.removed == self.changed == 0


def _line_source(
    line: LineDraft,
) -> tuple[uuid.UUID | None, date | None, uuid.UUID | None]:
    """What one line is the line of, whatever its figures or its kind."""
    return line.class_session_id, line.salary_starts_on, line.pay_item_id


def line_drift(stored: Iterable[LineDraft], fresh: Iterable[LineDraft]) -> LineDrift:
    """The difference between two sets of lines, in any order (module
    docstring)."""
    kept, computed = Counter(stored), Counter(fresh)
    gone = Counter(_line_source(line) for line in (kept - computed).elements())
    new = Counter(_line_source(line) for line in (computed - kept).elements())
    return LineDrift(
        added=(new - gone).total(),
        removed=(gone - new).total(),
        changed=(new & gone).total(),
    )


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


def _rate_in_effect(
    basis: PayBasis,
    class_kind: str,
    on: date,
    written_by: datetime | None = None,
) -> RateInEffect | None:
    """The version of ``class_kind`` on ``on``, if it has a model. With
    ``written_by``, only the versions that existed then."""
    version = _in_effect(
        (
            rate
            for rate in basis.rates
            if rate.class_kind == class_kind
            and (
                written_by is None
                or rate.created_at is None
                or rate.created_at <= written_by
            )
        ),
        on,
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


def salary_cover_of(basis: PayBasis, on: date) -> datetime | None:
    """When the finalized period whose salary line paid ``on`` was finalized,
    or ``None`` (#111 E4)."""
    for cover in basis.salary_covers:
        if cover.starts_on <= on <= cover.ends_on:
            return cover.finalized_at
    return None


def _ordinary_basis(
    basis: PayBasis, class_kind: str, on: date
) -> RateInEffect | Covered | None:
    """:func:`resolve_basis`, but for a ``per_course`` version, which pays only
    through a cohort's walk (#442 B3).

    **A date a finalized salary line paid (#111 E4)** is judged as it stood at
    that period's finalize: a rate version written later is ignored, and the
    salary covers whatever has no rate, whatever the terms say now (a late stop
    included). A rate that existed then still pays."""
    finalized_at = salary_cover_of(basis, on)
    if finalized_at is not None:
        settled = _rate_in_effect(basis, class_kind, on, finalized_at)
        if settled is None:
            return COVERED
        return settled if settled.rate_model in ORDINARY_MODELS else None
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
    return _priced_approved_draft(basis, paid, work)


def _priced_approved_draft(
    basis: PayBasis, paid: RateInEffect, work: ApprovedSession
) -> LineDraft:
    """The line of ``work`` at ``paid``, with the rule of its event on the service
    date from ``basis``. A line and an adjustment share it (pm-107)."""
    minutes = work.approved_minutes
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


def in_walk_order(steps: Sequence[CohortStep]) -> list[CohortStep]:
    """``steps`` by ``(session_starts_at, class_session_id)``, with the fixed
    steps re-sorted by their stored ``k`` in the places fixed steps hold
    (M7-08d E1).

    Oldest-first finalize (#110 E2) makes the finalized periods a prefix in time,
    so the approved steps never fall between two fixed ones: the ones before are
    work before the first period, the ones after are open. An anchor stores the
    ``k`` it was posted at, after every finalized line then, so its order among
    the fixed steps is its ``k``, not its date."""
    fixed = iter(
        sorted(
            (step for step in steps if isinstance(step, FinalizedCourseLine)),
            key=lambda step: step.cumulative_sessions,
        )
    )
    return [
        next(fixed) if isinstance(step, FinalizedCourseLine) else step for step in steps
    ]


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
    and the next line doesn't catch it up.

    **A fixed step beyond ``N``** (M7-08d edge case, QA round 1 D1): a fixed
    step stores the ``N`` it was priced at, and a step after it can find ``k >
    N`` once a session not yet taught is cancelled. Its ``k`` counts as ``N``: it
    catches the cohort up to the rate, never past it, and stores ``N``. With no
    fixed step before it, ``k > N`` stays a bug."""
    n = cohort.planned_sessions
    k = 0
    cum = ZERO
    finalized_peak = ZERO
    fixed_seen = False
    drafts: list[LineDraft] = []
    for step in cohort.steps:
        if isinstance(step, FinalizedCourseLine):
            k = step.cumulative_sessions
            finalized_peak = max(
                finalized_peak, step.rate.to_money(k, step.cohort_sessions)
            )
            cum = finalized_peak
            fixed_seen = True
            continue
        k += 1
        if k > n and not fixed_seen:
            raise CourseWalkError(str(step.class_session_id))
        try:
            at = min(k, n)
            reached = max(cum, paid.rate.to_money(at, n))
            in_period = starts_on <= step.service_date <= ends_on
            if in_period and _paying_terms(basis, step.service_date) is not None:
                drafts.append(_course_draft(basis, paid, step, reached - cum, at, n))
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


# --- Work after lock (#111; ADR-0017 E11 and its #111 notes) -----


@dataclass(frozen=True)
class OriginSnapshot:
    """What paid a session first, in a finalized way (#111 E2): a finalized
    session line, or a ``late_approval``. ``None`` values: "covered by salary".
    A ``per_course`` origin also needs its kind, amount, ``k``, ``N`` and rule
    (M7-08d E2)."""

    rate_model: str | None
    rate: Rate | None
    compensation_rate_id: uuid.UUID | None
    kind: str | None = None
    line_amount: Money | None = None
    cumulative_sessions: int | None = None
    cohort_sessions: int | None = None
    session_pay_rule_id: uuid.UUID | None = None


def late_approval_draft(
    basis: PayBasis, work: ApprovedSession | PayableCancellation
) -> LineDraft | None:
    """What a session line would have been, from the same functions as a line
    (pm-107), on the service date's basis (ADR-0017 E11, #110 E5), with #111 E4's
    salaried dates applied through ``basis.salary_covers``. ``None``: no basis, or
    nothing owed under the rule."""
    try:
        if isinstance(work, ApprovedSession):
            return _approved_draft(basis, work)
        return _cancellation_draft(basis, work)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError(work.class_session_id) from None


def correction_draft(
    basis: PayBasis, origin: OriginSnapshot, work: ApprovedSession
) -> LineDraft | None:
    """The current revision priced on the origin's snapshot, with the rule of
    its event on the service date (#111 E2). A covered origin has no price, so
    no correction (C7); a ``per_course`` one is :func:`course_correction_draft`'s,
    never priced here."""
    if (
        origin.rate_model not in ORDINARY_MODELS
        or origin.rate is None
        or origin.compensation_rate_id is None
    ):
        return None
    paid = RateInEffect(
        compensation_rate_id=origin.compensation_rate_id,
        rate_model=origin.rate_model,
        rate=origin.rate,
        effective_from=work.service_date,
    )
    try:
        return _priced_approved_draft(basis, paid, work)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError(work.class_session_id) from None


def course_anchor_draft(
    basis: PayBasis, paid: RateInEffect, cohort: CohortWalk, work: ApprovedSession
) -> LineDraft | None:
    """A late session of a cohort paid ``per_course`` as an anchor of the walk
    (M7-08d E1). It is the step after every fixed step of ``cohort``, and after
    the work before the first period, which counts in ``K`` as in
    :func:`walk_cohort`: ``k = K + 1``, paid
    ``max(0.00, rate x k / N - peak)``, or its event's amount, where the peak is
    the highest ``rate x k / N`` of the fixed steps, each at its own ``N``. The
    open steps, later in time, aren't walked. ``None``: no terms on its date, so
    no line would have been paid either.

    **The other late sessions dated before it** (``cohort.late``), and after the
    cohort's latest fixed step, count in ``k`` too. The job anchors a cohort oldest
    first (M7-08d QA D1, :func:`~app.workflows.payroll_adjustments.posting_order`),
    so one still waiting floored at ``0.00`` in this run: it raises nothing, and
    this anchor pays its share with its own. One dated before a fixed step isn't
    counted again: an anchor after it already counted it, and a line after it never
    walked it, so it takes its own ``k`` when it is anchored. An anchor floored at
    ``0.00`` is never written (P9), so without that a cohort whose remaining
    sessions are all late would compute ``K + 1`` again and again and never reach
    its rate. A late session settled by hand (``cohort.settled``, M7-08c) or with no
    paying terms on its date never waits: it will never be anchored, and like the
    walk, which leaves every finalized-period session with neither a line nor an
    anchor out of ``k``, the cohort's ``k`` stays one short, so its share is
    consumed once and nothing pays it again (QA D2). ``k`` is stored capped at
    ``N``. So a cohort pays the highest ``rate x k / N`` among its fixed steps:
    never more than its rate, the rate itself once its last session is fixed, less
    one share for each session paid by hand (the oracle in
    ``tests/unit/test_payroll_course_anchors.py``)."""
    if _paying_terms(basis, work.service_date) is None:
        return None
    n = cohort.planned_sessions
    k = 0
    cum = ZERO
    peak = ZERO
    try:
        for step in cohort.steps:
            if isinstance(step, FinalizedCourseLine):
                k = step.cumulative_sessions
                peak = max(peak, step.rate.to_money(k, step.cohort_sessions))
                cum = peak
            elif step.service_date < work.service_date:
                k += 1  # work before the first period, as the walk counts it
                cum = max(cum, paid.rate.to_money(min(k, n), n))
        eligible = sorted(
            (
                item
                for item in (*cohort.late, work)
                if item.class_session_id not in cohort.settled
                and _paying_terms(basis, item.service_date) is not None
            ),
            key=lambda item: (item.service_date, item.class_session_id),
        )
        consumed = {
            item.class_session_id for item in eligible[: _counted_waiting(cohort)]
        }
        if work.class_session_id in consumed:
            return None  # an earlier anchor counted it and paid its share
        k += 1 + sum(
            1
            for item in eligible
            if item.class_session_id not in consumed
            and (item.service_date, item.class_session_id)
            < (work.service_date, work.class_session_id)
        )
        stored = min(k, n)
        share = max(cum, paid.rate.to_money(stored, n)) - cum
        return _course_draft(basis, paid, work, share, stored, n)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError(work.class_session_id) from None


def _counted_waiting(cohort: CohortWalk) -> int:
    """How many waiting sessions the cohort's anchors have counted: each anchor's
    jump in ``k`` over the step before it, in walk order (M7-08d QA D1). The
    work before the first period, walked first, counts as steps."""
    previous = 0
    counted = 0
    for step in cohort.steps:
        if isinstance(step, FinalizedCourseLine):
            if step.anchor:
                counted += max(0, step.cumulative_sessions - previous - 1)
            previous = step.cumulative_sessions
        else:
            previous += 1
    return counted


def _whole_share(basis: PayBasis, origin: OriginSnapshot) -> bool:
    """Whether a per-course origin's amount is its whole share: a ``session``,
    or an event whose rule paid exactly 100 % (M7-08d E2)."""
    if origin.kind == "session":
        return True
    rule = next(
        (item for item in basis.rules if item.id == origin.session_pay_rule_id), None
    )
    return rule is not None and rule.mode == "percent" and rule.percent == WHOLE_LINE


def course_correction_draft(
    basis: PayBasis, origin: OriginSnapshot, work: ApprovedSession
) -> LineDraft | None:
    """The current revision of a session paid ``per_course`` (a finalized line or
    an anchor), at the origin's own ``k``, ``N`` and rate, so the walk's later
    lines don't move (M7-08d E2). Minutes don't change per-course pay, so a
    revision of the origin's kind targets the origin's amount. Another kind
    targets the share ``S`` (the origin's amount when it is a whole share, else
    ``max(0.00, rate x k / N - rate x (k - 1) / N)``) through the line's own
    function: ``S`` for a ``session``, the rule's amount for a ``no_show``."""
    k, n = origin.cumulative_sessions, origin.cohort_sessions
    if (
        origin.rate_model != PER_COURSE
        or origin.rate is None
        or origin.compensation_rate_id is None
        or origin.line_amount is None
        or k is None
        or n is None
    ):
        return None
    paid = RateInEffect(
        compensation_rate_id=origin.compensation_rate_id,
        rate_model=PER_COURSE,
        rate=origin.rate,
        effective_from=work.service_date,
    )
    try:
        share = (
            origin.line_amount
            if _whole_share(basis, origin)
            else max(ZERO, origin.rate.to_money(k, n) - origin.rate.to_money(k - 1, n))
        )
        draft = _course_draft(basis, paid, work, share, k, n)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError(work.class_session_id) from None
    if draft.kind == origin.kind:
        return replace(
            draft,
            amount=origin.line_amount,
            session_pay_rule_id=origin.session_pay_rule_id,
        )
    return draft


def owed(target: Money, paid: Iterable[Money]) -> Money | None:
    """``target`` minus what was paid so far, or ``None`` when that is ``0.00``:
    an adjustment is never ``0`` (P9), and a zero difference is worked out again
    on the next run (#111 E2)."""
    try:
        difference = target - Money.total(paid)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError() from None
    return None if difference.is_zero() else difference
