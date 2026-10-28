"""Shared helpers of the session charge tests (#91 F1-F15).

The sweeper and the unbilled list read **every** candidate of the database,
so each test starts from :func:`reset`: billing off, and every earlier
uncharged billable session of the module database cancelled (raw SQL, as a
test fixture may), so the counts of a run are the test's own.

Times are in the center zone (Mexico City, the seeded default: UTC-6, no
DST). :func:`item` builds one individual session with one participant and,
by default, a family and an any-level, any-duration ``per_session`` price
for its subject.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import service as billing
from app.modules.families.models import Family
from app.modules.scheduling.service import SessionKind
from app.workflows import session_charges
from tests.factories.billing import persist_service_price
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")
FLOOR = date(2031, 3, 1)
PRICE_FROM = date(2030, 1, 1)


def local(day: date, hour: int = 10, minute: int = 0) -> datetime:
    """The UTC instant of a center-local wall time."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).astimezone(
        UTC
    )


def reset(url: str) -> None:
    """Billing off; earlier uncharged billable sessions cancelled."""
    set_floor(url, None)
    run_sql(
        url,
        "UPDATE class_sessions s SET state = 'cancelled', cancelled_at = now(),"
        " cancellation_reason = 'Reinicio de prueba'"
        " WHERE s.state IN ('confirmed', 'completed') AND EXISTS ("
        "  SELECT 1 FROM class_session_participants p"
        "  WHERE p.class_session_id = s.id AND NOT EXISTS ("
        "   SELECT 1 FROM charges c WHERE c.kind = 'session'"
        "   AND c.class_session_id = p.class_session_id"
        "   AND c.student_id = p.student_id))",
    )


def set_floor(url: str, floor: date | None) -> None:
    run_sql(
        url,
        "UPDATE billing_settings SET billing_starts_on = :d, version = version + 1",
        {"d": floor},
    )


def run(url: str) -> session_charges.SessionChargeRunResult:
    return asyncio.run(session_charges.run_post_session_charges(settings_for(url)))


@dataclass(frozen=True)
class Item:
    session: uuid.UUID
    student: uuid.UUID
    subject: uuid.UUID
    level: uuid.UUID
    """The subject's level (the student's too, unless ``level=False``)."""
    family: uuid.UUID | None
    price: uuid.UUID | None
    """The ``service_prices`` item, when one was created."""


async def _family(session: AsyncSession, *students: Any) -> uuid.UUID:
    family = FamilyFactory.build()
    session.add(family)
    await session.flush()
    await add_to_family(session, family, *students)
    return family.id


def item(
    url: str,
    *,
    starts_at: datetime,
    minutes: int = 60,
    state: str = "confirmed",
    kind: str = SessionKind.INDIVIDUAL.value,
    family: bool = True,
    level: bool = True,
    subject: uuid.UUID | None = None,
    price: str | None = "250.00",
    versions: Sequence[tuple[date, str | None]] | None = None,
    student: uuid.UUID | None = None,
) -> Item:
    """One session with one participant (see the module docstring).
    ``price=None`` and no ``versions``: no catalog item."""

    async def _make(session: AsyncSession) -> Item:
        from app.modules.catalog.models import Subject
        from app.modules.families.models import Student

        if subject is None:
            subject_row = await persist_subject(session)
        else:
            found = await session.get(Subject, subject)
            assert found is not None
            subject_row = found
        price_item = None
        wanted = (
            versions
            if versions is not None
            else ([] if price is None else [(PRICE_FROM, price)])
        )
        if wanted:
            price_item = (
                await persist_service_price(
                    session, subject_id=subject_row.id, versions=wanted
                )
            ).id
        if student is None:
            student_row = await persist_student(
                session,
                contacts=0,
                education_level_id=subject_row.education_level_id if level else None,
            )
        else:
            loaded = await session.get(Student, student)
            assert loaded is not None
            student_row = loaded
        family_id = await _family(session, student_row) if family else None
        extra: dict[str, Any] = {}
        if state == "skipped_closure":
            # Confirmed once, with its tutor (so it can be restored).
            extra["first_confirmed_at"] = starts_at - timedelta(days=7)
            extra["tutor"] = True
        class_session = await persist_class_session(
            session,
            subject_id=subject_row.id,
            kind=kind,
            state=state,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            **extra,
        )
        await add_participant(session, class_session, student_row.id)
        return Item(
            session=class_session.id,
            student=student_row.id,
            subject=subject_row.id,
            level=subject_row.education_level_id,
            family=family_id,
            price=price_item,
        )

    return in_uow(url, _make)


def add_family(url: str, student: uuid.UUID) -> uuid.UUID:
    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Student

        loaded = await session.get(Student, student)
        assert loaded is not None
        return await _family(session, loaded)

    return in_uow(url, _add)


def family_name(url: str, family: uuid.UUID) -> str:
    async def _name(session: AsyncSession) -> str:
        row = await session.get(Family, family)
        assert row is not None
        return row.name

    return in_uow(url, _name)


def add_version(url: str, price_item: uuid.UUID, on: date, amount: str | None) -> None:
    run_sql(
        url,
        "INSERT INTO price_versions (service_price_id, effective_from, amount)"
        " VALUES (:p, :d, :a)",
        {"p": price_item, "d": on, "a": amount},
    )


def add_price(
    url: str,
    subject: uuid.UUID,
    versions: Sequence[tuple[date, str | None]],
    **key: Any,
) -> uuid.UUID:
    async def _add(session: AsyncSession) -> uuid.UUID:
        return (
            await persist_service_price(
                session, subject_id=subject, versions=versions, **key
            )
        ).id

    return in_uow(url, _add)


def set_offset(url: str, family: uuid.UUID, days: int) -> uuid.UUID:
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, family))
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = :d WHERE id = :id",
        {"d": days, "id": account},
    )
    return account


def charges_of(url: str, class_session: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charges WHERE kind = 'session' AND class_session_id = :s"
        " ORDER BY posted_at",
        {"s": class_session},
    )


def post_one(url: str, it: Item) -> session_charges.ItemOutcome:
    return in_uow(
        url,
        lambda s: session_charges.post_one(
            s, it.session, it.student, zone="America/Mexico_City"
        ),
    )
