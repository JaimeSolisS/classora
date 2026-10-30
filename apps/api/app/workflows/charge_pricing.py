"""Pieces shared by the session and enrollment charge workflows (#91, #92).

:mod:`app.workflows.session_charges` (``billing:post_session_charges``) and
:mod:`app.workflows.enrollment_charges` (``billing:post_enrollment_charges``)
both import this module; it imports neither, so the two can use each other's
rules without an import cycle:

- :func:`local_date` and :func:`floor_instant` (ADR-0004: local dates in the
  center zone);
- :class:`PriceKey` and :class:`PriceMemo`: base prices within one run or
  read (#95 C2, C3), memoized per catalog key, custom prices batched once;
- :class:`Period` (a group or cohort enrollment period),
  :func:`fee_price_query` (its ``full_course`` fee, #86 E9) and
  :func:`period_facts` (the charges a period's classification needs, J2 and
  J5, and whether a tuition plan is linked to it, #93 K6, for many periods
  in one statement per kind).

Everything runs in the caller's Unit of Work: no lock, no write.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.courses import service as courses
from app.modules.scheduling import service as scheduling

type EnrollmentKind = Literal["group_enrollment", "cohort_enrollment"]


def local_date(instant: datetime, zone: str) -> date:
    """The center-local date of ``instant`` (ADR-0004)."""
    return instant.astimezone(ZoneInfo(zone)).date()


def floor_instant(floor: date, zone: str) -> datetime:
    """The instant of the floor's local midnight in the center zone."""
    return datetime.combine(floor, time(0), tzinfo=ZoneInfo(zone)).astimezone(UTC)


# --- Base prices ---------------------------------------------------------------------


@dataclass(frozen=True)
class PriceKey:
    """A catalog lookup (#91 C5 step 5; #92 adds the group sessions'
    ``group_id`` and the enrollment fees' scopes, with no duration)."""

    subject_id: uuid.UUID | None
    education_level_id: uuid.UUID | None
    duration_minutes: int | None
    on: date
    class_type: str = "individual"
    pricing_model: str = "per_session"
    group_id: uuid.UUID | None = None
    cohort_id: uuid.UUID | None = None

    @classmethod
    def of(cls, query: billing.PriceQuery) -> PriceKey:
        """The catalog part of a :class:`~app.modules.billing.schemas.
        PriceQuery` (no student, family or charge kind)."""
        return cls(
            subject_id=query.subject_id,
            education_level_id=query.education_level_id,
            duration_minutes=query.duration_minutes,
            on=query.on,
            class_type=query.class_type,
            pricing_model=query.pricing_model,
            group_id=query.group_id,
            cohort_id=query.cohort_id,
        )


@dataclass
class PriceMemo:
    """Base prices within one run or read (#95 C2, C3). After
    :meth:`prefetch` the custom prices of the given queries come from one
    batched statement and the catalog lookups are memoized per
    :class:`PriceKey`; without it each query is one
    ``billing.service.base_price``."""

    session: AsyncSession
    custom: Mapping[billing.PriceQuery, billing.BasePrice] | None = None
    found: dict[PriceKey, billing.PriceInEffect | None] = field(default_factory=dict)

    async def prefetch(self, queries: Sequence[billing.PriceQuery]) -> None:
        """Read the custom prices of ``queries`` in one statement."""
        self.custom = await billing.custom_prices_for(self.session, queries)

    async def lookup(self, key: PriceKey) -> billing.PriceInEffect | None:
        if key not in self.found:
            assert key.subject_id is not None  # noqa: S101 - unpinned keys have one
            self.found[key] = await billing.lookup_price(
                self.session,
                class_type=key.class_type,
                pricing_model=key.pricing_model,
                subject_id=key.subject_id,
                on=key.on,
                education_level_id=key.education_level_id,
                duration_minutes=key.duration_minutes,
                group_id=key.group_id,
                cohort_id=key.cohort_id,
            )
        return self.found[key]

    async def base(
        self, query: billing.PriceQuery, key: PriceKey | None = None
    ) -> billing.BasePrice | None:
        if self.custom is None:
            return await billing.base_price(self.session, query)
        custom = self.custom.get(query)
        if custom is not None:
            return custom
        price = await self.lookup(PriceKey.of(query) if key is None else key)
        return None if price is None else billing.BasePrice.of_catalog(price)


# --- Enrollment periods --------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    """A group (``group_enrollment``) or cohort (``cohort_enrollment``)
    enrollment period, with its owner's subject."""

    kind: EnrollmentKind
    enrollment_id: uuid.UUID
    owner_id: uuid.UUID
    """The group or the cohort."""
    student_id: uuid.UUID
    subject_id: uuid.UUID
    enrolled_at: datetime
    withdrawn_at: datetime | None = None

    @property
    def is_group(self) -> bool:
        return self.kind == "group_enrollment"

    @property
    def owner(self) -> scheduling.SeriesOwner:
        if self.is_group:
            return scheduling.SeriesOwner.group(self.owner_id)
        return scheduling.SeriesOwner.cohort(self.owner_id)

    @classmethod
    def of_row(cls, kind: EnrollmentKind, row: Any) -> Period:
        """A period from a row of ``group_enrollment_columns`` /
        ``cohort_enrollment_columns``."""
        return cls(
            kind=kind,
            enrollment_id=row.enrollment_id,
            owner_id=row.owner_id,
            student_id=row.student_id,
            subject_id=row.subject_id,
            enrolled_at=row.enrolled_at.astimezone(UTC),
            withdrawn_at=(
                None if row.withdrawn_at is None else row.withdrawn_at.astimezone(UTC)
            ),
        )


def period_columns(kind: EnrollmentKind) -> Any:
    """The unexecuted period select of a kind (scheduling or courses)."""
    if kind == "group_enrollment":
        return scheduling.group_enrollment_columns()
    return courses.cohort_enrollment_columns()


def fee_price_query(
    period: Period,
    *,
    service_date: date,
    education_level_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
) -> billing.PriceQuery:
    """The fee of a period (#86 E9, #92 B1 step 4): ``course/full_course``
    with the cohort, or ``group/full_course`` with the group; the owner's
    subject, ``on`` = the enrollment's local date, the student's current
    level, no duration."""
    return billing.PriceQuery(
        student_id=period.student_id,
        family_id=family_id,
        charge_kind=period.kind,
        class_type="group" if period.is_group else "course",
        pricing_model="full_course",
        subject_id=period.subject_id,
        on=service_date,
        education_level_id=education_level_id,
        duration_minutes=None,
        group_id=period.owner_id if period.is_group else None,
        cohort_id=None if period.is_group else period.owner_id,
    )


@dataclass(frozen=True)
class PeriodFacts:
    """What a period's classification reads from the ledger (#92 B1):

    - ``fee_posted``: the period has its fee charge (step 1);
    - ``session_charged``: a group period has a ``session`` charge of the
      student on a group session starting at or after its ``enrolled_at``,
      whatever the charge's net after removal credits and including
      sessions after ``withdrawn_at`` (step 3, J2 as refined by the
      owner-delegated QA decision on #92: once charged per session, a
      period is never fee-charged);
    - ``other_fee_positive``: another period of the student in the same
      group or cohort has a fee charge with a net above ``0.00`` (step 5,
      J5);
    - ``tuition_linked``: a tuition plan is linked to the group period
      (#93 K6, any window): the period is ``tuition_priced``, never
      fee-charged;
    - ``withdrawal_settled``: a #101 withdrawal settled the period (S6): its
      fee is never posted afterwards."""

    fee_posted: bool = False
    session_charged: bool = False
    other_fee_positive: bool = False
    tuition_linked: bool = False
    withdrawal_settled: bool = False


async def period_facts(
    session: AsyncSession, periods: Sequence[Period]
) -> dict[uuid.UUID, PeriodFacts]:
    """:class:`PeriodFacts` by enrollment id: one statement per kind present
    (none for no period). No lock: the posting re-reads it under the account
    lock (B4)."""
    found: dict[uuid.UUID, PeriodFacts] = {}
    for kind in ("group_enrollment", "cohort_enrollment"):
        ids = [p.enrollment_id for p in periods if p.kind == kind]
        if ids:
            found.update(await _facts_of_kind(session, kind, ids))
    return found


async def _facts_of_kind(
    session: AsyncSession, kind: EnrollmentKind, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PeriodFacts]:
    periods = period_columns(kind).subquery("periods")
    others = period_columns(kind).subquery("others")
    fees = billing.enrollment_fee_columns().subquery("fees")
    own_fee = (
        select(fees.c.charge_id)
        .where(fees.c.kind == kind, fees.c.enrollment_id == periods.c.enrollment_id)
        .exists()
    )
    other_fee = (
        select(fees.c.charge_id)
        .join(others, others.c.enrollment_id == fees.c.enrollment_id)
        .where(
            fees.c.kind == kind,
            fees.c.net > Money.zero(),
            others.c.owner_id == periods.c.owner_id,
            others.c.student_id == periods.c.student_id,
            others.c.enrollment_id != periods.c.enrollment_id,
        )
        .exists()
    )
    settled_rows = billing.withdrawal_subject_columns().subquery("settled")
    settled_column = (
        settled_rows.c.group_enrollment_id
        if kind == "group_enrollment"
        else settled_rows.c.cohort_enrollment_id
    )
    settled = (
        select(settled_rows.c.id)
        .where(settled_column == periods.c.enrollment_id)
        .exists()
    )
    columns: list[Any] = [
        periods.c.enrollment_id,
        own_fee.label("fee_posted"),
        other_fee.label("other_fee_positive"),
        settled.label("withdrawal_settled"),
    ]
    if kind == "group_enrollment":
        posted = billing.session_charge_key_columns().subquery("posted")
        group_sessions = scheduling.owner_session_columns(counted_only=False).subquery(
            "group_sessions"
        )
        session_charged = (
            select(posted.c.class_session_id)
            .join(group_sessions, group_sessions.c.id == posted.c.class_session_id)
            .where(
                posted.c.student_id == periods.c.student_id,
                group_sessions.c.group_id == periods.c.owner_id,
                # Not bounded by withdrawn_at: a withdrawal credits the
                # charges, and the period stays session-priced (QA #92 D1).
                group_sessions.c.starts_at >= periods.c.enrolled_at,
            )
            .exists()
        )
        columns.append(session_charged.label("session_charged"))
        plans = billing.linked_plan_columns().subquery("linked_plans")
        linked = (
            select(plans.c.id)
            .where(plans.c.group_enrollment_id == periods.c.enrollment_id)
            .exists()
        )
        columns.append(linked.label("tuition_linked"))
    rows = await session.execute(
        select(*columns).where(periods.c.enrollment_id.in_(list(ids)))
    )
    return {
        row.enrollment_id: PeriodFacts(
            fee_posted=bool(row.fee_posted),
            session_charged=bool(getattr(row, "session_charged", False)),
            other_fee_positive=bool(row.other_fee_positive),
            tuition_linked=bool(getattr(row, "tuition_linked", False)),
            withdrawal_settled=bool(row.withdrawal_settled),
        )
        for row in rows
    }
