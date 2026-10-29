"""The daily package expiry (#94 F; L1, L2, L5; ADR-0006 E7, E10;
``_docs/data-model.md`` §3.20, §6).

**Due packages** (F2): ``expires_on`` before the center's today, no
``expire`` row and remaining units > 0, in keyset pages until exhausted.
It doesn't depend on ``billing_starts_on`` (L5): packages were sold
explicitly, and coverage starts at ``purchased_on``.

**Per due package, in order** (F3):

1. **Fallback consumption (L1; #86 QA O1).** The student's **pending**
   covered sessions in the package's window: an ``individual`` session in
   ``confirmed`` or ``completed`` with the student as a participant, a
   local date in ``[purchased_on, expires_on]``, the package's subject (or
   any, for an any-subject package), no ``session`` charge, no
   ``consume`` row and no approved closeout. By ``(starts_at,
   class_session_id)``, each in its own Unit of Work: the session ``FOR
   SHARE`` (``scheduling.service.lock_session_for_billing``; the kind, the
   state, the participant and the approval are checked again) →
   ``billing.service.consume_session_credit``, the same step as an
   approval (first-expiring-first, so it may use an earlier-expiring
   package; all or nothing). A later revision-1 approval of such a
   session finds the consumption and does nothing; a ``short`` one is
   charged by the session sweeper's next run (it is no longer covered once
   the package expires).
2. **Expiry.** Its own Unit of Work: ``billing.service.expire_package``
   (the account ``FOR UPDATE`` → remaining recomputed → one ``expire`` row
   for it, natural key ``package_id``; a violation is ``already_expired``).
   A package whose fallback had a failing item is not expired in this run,
   so no unit lapses that a pending session should have used.

So every covered session ends up consumed (at approval, or at the latest
the day after its package expires) or charged by the session sweeper.

**Counts** (one info log line, counts only): ``packages``,
``fallback_consumed``, ``fallback_short``, ``expired``,
``already_expired``, ``failed``. A failing item rolls back, is logged with
its ids and the error class, and is retried on the next run.

**Lock order:** session ``FOR SHARE`` (fallback items only) → the billing
account ``FOR UPDATE`` (the last lock).

**Run by hand** (:func:`run_expire_packages` builds its own direct engine
and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.package_expiry import run_expire_packages as run; \\
    print(asyncio.run(run(get_settings())))"
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import floor_instant, local_date

logger = logging.getLogger(__name__)

INDIVIDUAL: Final = "individual"


@dataclass(frozen=True)
class PackageExpiryRunResult:
    """The counts of one run (also its log line)."""

    packages: int = 0
    fallback_consumed: int = 0
    fallback_short: int = 0
    expired: int = 0
    already_expired: int = 0
    failed: int = 0


@dataclass(frozen=True)
class PendingSession:
    """A pending covered session of a due package (F3 i)."""

    class_session_id: uuid.UUID
    starts_at: datetime


async def pending_sessions(
    session: AsyncSession, package: billing.DuePackage, *, zone: str
) -> list[PendingSession]:
    """F3 (i): the package student's pending sessions in its window, by
    ``(starts_at, class_session_id)``. Two statements (the sessions with the
    charge and consumption anti-joins, then the approved closeouts)."""
    participants = scheduling.billable_participant_columns(
        kinds=(INDIVIDUAL,),
        starts_from=floor_instant(package.purchased_on, zone),
        student_id=package.student_id,
    ).subquery("participants")
    charged = billing.session_charge_key_columns().subquery("charged")
    consumed = billing.consumed_key_columns().subquery("consumed")

    def _absent(keys: Any) -> Any:
        return ~(
            select(keys.c.class_session_id)
            .where(
                keys.c.class_session_id == participants.c.class_session_id,
                keys.c.student_id == participants.c.student_id,
            )
            .exists()
        )

    query = (
        select(
            participants.c.class_session_id,
            participants.c.starts_at,
            participants.c.subject_id,
        )
        .where(
            participants.c.starts_at
            < floor_instant(package.expires_on + timedelta(days=1), zone),
            _absent(charged),
            _absent(consumed),
        )
        .order_by(participants.c.starts_at, participants.c.class_session_id)
    )
    rows = (await session.execute(query)).all()
    found = [
        row
        for row in rows
        if (package.subject_id is None or row.subject_id == package.subject_id)
        and package.purchased_on
        <= local_date(row.starts_at, zone)
        <= package.expires_on
    ]
    approved = await academics.approved_closeout_session_ids(
        session, {row.class_session_id for row in found}
    )
    return [
        PendingSession(class_session_id=row.class_session_id, starts_at=row.starts_at)
        for row in found
        if row.class_session_id not in approved
    ]


async def consume_pending(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    zone: str,
) -> billing.ConsumeOutcome | None:
    """One fallback item in the caller's Unit of Work (F3 i): the session
    ``FOR SHARE`` → still an individual ``confirmed``/``completed`` session
    of the student without an approved closeout (else ``None``, nothing
    written) → ``consume_session_credit``."""
    locked = await scheduling.lock_session_for_billing(session, class_session_id)
    if (
        locked is None
        or locked.kind != INDIVIDUAL
        or locked.state not in scheduling.BILLABLE_STATES
        or student_id not in locked.participant_ids
    ):
        return None
    if await academics.approved_closeout_session_ids(session, [class_session_id]):
        return None
    result = await billing.consume_session_credit(
        session,
        billing.SessionCreditClaim(
            class_session_id=locked.id,
            student_id=student_id,
            subject_id=locked.subject_id,
            service_date=local_date(locked.starts_at, zone),
            scheduled_minutes=locked.scheduled_minutes,
        ),
    )
    return result.outcome


async def run_expire_packages(
    settings: Settings, *, now: datetime | None = None
) -> PackageExpiryRunResult:
    """One run (module docstring). ``now`` (default
    :func:`app.core.clock.now`) gives the center's local ``today``."""
    engine = create_worker_engine(settings)
    counts = dict.fromkeys(
        (
            "packages",
            "fallback_consumed",
            "fallback_short",
            "expired",
            "already_expired",
            "failed",
        ),
        0,
    )
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            zone = await administration.get_center_time_zone(uow.session)
        today: date = local_date(now or clock.now(), zone)
        after: uuid.UUID | None = None
        while True:
            async with UnitOfWork(sessionmaker) as uow:
                page = await billing.due_packages(uow.session, today=today, after=after)
            if not page:
                break
            after = page[-1].id
            for package in page:
                counts["packages"] += 1
                if await _settle(sessionmaker, package, zone=zone, counts=counts):
                    await _expire(sessionmaker, package, counts=counts)
            if len(page) < billing.DUE_PAGE:
                break
    finally:
        await engine.dispose()
    result = PackageExpiryRunResult(**counts)
    logger.info(
        "Expired packages",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


async def _settle(
    sessionmaker: async_sessionmaker[AsyncSession],
    package: billing.DuePackage,
    *,
    zone: str,
    counts: dict[str, int],
) -> bool:
    """F3 (i) for one package; ``False`` when an item failed (the package
    is then not expired in this run)."""
    try:
        async with UnitOfWork(sessionmaker) as uow:
            pending = await pending_sessions(uow.session, package, zone=zone)
    except Exception as exc:  # noqa: BLE001 - one package never blocks the rest
        counts["failed"] += 1
        _log_failure(package.id, None, exc)
        return False
    settled = True
    for item in pending:
        try:
            async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                outcome = await consume_pending(
                    uow.session, item.class_session_id, package.student_id, zone=zone
                )
        except Exception as exc:  # noqa: BLE001 - one item never blocks the rest
            counts["failed"] += 1
            settled = False
            _log_failure(package.id, item.class_session_id, exc)
            continue
        if outcome == "consumed":
            counts["fallback_consumed"] += 1
        elif outcome == "short":
            counts["fallback_short"] += 1
    return settled


async def _expire(
    sessionmaker: async_sessionmaker[AsyncSession],
    package: billing.DuePackage,
    *,
    counts: dict[str, int],
) -> None:
    """F3 (ii) for one package."""
    try:
        async with UnitOfWork(sessionmaker, actor_id=None) as uow:
            outcome = await billing.expire_package(uow.session, package.id)
    except Exception as exc:  # noqa: BLE001 - one package never blocks the rest
        counts["failed"] += 1
        _log_failure(package.id, None, exc)
        return
    if outcome == "expired":
        counts["expired"] += 1
    elif outcome == "already_expired":
        counts["already_expired"] += 1


def _log_failure(
    package_id: uuid.UUID, class_session_id: uuid.UUID | None, exc: Exception
) -> None:
    extra = {"package_id": str(package_id), "error": type(exc).__name__}
    if class_session_id is not None:
        extra["class_session_id"] = str(class_session_id)
    logger.error("Expiring a package failed", extra=extra)
