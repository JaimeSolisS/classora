"""Tutor compensation: versions and lookups (#105; ADR-0017 E1, data-model
§11.2, §11.3).

The routes are ``app.workflows.payroll_compensation_router``; the workflow
(``app.workflows.payroll_compensation``) reads the tutor and the center's
local ``today``. Every function runs in the caller's Unit of Work and never
commits.

**Writes.** A version is inserted or deleted, never updated (#105 E4): a
correction is a delete and a new insert. :func:`add_pay_terms` and
:func:`add_compensation_rate` accept any date, past or future, also between
two versions. They insert in a savepoint and map the unique key by name, so a
concurrent identical insert is the same ``409``. :func:`delete_pay_terms` and
:func:`delete_compensation_rate` map the guard's 42501 (a finalized line uses
the row) to ``409 …_in_use``. #106 adds its frequency refusal to the terms
insert and delete here (#105 E7).

**Lookups.** :func:`rate_in_effect` and :func:`terms_in_effect` return the
version with the greatest ``effective_from <= on`` (W3.4), or ``None`` when
that version is a ``NULL`` one (no fall-through to an earlier version) or
there is none. They take a plain date, and they don't check the tutor's role
or state, because the calculation must find a rate for a tutor who left. The
caller passes the service date or, for ``per_course``, the cohort's first
session date (ADR-0017 E3).
"""

import uuid
from collections.abc import Collection, Hashable, Sequence
from datetime import date
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, Rate
from app.modules.payroll.errors import (
    CompensationRateExistsError,
    CompensationRateInUseError,
    PayTermsExistsError,
    PayTermsInUseError,
)
from app.modules.payroll.models import (
    CLASS_KINDS,
    RATE_KEY,
    TERMS_KEY,
    CompensationRate,
    TutorPayTerms,
)
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

INSUFFICIENT_PRIVILEGE: Final = "42501"


# --- Writes --------------------------------------------------------------------------


async def add_pay_terms(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    effective_from: date,
    pay_frequency: str | None,
    salary_amount: Money | None,
) -> uuid.UUID:
    """A new terms version; ``409 pay_terms_exists`` on a taken date."""
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
) -> uuid.UUID:
    """A new rate version; ``409 compensation_rate_exists`` on a taken date
    for this class kind."""
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
    return version.id


async def delete_pay_terms(
    session: AsyncSession, tutor_user_id: uuid.UUID, terms_id: uuid.UUID
) -> None:
    """Delete a terms version of this tutor, whatever its date (#105 E4);
    ``404`` for a row of another tutor, ``409 pay_terms_in_use`` for a used
    one."""
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
    await session.delete(terms)
    try:
        await session.flush()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE:
            raise PayTermsInUseError() from None
        raise


async def delete_compensation_rate(
    session: AsyncSession, tutor_user_id: uuid.UUID, rate_id: uuid.UUID
) -> None:
    """Delete a rate version of this tutor, whatever its date (#105 E4);
    ``404`` for a row of another tutor, ``409 compensation_rate_in_use`` for
    a used one."""
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
    await session.delete(version)
    try:
        await session.flush()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE:
            raise CompensationRateInUseError() from None
        raise


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
