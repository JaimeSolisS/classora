"""Shared helpers of the schedule reconciliation tests (#99 E).

**Isolation.** The job reads **every** candidate of the module database, and
the #91 reset (billing off, earlier uncharged billable sessions cancelled)
creates cancelled, once-confirmed sessions. :func:`reset` runs the package
reset (which runs the #91 one) and then makes every remaining candidate a
non-candidate (raw SQL, as a test fixture may: cancelled, never confirmed),
so a run's counts are the test's own.

**State changes** are written with raw SQL where the job only reads the
committed result (it is a state-derived sweeper, #99 Q1): a center
cancellation, a closure skip and restore, a new duration (``version + 1``,
as every scheduling writer bumps it) and an approved change request row.
The real routes are covered by ``test_schedule_reconciliation_routes.py``.

Charges are posted by the #91 step (:func:`charged`): one individual session
on :data:`DAY` at 10:00 (Mexico City) with one participant in a family and
a ``250.00`` any-duration price, billing on from ``FLOOR``.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.workflows import schedule_reconciliation as reconciliation
from app.workflows import session_charges
from tests.factories import billing as billing_factories
from tests.factories.identity import persist_user
from tests.factories.scheduling import add_participant
from tests.integration import package_support
from tests.integration import session_charge_support as scs
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow

ZONE = "America/Mexico_City"
FLOOR = scs.FLOOR
DAY = FLOOR + timedelta(days=9)
"""2031-03-10, a Monday."""
PRICE = "250.00"

CANCEL_SQL = (
    "UPDATE class_sessions SET state = 'cancelled', cancelled_at = :at,"
    " cancellation_reason = :reason, version = version + 1 WHERE id = :id"
)


def reset(url: str) -> None:
    """The package and #91 resets, then no candidate is left."""
    package_support.reset(url)
    sessions: set[uuid.UUID] = set()

    async def _keys(session: AsyncSession) -> None:
        after = None
        while True:
            page, after = await reconciliation.candidate_page(session, after=after)
            sessions.update(key[0] for key in page)
            if after is None:
                return

    in_uow(url, _keys)
    if sessions:
        run_sql(
            url,
            "UPDATE class_sessions SET state = 'cancelled',"
            " cancelled_at = coalesce(cancelled_at, now()),"
            " cancellation_reason = coalesce(cancellation_reason, 'Reinicio'),"
            " first_confirmed_at = NULL WHERE id = ANY(:ids)",
            {"ids": list(sessions)},
        )


@dataclass(frozen=True)
class Charged:
    """A session with its participant's ``session`` charge."""

    item: scs.Item
    charge: uuid.UUID

    @property
    def session(self) -> uuid.UUID:
        return self.item.session

    @property
    def student(self) -> uuid.UUID:
        return self.item.student

    @property
    def key(self) -> tuple[uuid.UUID, uuid.UUID]:
        return (self.item.session, self.item.student)


def post(url: str, it: scs.Item) -> uuid.UUID:
    """Billing on from ``FLOOR``, then the #91 step for ``it``; returns the
    charge id."""
    scs.set_floor(url, FLOOR)
    assert scs.post_one(url, it) in ("posted", "already_posted")
    return charge_of(url, it.session, it.student).id


def charged(
    url: str,
    *,
    starts_at: datetime | None = None,
    minutes: int = 60,
    price: str | None = PRICE,
    **kwargs: Any,
) -> Charged:
    """One charged session (module docstring)."""
    it = scs.item(
        url,
        starts_at=starts_at or scs.local(DAY),
        minutes=minutes,
        price=price,
        **kwargs,
    )
    return Charged(it, post(url, it))


def charge_of(url: str, class_session: uuid.UUID, student: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT * FROM charges WHERE kind = 'session' AND class_session_id = :s"
        " AND student_id = :st",
        {"s": class_session, "st": student},
    )
    return row


def charges_of(url: str, class_session: uuid.UUID) -> list[Any]:
    return scs.charges_of(url, class_session)


def adjustments(url: str, charge: uuid.UUID, kind: str | None = None) -> list[Any]:
    sql = "SELECT * FROM charge_adjustments WHERE charge_id = :c"
    if kind is not None:
        sql += " AND kind = :k"
    return run_sql(url, sql + " ORDER BY posted_at, id", {"c": charge, "k": kind})


def net(url: str, charge: uuid.UUID) -> Money:
    [row] = run_sql(
        url,
        "SELECT c.amount + coalesce((SELECT sum(a.amount) FROM charge_adjustments a"
        " WHERE a.charge_id = c.id), 0) AS net FROM charges c WHERE c.id = :c",
        {"c": charge},
    )
    return Money(row.net)


def decision(url: str, class_session: uuid.UUID, student: uuid.UUID) -> Any | None:
    rows = run_sql(
        url,
        "SELECT * FROM session_cancellation_decisions"
        " WHERE class_session_id = :s AND student_id = :st",
        {"s": class_session, "st": student},
    )
    return rows[0] if rows else None


def repricings(url: str, charge: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charge_duration_repricings WHERE charge_id = :c"
        " ORDER BY created_at, id",
        {"c": charge},
    )


def cancel(
    url: str,
    class_session: uuid.UUID,
    *,
    at: datetime | None = None,
    reason: str = "Cancelada por el centro",
) -> None:
    """A committed cancellation (raw SQL; ``at`` defaults to now)."""
    run_sql(
        url,
        CANCEL_SQL,
        {"id": class_session, "at": at or datetime.now(UTC), "reason": reason},
    )


def set_state(url: str, class_session: uuid.UUID, state: str) -> None:
    """A closure skip or a restore (raw SQL, ``version + 1``)."""
    run_sql(
        url,
        "UPDATE class_sessions SET state = :state, version = version + 1"
        " WHERE id = :id",
        {"id": class_session, "state": state},
    )


def retime(url: str, class_session: uuid.UUID, minutes: int) -> None:
    """A new duration from the same start (raw SQL, ``version + 1``)."""
    run_sql(
        url,
        "UPDATE class_sessions SET ends_at = starts_at + make_interval(mins => :m),"
        " scheduled_minutes = :m, version = version + 1 WHERE id = :id",
        {"id": class_session, "m": minutes},
    )


def move(url: str, class_session: uuid.UUID, by: timedelta) -> None:
    """A reschedule that keeps the minutes (raw SQL, ``version + 1``)."""
    run_sql(
        url,
        "UPDATE class_sessions SET starts_at = starts_at + :d,"
        " ends_at = ends_at + :d, version = version + 1 WHERE id = :id",
        {"id": class_session, "d": by},
    )


def approved_request(
    url: str,
    class_session: uuid.UUID,
    *,
    student: uuid.UUID | None,
    initiated_by: str = "family",
    classification: str = "on_time",
    makeup: bool = False,
    final_classification: str | None = None,
    final_makeup: bool | None = None,
    exception: bool = False,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """An approved cancellation request row (raw SQL; the #62 CHECKs hold):
    the request-time outcome and the effective one (``final_*``, the
    request-time one by default)."""

    async def _user(session: AsyncSession) -> uuid.UUID:
        return (await persist_user(session)).id

    user = in_uow(url, _user)
    [policy] = run_sql(
        url, "SELECT id FROM scheduling_policies ORDER BY effective_from LIMIT 1"
    )
    [times] = run_sql(
        url,
        "SELECT starts_at, ends_at FROM class_sessions WHERE id = :id",
        {"id": class_session},
    )
    final = classification if final_classification is None else final_classification
    final_makeup_allowed = makeup if final_makeup is None else final_makeup
    until = times.starts_at + timedelta(days=30)
    [row] = run_sql(
        url,
        "INSERT INTO session_change_requests (class_session_id, kind, initiated_by,"
        " requested_by_user_id, student_id, session_starts_at, session_ends_at,"
        " policy_id, classification, makeup_allowed, makeup_until, status,"
        " exception, decision_reason, decided_by_user_id, resolved_at,"
        " final_classification, final_makeup_allowed, final_makeup_until,"
        " created_at)"
        " VALUES (:s, 'cancellation', :by, :u, :st, :start, :end, :p, :c, :m,"
        " :until, 'approved', :e, :reason, :u, now(), :fc, :fm, :funtil,"
        " coalesce(:created, now())) RETURNING id",
        {
            "s": class_session,
            "by": initiated_by,
            "u": user,
            "st": student if initiated_by == "family" else None,
            "start": times.starts_at,
            "end": times.ends_at,
            "p": policy.id,
            "c": classification,
            "m": makeup,
            "until": until if makeup else None,
            "e": exception,
            "reason": "Excepción autorizada" if exception else None,
            "fc": final,
            "fm": final_makeup_allowed,
            "funtil": until if final_makeup_allowed else None,
            "created": created_at,
        },
    )
    return uuid.UUID(str(row.id))


def add_rule(url: str, effective_from: datetime, **outcomes: str) -> uuid.UUID:
    """A rule version at any instant (raw SQL: the guard refuses only
    ``UPDATE``, ``DELETE`` of a version in force and ``TRUNCATE``)."""
    values = {
        "excused_outcome": "credit",
        "on_time_outcome": "credit",
        "late_outcome": "charge",
        "no_show_outcome": "charge",
        **outcomes,
    }
    [row] = run_sql(
        url,
        "INSERT INTO cancellation_billing_rules (effective_from, excused_outcome,"
        " on_time_outcome, late_outcome, no_show_outcome)"
        " VALUES (:at, :excused_outcome, :on_time_outcome, :late_outcome,"
        " :no_show_outcome) RETURNING id",
        {"at": effective_from, **values},
    )
    return uuid.UUID(str(row.id))


def run(url: str) -> reconciliation.ReconciliationRunResult:
    return asyncio.run(reconciliation.run_reconcile_session_charges(settings_for(url)))


def one(url: str, class_session: uuid.UUID, student: uuid.UUID) -> list[str]:
    return in_uow(
        url,
        lambda s: reconciliation.reconcile_one(s, class_session, student, zone=ZONE),
    )


def pay(url: str, charge: uuid.UUID, amount: str) -> uuid.UUID:
    """A payment of ``amount`` allocated to ``charge``; returns the
    allocation id."""

    [row] = run_sql(
        url, "SELECT billing_account_id FROM charges WHERE id = :c", {"c": charge}
    )

    async def _make(session: AsyncSession) -> uuid.UUID:
        recorder = await billing_factories.staff_user_id(session)
        paid = await billing_factories.payment(
            session, row.billing_account_id, amount, recorded_by=recorder
        )
        allocated = await billing_factories.allocation(session, paid, charge, amount)
        return allocated.id

    return in_uow(url, _make)


def released(url: str, allocation: uuid.UUID) -> bool:
    return bool(
        run_sql(
            url,
            "SELECT 1 FROM allocation_releases WHERE allocation_id = :a",
            {"a": allocation},
        )
    )


def add_students(
    url: str, it: scs.Item, families: Sequence[uuid.UUID | None]
) -> list[uuid.UUID]:
    """More participants of ``it``'s session (the subject's level), each in
    the given family (``None``: a new one)."""

    async def _add(session: AsyncSession) -> list[uuid.UUID]:
        from app.modules.families.models import Family
        from app.modules.scheduling.models import ClassSession
        from tests.factories.families import (
            FamilyFactory,
            add_to_family,
            persist_student,
        )

        class_session = await session.get(ClassSession, it.session)
        assert class_session is not None
        added: list[uuid.UUID] = []
        for family_id in families:
            student = await persist_student(
                session, contacts=0, education_level_id=it.level
            )
            if family_id is None:
                family = FamilyFactory.build()
                session.add(family)
                await session.flush()
            else:
                found = await session.get(Family, family_id)
                assert found is not None
                family = found
            await add_to_family(session, family, student)
            await add_participant(session, class_session, student.id)
            added.append(student.id)
        return added

    return in_uow(url, _add)


def post_for(url: str, class_session: uuid.UUID, student: uuid.UUID) -> uuid.UUID:
    """The #91 step for another participant; returns the charge id."""
    outcome = in_uow(
        url,
        lambda s: session_charges.post_one(s, class_session, student, zone=ZONE),
    )
    assert outcome in ("posted", "already_posted"), outcome
    return charge_of(url, class_session, student).id


def local(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return scs.local(day, hour, minute)
