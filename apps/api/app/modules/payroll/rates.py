"""Tutor compensation: versions and lookups (#105; ADR-0017 E1, data-model
§11.2, §11.3).

The routes are ``app.workflows.payroll_compensation_router``; the workflow
(``app.workflows.payroll_compensation``) reads the tutor and the center's
local ``today``. Every function runs in the caller's Unit of Work and never
commits.

**Writes.** A version is inserted or deleted, never updated (#105 E4): a
correction is a delete and a new insert. :func:`add_pay_terms` and
:func:`add_compensation_rate` accept any date, past or future, also between
two versions, but for a rate that would take over a finalized priced line. They
insert in a savepoint and map the unique key by name, so a concurrent identical
insert is the same ``409``. :func:`delete_pay_terms` and
:func:`delete_compensation_rate` map the guard's 42501 (a finalized line uses
the row) to ``409 …_in_use``.

**Deleting a version an open line uses (#107 E8, #108 F).** ``recalculate``
would pick the same version again, so the line can't free it. The delete locks
every ``open`` period of the tutor, oldest first, then the version, then deletes
its lines in those periods, then the version. A line of a ``prepared`` period
stays, and the foreign key refuses (``23001``, ``409 …_in_use``). The order is
the run's, periods before versions, or the two deadlock. A terms delete takes
the advisory lock below before any of that. A rate delete locks every
not-finalized period instead, as an add does (M7-08d B1), so it can't race a
finalize.

**Terms against generated periods** (#106 F): the terms insert and delete take
the advisory lock of :mod:`~app.modules.payroll.period_rows` first, then
evaluate the terms as they would be after the write
(:func:`~app.modules.payroll.periods.terms_refusal`): ``422
effective_from_inside_generated_period`` and ``422
effective_from_before_first_period``, before ``409 pay_terms_exists`` and
``409 pay_terms_in_use``. A tutor with no periods has neither. Rate versions
aren't affected.

**A rate under a finalized line (#110 E5, narrowing #105 E4).**
:func:`add_compensation_rate` first locks the tutor's not-finalized periods, so it
can't race a finalize, and refuses a version that would take over a date holding
a finalized priced line of that class kind: ``422
effective_from_under_finalized_line``. The window runs from ``effective_from`` to
the next version of the kind. For ``course`` the rule goes by the line's model and
the new version's (#110 QA D1, D2; :func:`~app.modules.payroll.calculation.
repriced_on`): a version refused is one that would change a finalized line's
price, and no other. A version of a line's own model and rate never refuses it,
for any kind (QA D4). The first dates are
``scheduling``'s, so the workflow passes them in (``first_session_dates``).
``no_rate`` work has no line and "covered by salary" no rate snapshot, so a
back-dated rate stays the way out of #443 D7's blocking item. A posted
``late_approval`` or ``hours_correction`` with a rate snapshot counts as a
finalized priced line (M7-08d E4): ordinary on its service date, per-course on
its cohort's first date.

**A delete runs the add's refusal (M7-08d E3).** Deleting a version gives its
window, from its ``effective_from`` to the next version of the kind, to the
previous version, or to nothing if it is the first. That is a version of the
previous one's model and rate added on the deleted one's date, so the same rule
answers (:func:`~app.modules.payroll.calculation.repriced_by_delete`), the
same-rate pass included: ``409 compensation_rate_delete_under_finalized_line``
with ``details.latest_finalized_on``. A version a prepared or finalized line or
an adjustment names is ``409 compensation_rate_in_use`` first, so its own lines
never reach the new check.

**Lookups.** :func:`rate_in_effect` and :func:`terms_in_effect` return the
version with the greatest ``effective_from <= on`` (W3.4), or ``None`` when
that version is a ``NULL`` one (no fall-through to an earlier version) or
there is none. They take a plain date, and they don't check the tutor's role
or state, because the calculation must find a rate for a tutor who left. The
caller passes the service date or, for ``per_course``, the cohort's first
session date (ADR-0017 E3).

**The run's reads (#107 C4, #108 C3).** :func:`rate_versions` and
:func:`terms_basis_versions`, both ``FOR KEY SHARE``, read every version of a
tutor in one statement each; the pure pick of
:mod:`~app.modules.payroll.calculation` replaces a lookup per session. The lock
makes a concurrent delete wait for the run, then fail on the new line, instead
of an insert failing on the foreign key.
"""

import uuid
from collections.abc import Awaitable, Callable, Collection, Hashable, Mapping, Sequence
from datetime import date
from typing import Any, Final, cast

from sqlalchemy import Row, Select, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, Rate
from app.modules.payroll.calculation import (
    FinalizedPricedLine,
    RateVersion,
    TermsBasisVersion,
    repriced_by_delete,
    repriced_on,
)
from app.modules.payroll.errors import (
    CompensationRateDeleteUnderFinalizedLineError,
    CompensationRateExistsError,
    CompensationRateInUseError,
    EffectiveFromBeforeFirstPeriodError,
    EffectiveFromInsideGeneratedPeriodError,
    EffectiveFromUnderFinalizedLineError,
    PayTermsExistsError,
    PayTermsInUseError,
)
from app.modules.payroll.lines import (
    FOREIGN_KEY_REFUSALS,
    delete_lines_of_rate,
    delete_lines_of_terms,
    finalized_course_kind_lines,
    latest_finalized_priced_date,
    lock_open_periods_of_tutor,
    lock_unfinalized_periods_of_tutor,
)
from app.modules.payroll.models import (
    CLASS_KINDS,
    COURSE,
    RATE_KEY,
    TERMS_KEY,
    CompensationRate,
    TutorPayTerms,
)
from app.modules.payroll.period_rows import (
    generated_periods,
    lock_payroll_periods,
    terms_versions,
)
from app.modules.payroll.periods import TermsVersion, terms_refusal
from app.modules.payroll.schemas import (
    ClassKind,
    CompensationRateItem,
    CompensationStatus,
    CompensationSummary,
    CurrentTerms,
    PayFrequency,
    PayTermsItem,
    RateInEffect,
    RateModel,
    TermsInEffect,
)
from app.modules.payroll.settings import payroll_starts_on

INSUFFICIENT_PRIVILEGE: Final = "42501"


# --- Writes --------------------------------------------------------------------------

type FirstSessionDates = Callable[
    [Collection[uuid.UUID]], Awaitable[Mapping[uuid.UUID, date]]
]
"""Each session's cohort's first date, which ``scheduling`` knows (ADR-0002)."""


async def _refuse_against_periods(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    terms_after: Sequence[TermsVersion],
) -> None:
    """The #106 refusals, under the advisory lock."""
    periods = await generated_periods(session, tutor_user_id)
    if not periods:
        return
    floor = await payroll_starts_on(session)
    # Locked once a period exists (#106 E6), so never NULL here.
    refusal = terms_refusal(terms_after, periods, floor or periods[0].starts_on)
    if refusal == "inside_generated_period":
        raise EffectiveFromInsideGeneratedPeriodError()
    if refusal == "before_first_period":
        raise EffectiveFromBeforeFirstPeriodError()


async def add_pay_terms(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    effective_from: date,
    pay_frequency: str | None,
    salary_amount: Money | None,
) -> uuid.UUID:
    """A new terms version: the #106 refusals, then ``409 pay_terms_exists``
    on a taken date."""
    await lock_payroll_periods(session)
    before = await terms_versions(session, tutor_user_id)
    await _refuse_against_periods(
        session,
        tutor_user_id,
        [
            *(v for v in before if v.effective_from != effective_from),
            TermsVersion(effective_from, pay_frequency),
        ],
    )
    terms = TutorPayTerms(
        tutor_user_id=tutor_user_id,
        effective_from=effective_from,
        pay_frequency=pay_frequency,
        salary_amount=salary_amount,
    )
    try:
        async with session.begin_nested():
            session.add(terms)
            await session.flush()
    except IntegrityError as exc:
        if TERMS_KEY in str(exc.orig):
            raise PayTermsExistsError() from None
        raise
    return terms.id


async def add_compensation_rate(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    class_kind: str,
    effective_from: date,
    rate_model: str | None,
    rate: Rate | None,
    first_session_dates: FirstSessionDates,
) -> uuid.UUID:
    """A new rate version; ``409 compensation_rate_exists`` on a taken date
    for this class kind, then ``422 effective_from_under_finalized_line``
    (#110 E5, module docstring)."""
    await lock_unfinalized_periods_of_tutor(session, tutor_user_id)
    version = CompensationRate(
        tutor_user_id=tutor_user_id,
        class_kind=class_kind,
        effective_from=effective_from,
        rate_model=rate_model,
        rate=rate,
    )
    try:
        async with session.begin_nested():
            session.add(version)
            await session.flush()
    except IntegrityError as exc:
        if RATE_KEY in str(exc.orig):
            raise CompensationRateExistsError() from None
        raise
    until = await _next_rate_date(session, tutor_user_id, class_kind, effective_from)
    latest = (
        await _latest_repriced_course_date(
            session,
            tutor_user_id,
            rate_model,
            rate,
            effective_from,
            until,
            first_session_dates,
        )
        if class_kind == COURSE
        else await latest_finalized_priced_date(
            session,
            tutor_user_id,
            class_kind=class_kind,
            effective_from=effective_from,
            until=until,
            rate_model=rate_model,
            rate=rate,
        )
    )
    if latest is not None:
        raise EffectiveFromUnderFinalizedLineError(latest)
    return version.id


async def _latest_repriced_course_date(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    rate_model: str | None,
    rate: Rate | None,
    effective_from: date,
    until: date | None,
    first_session_dates: FirstSessionDates,
) -> date | None:
    """The newest date on which this version would price a finalized ``course``
    line, or ``None``. A session with no cohort is its own first date."""

    def rule(line: FinalizedPricedLine, first: date) -> date | None:
        return repriced_on(
            line,
            first_session_date=first,
            new_model=rate_model,
            new_rate=rate,
            effective_from=effective_from,
            until=until,
        )

    return await _latest_course_date(session, tutor_user_id, first_session_dates, rule)


async def _latest_course_date(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    first_session_dates: FirstSessionDates,
    rule: Callable[[FinalizedPricedLine, date], date | None],
) -> date | None:
    """The newest date ``rule`` answers over the tutor's finalized ``course``
    prices (lines and posted adjustments), or ``None``. A session with no cohort
    is its own first date. Three statements, one with no price."""
    lines = await finalized_course_kind_lines(session, tutor_user_id)
    if not lines:
        return None
    first = await first_session_dates([line.class_session_id for line in lines])
    return max(
        (
            day
            for line in lines
            if (day := rule(line, first.get(line.class_session_id, line.service_date)))
            is not None
        ),
        default=None,
    )


async def _next_rate_date(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    class_kind: str,
    effective_from: date,
) -> date | None:
    """When the version after ``effective_from`` starts. One statement."""
    return await session.scalar(
        select(func.min(CompensationRate.effective_from)).where(
            CompensationRate.tutor_user_id == tutor_user_id,
            CompensationRate.class_kind == class_kind,
            CompensationRate.effective_from > effective_from,
        )
    )


async def delete_pay_terms(
    session: AsyncSession, tutor_user_id: uuid.UUID, terms_id: uuid.UUID
) -> None:
    """Delete a terms version of this tutor, whatever its date (#105 E4), with
    its lines in ``open`` periods (#108 F, module docstring): ``404`` for a row
    of another tutor, the #106 refusals, then ``409 pay_terms_in_use`` for a
    version a prepared or finalized period uses."""
    await lock_payroll_periods(session)
    period_ids = await lock_open_periods_of_tutor(session, tutor_user_id)
    terms = await session.scalar(
        select(TutorPayTerms)
        .where(
            TutorPayTerms.id == terms_id,
            TutorPayTerms.tutor_user_id == tutor_user_id,
        )
        .with_for_update()
    )
    if terms is None:
        raise RecordNotFoundError()
    before = await terms_versions(session, tutor_user_id)
    await _refuse_against_periods(
        session,
        tutor_user_id,
        [v for v in before if v.effective_from != terms.effective_from],
    )
    await delete_lines_of_terms(session, terms_id, period_ids)
    await session.delete(terms)
    try:
        await session.flush()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == INSUFFICIENT_PRIVILEGE or state in FOREIGN_KEY_REFUSALS:
            raise PayTermsInUseError() from None
        raise


async def delete_compensation_rate(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    rate_id: uuid.UUID,
    *,
    first_session_dates: FirstSessionDates,
) -> None:
    """Delete a rate version of this tutor, whatever its date (#105 E4), with
    its lines in ``open`` periods (#107 E8, module docstring). Order (M7-08d
    B1): the not-finalized periods ``FOR UPDATE``, oldest first, then the
    version: ``404`` for a row of another tutor, ``409
    compensation_rate_in_use`` for a version a prepared or finalized period or
    an adjustment uses, then ``409 compensation_rate_delete_under_finalized_line``
    (M7-08d E3), then the delete."""
    period_ids = await lock_unfinalized_periods_of_tutor(session, tutor_user_id)
    version = await session.scalar(
        select(CompensationRate)
        .where(
            CompensationRate.id == rate_id,
            CompensationRate.tutor_user_id == tutor_user_id,
        )
        .with_for_update()
    )
    if version is None:
        raise RecordNotFoundError()
    class_kind, deleted_from = version.class_kind, version.effective_from
    await delete_lines_of_rate(session, rate_id, period_ids)
    await session.delete(version)
    try:
        await session.flush()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == INSUFFICIENT_PRIVILEGE or state in FOREIGN_KEY_REFUSALS:
            raise CompensationRateInUseError() from None
        raise
    # The version is gone in this transaction: the refusal reads the history
    # without it, and a refusal rolls the delete back with the Unit of Work.
    latest = await _latest_repriced_by_delete(
        session,
        tutor_user_id,
        class_kind,
        deleted_from,
        first_session_dates,
    )
    if latest is not None:
        raise CompensationRateDeleteUnderFinalizedLineError(latest)


async def _latest_repriced_by_delete(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    class_kind: str,
    deleted_from: date,
    first_session_dates: FirstSessionDates,
) -> date | None:
    """The newest date on which the deleted window, now the previous version's,
    changes a finalized price (M7-08d E3, E4). Two statements, more for
    ``course``."""
    until = await _next_rate_date(session, tutor_user_id, class_kind, deleted_from)
    previous = await _previous_rate_version(
        session, tutor_user_id, class_kind, deleted_from
    )
    if class_kind != COURSE:
        return await latest_finalized_priced_date(
            session,
            tutor_user_id,
            class_kind=class_kind,
            effective_from=deleted_from,
            until=until,
            rate_model=None if previous is None else previous.rate_model,
            rate=None if previous is None else previous.rate,
        )

    def rule(line: FinalizedPricedLine, first: date) -> date | None:
        return repriced_by_delete(
            line,
            first_session_date=first,
            previous=previous,
            deleted_from=deleted_from,
            until=until,
        )

    return await _latest_course_date(session, tutor_user_id, first_session_dates, rule)


async def _previous_rate_version(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    class_kind: str,
    before: date,
) -> RateVersion | None:
    """The version of the kind in force the day before ``before``. One
    statement."""
    row = (
        await session.execute(
            _rate_version_columns()
            .where(
                CompensationRate.tutor_user_id == tutor_user_id,
                CompensationRate.class_kind == class_kind,
                CompensationRate.effective_from < before,
            )
            .order_by(None)
            .order_by(CompensationRate.effective_from.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else _rate_version(row)


# --- Lookups -------------------------------------------------------------------------


async def rate_in_effect(
    session: AsyncSession, tutor_user_id: uuid.UUID, class_kind: str, on: date
) -> RateInEffect | None:
    """The rate of this class kind on ``on`` (see the module docstring)."""
    version = await session.scalar(
        select(CompensationRate)
        .where(
            CompensationRate.tutor_user_id == tutor_user_id,
            CompensationRate.class_kind == class_kind,
            CompensationRate.effective_from <= on,
        )
        .order_by(CompensationRate.effective_from.desc())
        .limit(1)
    )
    if version is None or version.rate is None or version.rate_model is None:
        return None
    return RateInEffect(
        compensation_rate_id=version.id,
        rate_model=version.rate_model,
        rate=version.rate,
        effective_from=version.effective_from,
    )


async def terms_in_effect(
    session: AsyncSession, tutor_user_id: uuid.UUID, on: date
) -> TermsInEffect | None:
    """The terms on ``on`` (see the module docstring)."""
    terms = await session.scalar(
        select(TutorPayTerms)
        .where(
            TutorPayTerms.tutor_user_id == tutor_user_id,
            TutorPayTerms.effective_from <= on,
        )
        .order_by(TutorPayTerms.effective_from.desc())
        .limit(1)
    )
    if terms is None or terms.pay_frequency is None:
        return None
    return TermsInEffect(
        tutor_pay_terms_id=terms.id,
        pay_frequency=terms.pay_frequency,
        salary_amount=terms.salary_amount,
        effective_from=terms.effective_from,
    )


def _rate_version_columns() -> Select[*tuple[Any, ...]]:
    return select(
        CompensationRate.id,
        CompensationRate.tutor_user_id,
        CompensationRate.class_kind,
        CompensationRate.effective_from,
        CompensationRate.rate_model,
        CompensationRate.rate,
        CompensationRate.created_at,
    ).order_by(CompensationRate.class_kind, CompensationRate.effective_from)


def _rate_version(row: Row[Any]) -> RateVersion:
    return RateVersion(
        id=row.id,
        class_kind=row.class_kind,
        effective_from=row.effective_from,
        rate_model=row.rate_model,
        rate=row.rate,
        created_at=row.created_at,
    )


async def rate_versions(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[RateVersion]:
    """Every rate version of the tutor, ``FOR KEY SHARE`` (module docstring).
    One statement."""
    rows = await session.execute(
        _rate_version_columns()
        .where(CompensationRate.tutor_user_id == tutor_user_id)
        .with_for_update(read=True, key_share=True)
    )
    return [_rate_version(row) for row in rows]


async def rate_versions_of_tutors(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[RateVersion]]:
    """Every rate version of these tutors, by tutor. It writes no line, so it
    takes no lock (#443). One statement, none for no ids."""
    versions: dict[uuid.UUID, list[RateVersion]] = {}
    if tutor_user_ids:
        rows = await session.execute(
            _rate_version_columns().where(
                CompensationRate.tutor_user_id.in_(set(tutor_user_ids))
            )
        )
        for row in rows:
            versions.setdefault(row.tutor_user_id, []).append(_rate_version(row))
    return versions


def _terms_basis_columns() -> Select[*tuple[Any, ...]]:
    return select(
        TutorPayTerms.id,
        TutorPayTerms.tutor_user_id,
        TutorPayTerms.effective_from,
        TutorPayTerms.pay_frequency,
        TutorPayTerms.salary_amount,
    ).order_by(TutorPayTerms.effective_from)


def _terms_basis_version(row: Row[Any]) -> TermsBasisVersion:
    return TermsBasisVersion(
        id=row.id,
        effective_from=row.effective_from,
        pay_frequency=row.pay_frequency,
        salary=row.salary_amount,
    )


async def terms_basis_versions(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[TermsBasisVersion]:
    """Every terms version of the tutor, ``FOR KEY SHARE`` (module docstring).
    One statement."""
    rows = await session.execute(
        _terms_basis_columns()
        .where(TutorPayTerms.tutor_user_id == tutor_user_id)
        .with_for_update(read=True, key_share=True)
    )
    return [_terms_basis_version(row) for row in rows]


async def terms_basis_versions_of_tutors(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[TermsBasisVersion]]:
    """Every terms version of these tutors, by tutor. It writes no line, so it
    takes no lock (#443). One statement, none for no ids."""
    versions: dict[uuid.UUID, list[TermsBasisVersion]] = {}
    if tutor_user_ids:
        rows = await session.execute(
            _terms_basis_columns().where(
                TutorPayTerms.tutor_user_id.in_(set(tutor_user_ids))
            )
        )
        for row in rows:
            versions.setdefault(row.tutor_user_id, []).append(_terms_basis_version(row))
    return versions


# --- Reads ---------------------------------------------------------------------------


def _statuses(
    versions: Sequence[tuple[Hashable, date]], today: date
) -> list[CompensationStatus]:
    """The status of each ``(key, effective_from)``, newest first within a
    key: the first one on or before ``today`` is ``current``."""
    seen: set[Hashable] = set()
    statuses: list[CompensationStatus] = []
    for key, effective_from in versions:
        if effective_from > today:
            statuses.append("upcoming")
        elif key in seen:
            statuses.append("past")
        else:
            seen.add(key)
            statuses.append("current")
    return statuses


async def list_pay_terms(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, today: date
) -> list[PayTermsItem]:
    """The tutor's terms versions, newest first."""
    rows = list(
        await session.scalars(
            select(TutorPayTerms)
            .where(TutorPayTerms.tutor_user_id == tutor_user_id)
            .order_by(TutorPayTerms.effective_from.desc())
        )
    )
    statuses = _statuses([(None, row.effective_from) for row in rows], today)
    return [
        PayTermsItem(
            id=row.id,
            effective_from=row.effective_from,
            pay_frequency=cast(PayFrequency | None, row.pay_frequency),
            salary_amount=row.salary_amount,
            status=status,
        )
        for row, status in zip(rows, statuses, strict=True)
    ]


async def list_compensation_rates(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, today: date
) -> list[CompensationRateItem]:
    """The tutor's rate versions, by class kind (individual, group, course),
    then newest first."""
    rows = sorted(
        await session.scalars(
            select(CompensationRate)
            .where(CompensationRate.tutor_user_id == tutor_user_id)
            .order_by(CompensationRate.effective_from.desc())
        ),
        key=lambda row: CLASS_KINDS.index(row.class_kind),
    )
    statuses = _statuses([(row.class_kind, row.effective_from) for row in rows], today)
    return [
        CompensationRateItem(
            id=row.id,
            class_kind=cast(ClassKind, row.class_kind),
            effective_from=row.effective_from,
            rate_model=cast(RateModel | None, row.rate_model),
            rate=row.rate,
            status=status,
        )
        for row, status in zip(rows, statuses, strict=True)
    ]


async def compensation_summaries(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID], *, today: date
) -> dict[uuid.UUID, CompensationSummary]:
    """Each tutor's terms and rated class kinds in effect ``today``; a tutor
    with neither is missing. Two statements, whatever the number of tutors."""
    ids = list(tutor_user_ids)
    terms = await session.scalars(
        select(TutorPayTerms)
        .where(TutorPayTerms.tutor_user_id.in_(ids))
        .where(TutorPayTerms.effective_from <= today)
        .ext(distinct_on(TutorPayTerms.tutor_user_id))
        .order_by(TutorPayTerms.tutor_user_id, TutorPayTerms.effective_from.desc())
    )
    current = {
        row.tutor_user_id: CurrentTerms(
            pay_frequency=cast(PayFrequency, row.pay_frequency),
            salary_amount=row.salary_amount,
        )
        for row in terms
        if row.pay_frequency is not None
    }
    rates = await session.scalars(
        select(CompensationRate)
        .where(CompensationRate.tutor_user_id.in_(ids))
        .where(CompensationRate.effective_from <= today)
        .ext(distinct_on(CompensationRate.tutor_user_id, CompensationRate.class_kind))
        .order_by(
            CompensationRate.tutor_user_id,
            CompensationRate.class_kind,
            CompensationRate.effective_from.desc(),
        )
    )
    kinds: dict[uuid.UUID, list[str]] = {}
    for row in rates:
        if row.rate is not None:
            kinds.setdefault(row.tutor_user_id, []).append(row.class_kind)
    return {
        tutor_id: CompensationSummary(
            current_terms=current.get(tutor_id),
            current_rate_kinds=tuple(
                kind for kind in CLASS_KINDS if kind in kinds.get(tutor_id, ())
            ),
        )
        for tutor_id in current.keys() | kinds.keys()
    }
