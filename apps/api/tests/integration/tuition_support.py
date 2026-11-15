"""Shared helpers of the tuition plan tests (#93 H2-H9).

**Isolation.** The daily job and the unbilled read look at **every** plan of
the module database. :func:`reset` runs first in each test: billing off,
every earlier plan without a charge deleted, and every earlier plan with
charges ended at its last posted period (raw SQL, as a fixture may; the
key trigger allows ``ends_on``), so a run's counts are the test's own.

**Dates** are center-local (Mexico City by default, UTC-6, no DST). The job
takes ``now`` (:func:`run`), the routes the frozen clock.
"""

import asyncio
import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import service as billing
from app.workflows import tuition_charges
from tests.factories.billing import persist_service_price
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.scheduling import persist_subject
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow
from tests.integration.session_charge_support import set_floor

__all__ = ["set_floor"]

MX = ZoneInfo("America/Mexico_City")
PLANS = "/api/v1/billing/tuition-plans"
UNBILLED = "/api/v1/billing/unbilled-tuition-periods"
PRICE_FROM = date(2020, 1, 1)

YEARS = itertools.count(2031, 2)
"""Years for the group tests' timelines (each test takes two)."""


def local(day: date, hhmm: str = "12:00") -> datetime:
    """The UTC instant of a center-local wall time (Mexico City)."""
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=MX).astimezone(UTC)


def reset(url: str) -> None:
    """Billing off; earlier plans deleted or ended (module docstring)."""
    set_floor(url, None)
    run_sql(
        url,
        "DELETE FROM tuition_plans p WHERE NOT EXISTS ("
        " SELECT 1 FROM charges c WHERE c.tuition_plan_id = p.id)",
    )
    run_sql(
        url,
        "UPDATE tuition_plans p SET ends_on = last.through, version = version + 1"
        " FROM (SELECT tuition_plan_id, max(service_period_end) AS through"
        "  FROM charges WHERE kind = 'tuition_period' GROUP BY tuition_plan_id)"
        " AS last WHERE last.tuition_plan_id = p.id"
        " AND (p.ends_on IS NULL OR p.ends_on > last.through)",
    )


def run(url: str, now: datetime) -> tuition_charges.TuitionChargeRunResult:
    return asyncio.run(
        tuition_charges.run_post_tuition_charges(settings_for(url), now=now)
    )


@dataclass(frozen=True)
class World:
    """A student (with the subject's level) in a family, and a
    ``per_period`` item of the subject."""

    student: uuid.UUID
    family: uuid.UUID | None
    subject: uuid.UUID
    level: uuid.UUID
    item: uuid.UUID


async def new_family(session: AsyncSession, *students: Any) -> uuid.UUID:
    family = FamilyFactory.build()
    session.add(family)
    await session.flush()
    await add_to_family(session, family, *students)
    return family.id


def world(
    url: str,
    *,
    frequency: str = "monthly",
    amount: str | None = "1000.00",
    price_from: date = PRICE_FROM,
    family: bool = True,
    class_type: str = "individual",
    group_id: uuid.UUID | None = None,
    subject: uuid.UUID | None = None,
    student: uuid.UUID | None = None,
) -> World:
    async def _make(session: AsyncSession) -> World:
        from app.modules.catalog.models import Subject
        from app.modules.families.models import Student

        if subject is None:
            subject_row = await persist_subject(session)
        else:
            found = await session.get(Subject, subject)
            assert found is not None
            subject_row = found
        item = await persist_service_price(
            session,
            subject_id=subject_row.id,
            class_type=class_type,
            pricing_model="per_period",
            period_frequency=frequency,
            group_id=group_id,
            versions=[(price_from, amount)],
        )
        if student is None:
            student_row = await persist_student(
                session, contacts=0, education_level_id=subject_row.education_level_id
            )
        else:
            loaded = await session.get(Student, student)
            assert loaded is not None
            student_row = loaded
        family_id = await new_family(session, student_row) if family else None
        return World(
            student=student_row.id,
            family=family_id,
            subject=subject_row.id,
            level=subject_row.education_level_id,
            item=item.id,
        )

    return in_uow(url, _make)


def plan(
    url: str,
    w: World,
    *,
    starts_on: date,
    ends_on: date | None = None,
    due_offset_days: int = 0,
    group_enrollment_id: uuid.UUID | None = None,
    frequency: str | None = None,
) -> uuid.UUID:
    """A plan written through the billing layer (no route rules)."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        assert w.family is not None
        account = await billing.ensure_billing_account(session, w.family)
        item = await billing.per_period_item(session, w.item)
        assert item is not None
        return await billing.create_plan(
            session,
            billing_account_id=account,
            student_id=w.student,
            service_price_id=w.item,
            group_enrollment_id=group_enrollment_id,
            frequency=frequency or item.period_frequency,  # type: ignore[arg-type]
            starts_on=starts_on,
            ends_on=ends_on,
            due_offset_days=due_offset_days,
        )

    return in_uow(url, _make)


def pause(url: str, plan_id: uuid.UUID, starts_on: date, ends_on: date | None) -> None:
    async def _add(session: AsyncSession) -> None:
        await billing.add_pause(session, plan_id, starts_on=starts_on, ends_on=ends_on)

    in_uow(url, _add)


def charges(url: str, plan_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charges WHERE tuition_plan_id = :p ORDER BY service_date",
        {"p": plan_id},
    )


def starts(url: str, plan_id: uuid.UUID) -> list[date]:
    return [c.service_date for c in charges(url, plan_id)]


def add_version(url: str, item: uuid.UUID, on: date, amount: str | None) -> None:
    run_sql(
        url,
        "INSERT INTO price_versions (service_price_id, effective_from, amount)"
        " VALUES (:p, :d, :a)",
        {"p": item, "d": on, "a": amount},
    )


def set_zone(url: str, zone: str) -> None:
    run_sql(url, "UPDATE business_settings SET time_zone = :z", {"z": zone})


def move_to_new_family(url: str, student: uuid.UUID) -> uuid.UUID:
    run_sql(url, "DELETE FROM family_students WHERE student_id = :s", {"s": student})

    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Student

        loaded = await session.get(Student, student)
        assert loaded is not None
        return await new_family(session, loaded)

    return in_uow(url, _add)


def add_sibling(url: str, family: uuid.UUID) -> uuid.UUID:
    """Another active student in ``family``."""

    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Family

        found = await session.get(Family, family)
        assert found is not None
        sibling = await persist_student(session, contacts=0)
        await add_to_family(session, found, sibling)
        return sibling.id

    return in_uow(url, _add)
