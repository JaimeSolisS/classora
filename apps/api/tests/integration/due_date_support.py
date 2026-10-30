"""Shared helpers of the due-date, overdue and reminder tests (#97 I2-I6).

Times are in the center zone (Mexico City, the seeded default: UTC-6, no
DST). :func:`world` builds a family with students, guardian profiles (with
a login by default) and its billing account; :func:`charge` posts a manual
charge with a given due date; :func:`pay` records a payment allocated to
it (ORM rows, the #90 factories).

The reminder job and the overdue list read **every** account of the module
database, so the reminder tests start from :func:`reset`: billing on, and
every earlier charge marked as reminded in every stage (rows in the
append-only ``charge_reminders``, which a fixture may insert), so the
counts of a run are the test's own.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.families.models import Guardian
from app.modules.identity.permissions import GUARDIAN
from app.workflows import charge_reminders
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.factories.identity import persist_user
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")
TODAY = date(2031, 3, 10)
FLOOR = date(2030, 1, 1)


def local(day: date, hour: int = 9, minute: int = 0) -> datetime:
    """The UTC instant of a center-local wall time."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).astimezone(
        UTC
    )


@dataclass(frozen=True)
class World:
    """A family, its students, its guardians' logins and its account."""

    family: uuid.UUID
    students: tuple[uuid.UUID, ...]
    guardians: tuple[uuid.UUID, ...]
    """The users of the guardian profiles that have a login."""
    account: uuid.UUID
    actor: uuid.UUID
    """A user to record payments and due-date changes with."""

    @property
    def student(self) -> uuid.UUID:
        return self.students[0]


async def make_world(
    session: AsyncSession,
    *,
    students: int = 1,
    guardians: int = 1,
    login: bool = True,
    active: bool = True,
    name: str | None = None,
    family_active: bool = True,
) -> World:
    family = FamilyFactory.build(**({"name": name} if name else {}))
    family.is_active = family_active
    session.add(family)
    await session.flush()
    members: list[Any] = [
        await persist_student(session, contacts=0) for _ in range(students)
    ]
    users: list[uuid.UUID] = []
    for _ in range(guardians):
        user_id = (await persist_user(session, roles=(GUARDIAN,))).id if login else None
        profile = await persist(
            session, GuardianFactory, user_id=user_id, is_active=active
        )
        members.append(profile)
        if user_id is not None and active:
            users.append(user_id)
    await add_to_family(session, family, *members)
    account = await billing.ensure_billing_account(session, family.id)
    actor = await factories.staff_user_id(session)
    return World(
        family=family.id,
        students=tuple(m.id for m in members if not isinstance(m, Guardian)),
        guardians=tuple(users),
        account=account,
        actor=actor,
    )


def world(url: str, **options: Any) -> World:
    return in_uow(url, lambda s: make_world(s, **options))


def add_guardian(url: str, family: uuid.UUID) -> uuid.UUID:
    """A guardian profile with a login added to the family; its user id."""

    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Family

        found = await session.get(Family, family)
        assert found is not None
        user = await persist_user(session, roles=(GUARDIAN,))
        profile = await persist(session, GuardianFactory, user_id=user.id)
        await add_to_family(session, found, profile)
        return user.id

    return in_uow(url, _add)


def charge(
    url: str,
    student: uuid.UUID,
    amount: str = "100.00",
    *,
    due: date = TODAY,
    service_date: date | None = None,
) -> uuid.UUID:
    """A manual charge due on ``due`` (no family offset)."""

    async def _post(session: AsyncSession) -> uuid.UUID:
        posted = await factories.charge(
            session,
            student,
            amount,
            due=due,
            service_date=service_date or due,
        )
        return posted.charge_id

    return in_uow(url, _post)


def pay(url: str, w: World, charge_id: uuid.UUID, amount: str) -> None:
    """A payment of ``amount`` allocated to the charge (ORM rows)."""

    async def _pay(session: AsyncSession) -> None:
        paid = await factories.payment(session, w.account, amount, recorded_by=w.actor)
        await factories.allocation(session, paid, charge_id, amount)

    in_uow(url, _pay)


def credit(url: str, charge_id: uuid.UUID, amount: str) -> None:
    """A credit adjustment (negative ``amount``) through ``post_adjustment``."""
    in_uow(
        url,
        lambda s: billing.post_adjustment(
            s,
            billing.AdjustmentDraft(
                charge_id=charge_id,
                kind="cancellation_credit",
                amount=Money(amount),
                source_key=f"test:{uuid.uuid4()}",
            ),
        ),
    )


def change_due(
    url: str,
    w: World,
    charge_id: uuid.UUID,
    new: date,
    *,
    today: date = TODAY,
    reason: str = "Acuerdo con la familia",
) -> billing.RecordedDueDateChange:
    """A due-date change through the billing primitive, as ``w.actor``."""
    return in_uow(
        url,
        lambda s: billing.record_due_date_change(
            s, w.account, charge_id, due_date=new, reason=reason, today=today
        ),
        actor_id=w.actor,
    )


def set_floor(url: str, floor: date | None) -> None:
    run_sql(
        url,
        "UPDATE billing_settings SET billing_starts_on = :d, version = version + 1",
        {"d": floor},
    )


def reset(url: str) -> None:
    """Billing on; every earlier charge marked as reminded in every stage."""
    set_floor(url, FLOOR)
    run_sql(
        url,
        "INSERT INTO charge_reminders (charge_id, stage, due_date, sent_at)"
        " SELECT c.id, s.stage, c.due_date, now() FROM charges c"
        " CROSS JOIN unnest(ARRAY['due_soon', 'overdue', 'overdue_followup'])"
        "  AS s(stage)"
        " ON CONFLICT (charge_id, stage) DO NOTHING",
    )


def run(
    url: str, day: date = TODAY, hour: int = 9, minute: int = 0
) -> charge_reminders.ChargeReminderRunResult:
    """One reminder run at a center-local time of ``day``."""
    when = local(day, hour, minute)
    with frozen_clock(when):
        return asyncio.run(
            charge_reminders.run_send_charge_reminders(settings_for(url), now=when)
        )


def reminders(url: str, *charge_ids: uuid.UUID) -> list[tuple[uuid.UUID, str, date]]:
    """``(charge_id, stage, due_date)`` of the charges' reminder rows."""
    rows = run_sql(
        url,
        "SELECT charge_id, stage, due_date FROM charge_reminders"
        " WHERE charge_id = ANY(:ids) ORDER BY charge_id, stage",
        {"ids": list(charge_ids)},
    )
    return [(row.charge_id, row.stage, row.due_date) for row in rows]


def notifications(
    url: str, users: Sequence[uuid.UUID]
) -> list[tuple[Any, dict[str, Any]]]:
    """``(recipient, params)`` of the payment reminders of these users, in
    creation order."""
    rows = run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = 'billing.payment_reminder'"
        " AND recipient_user_id = ANY(:ids) ORDER BY created_at, id",
        {"ids": list(users)},
    )
    return [(row.recipient_user_id, dict(row.params)) for row in rows]


def days(n: int) -> timedelta:
    return timedelta(days=n)
