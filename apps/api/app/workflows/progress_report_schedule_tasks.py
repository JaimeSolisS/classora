"""The progress report placeholder job (#316 D; ADR-0003, ISSUE-J2).

**``academics:create_progress_report_placeholders``**, every day at 07:00
UTC (``0 7 * * *``, 01:00 in Mexico City), registered in every environment
by :func:`app.core.jobs.create_job_app` (through :func:`register`, imported
lazily there). It lives under ``app/workflows/`` because it composes
``academics`` with ``scheduling``, ``families``, ``catalog``, ``identity``
and ``administration`` (``academics`` imports only ``app.core``). Like the
``scheduling`` tasks: the only argument is Procrastinate's ``timestamp:
int``; one job per tick (``queueing_lock`` and a fixed ``periodic_id``);
idempotent; one Unit of Work per item; counts-only logs.

Each run reads the center's zone (a zone change applies on the next run;
schedules hold local dates only, so nothing registers a time-zone
dependency) and takes ``today`` there. Then:

1. **Selection** (D2, one statement): the active schedules whose **latest
   ended period** (last week, or last month) is covered by ``starts_on``
   and has neither their own placeholder (deleted and retracted ones
   included: a placeholder is never created twice, E2) nor a live,
   non-retracted report the tutor wrote; by id, at most :data:`BATCH_SIZE`
   (the rest the next day). Older periods are never filled (no backfill).
2. **Per schedule**, its own Unit of Work with no actor (D3): the schedule
   ``FOR UPDATE``; skipped when no longer active or the period is no longer
   covered; skipped with its reason when not eligible now (#83 C2's create
   rules: the student is active, the author an active tutor who currently
   teaches them, the subject active and taught); else ``INSERT … ON
   CONFLICT DO NOTHING`` of the empty draft, and only when a row came back
   the ``academics.progress_report_due`` notification to the author and the
   ``academics.progress_report_placeholder_created`` audit event. A failing
   schedule is logged with its id and the error class and never blocks the
   others; the next run retries it.

:func:`run_progress_report_placeholders` builds its own direct engine and
disposes it, so it can be run by hand::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.progress_report_schedule_tasks import \\
    run_progress_report_placeholders as run; print(asyncio.run(run(get_settings())))"

Nothing outside ``app/core/jobs.py`` imports this module.
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Literal

from procrastinate import App
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine, ids_only
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.progress_report_schedules import (
    DUE_NOTIFICATION,
    ProgressReportDueParams,
    ineligible_reason,
    register_schedule_notifications,
)

logger = logging.getLogger(__name__)

rules = academics.progress_report_schedule_rules
report_rules = academics.progress_report_rules

TASK_NAME: Final = "academics:create_progress_report_placeholders"
CRON: Final = "0 7 * * *"
PERIODIC_ID: Final = "academics-progress-report-placeholders"
QUEUEING_LOCK: Final = "academics-progress-report-placeholders"
BATCH_SIZE: Final = 500
PLACEHOLDER_EVENT: Final = "academics.progress_report_placeholder_created"
RECORD_TYPE: Final = "progress_reports"

type Outcome = Literal["created", "existing", "skipped"] | rules.IneligibleReason


@dataclass(frozen=True)
class PlaceholderResult:
    """The counts of one run (also its log line)."""

    schedules: int = 0
    created: int = 0
    existing: int = 0
    """The insert found the period taken (a race with the tutor or another
    run)."""
    skipped: int = 0
    """No longer active, or the period no longer covered, under the lock."""
    ineligible: dict[str, int] = field(default_factory=dict)
    """Skipped by eligibility reason."""
    failed: int = 0


async def due_schedules(
    session: AsyncSession, now: datetime
) -> tuple[date, list[uuid.UUID]]:
    """D2: the center-local ``today`` of ``now`` and the schedules to fill.
    One statement after the zone read."""
    today = rules.local_today(now, await administration.get_center_time_zone(session))
    weekly_start, _ = rules.latest_ended_period(report_rules.WEEKLY, today)
    monthly_start, _ = rules.latest_ended_period(report_rules.MONTHLY, today)
    ids = await academics.due_schedule_ids(
        session,
        weekly_start=weekly_start,
        monthly_start=monthly_start,
        limit=BATCH_SIZE,
    )
    return today, ids


async def place_one(
    session: AsyncSession, schedule_id: uuid.UUID, *, now: datetime, today: date
) -> Outcome:
    """D3 for one schedule in the caller's Unit of Work (at most nine
    statements)."""
    schedule = await academics.get_schedule_for_update(session, schedule_id)
    if schedule is None or not schedule.is_active:
        return "skipped"
    period = rules.due_period(schedule.cadence, schedule.starts_on, today)
    if period is None:
        return "skipped"
    student = (await families.student_references(session, [schedule.student_id])).get(
        schedule.student_id
    )
    author = await identity.get_account_reference(session, schedule.author_user_id)
    author_subjects = await scheduling.subject_ids_for_tutor_student(
        session, schedule.author_user_id, schedule.student_id, now=now
    )
    subject: Any = None
    if schedule.subject_id is not None:
        subject = await catalog.get_subject_for_reference(
            session, schedule.subject_id, lock=True
        )
    reason = ineligible_reason(
        schedule,
        student=student,
        author=author,
        author_subjects=author_subjects,
        subject=subject,
    )
    if reason is not None:
        return reason
    period_start, period_end = period
    report_id = await academics.insert_placeholder(
        session, schedule, period_start=period_start, period_end=period_end
    )
    if report_id is None:
        return "existing"
    await administration.create_notification(
        session,
        recipient_user_id=schedule.author_user_id,
        notification_type=DUE_NOTIFICATION,
        params=ProgressReportDueParams(
            student_id=str(schedule.student_id),
            report_id=str(report_id),
            kind=schedule.cadence,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
        ),
    )
    record_audit_event(
        session,
        action=PLACEHOLDER_EVENT,
        record_type=RECORD_TYPE,
        record_id=report_id,
        details={
            "report_id": str(report_id),
            "schedule_id": str(schedule.id),
            "period_start": period_start.isoformat(),
        },
    )
    return "created"


async def run_progress_report_placeholders(
    settings: Settings, *, now: datetime | None = None
) -> PlaceholderResult:
    """One run at ``now`` (default :func:`app.core.clock.now`): the latest
    ended period of every due schedule, one Unit of Work per schedule.
    Returns the counts."""
    at = clock.now() if now is None else now
    # The worker and the CLI register it through app.wiring; a run by hand
    # may not have: registering again changes nothing.
    register_schedule_notifications()
    engine = create_worker_engine(settings)
    outcomes: Counter[str] = Counter()
    schedules = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            today, ids = await due_schedules(uow.session, at)
        for schedule_id in ids:
            schedules += 1
            try:
                async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                    outcome = await place_one(
                        uow.session, schedule_id, now=at, today=today
                    )
            except Exception as exc:  # noqa: BLE001 - one schedule never blocks the rest
                failed += 1
                logger.error(
                    "Progress report placeholder failed",
                    extra={
                        "schedule_id": str(schedule_id),
                        "error": type(exc).__name__,
                    },
                )
                continue
            outcomes[outcome] += 1
    finally:
        await engine.dispose()
    ineligible = {
        reason: outcomes[reason]
        for reason in rules.INELIGIBLE_REASONS
        if outcomes[reason]
    }
    result = PlaceholderResult(
        schedules=schedules,
        created=outcomes["created"],
        existing=outcomes["existing"],
        skipped=outcomes["skipped"],
        ineligible=ineligible,
        failed=failed,
    )
    logger.info(
        "Created progress report placeholders",
        # ``created`` is a LogRecord attribute: the keys carry a suffix.
        extra={
            "schedule_count": result.schedules,
            "created_count": result.created,
            "existing_count": result.existing,
            "skipped_count": result.skipped,
            "ineligible_count": sum(ineligible.values()),
            **{f"ineligible_{reason}_count": n for reason, n in ineligible.items()},
            "failed_count": result.failed,
        },
    )
    return result


@ids_only
async def create_progress_report_placeholders(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_progress_report_placeholders(get_settings())


def register(app: App) -> None:
    """Register the daily placeholder job on ``app`` (every environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=TASK_NAME, queueing_lock=QUEUEING_LOCK)(
            create_progress_report_placeholders
        )
    )
