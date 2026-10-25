"""``python -m tests.factories.e2e_attendance``: the E2E attendance and
closeout fixture (#76 E5, H9; #77 E8, H8; #301b H3).

``e2e/scripts/run.sh`` runs it right after the demo step. It inserts, with
the factories, one ``confirmed`` **group** session of ``demo.tutor`` (the
#195 demo account) with two new synthetic students, the subject
:data:`SUBJECT_NAME` (in its own level) and no room, in the latest free
60-minute slot that ends before now (whole hours, searching back up to
:data:`SEARCH_DAYS` days), so it never meets ``demo.tutor``'s other sessions
(for example the #299 demo schedule) and the tutor exclusion constraint.
The session has started, so the tutor can take attendance at once (no API
creates a started ``confirmed`` session: an offer can't be accepted after
its start, #52).

#77 adds a second session for the closeout spec, kept apart from the
attendance one (whose correction step stays unchanged): one ``confirmed``
**individual** session of ``demo.tutor`` with one new synthetic student,
the subject :data:`CLOSEOUT_SUBJECT_NAME` (same level), 60 minutes, in the
latest free slot that ends at or before the attendance session's start.

#301b adds a third session for the overdue closeouts spec, which the
Owner closes out on the tutor's behalf: one ``confirmed`` **individual**
session of ``demo.tutor`` with one new synthetic student and **no
attendance**, the subject :data:`OVERDUE_SUBJECT_NAME` (same level), 60
minutes, in the latest free slot that ends at or before the closeout
session's start.

Separate from ``make seed-demo`` (``tests/factories/demo.py``): it is test
data for one spec, not demo data.

- **Guard:** only with ``APP_ENV`` in ``demo.ALLOWED_APP_ENVS`` (``local``,
  ``dev``); otherwise it prints the refusal and exits 2 before connecting.
- **Idempotent:** when ``demo.tutor`` already has a session of a subject,
  that session is not written again (a full rerun prints ``0 rows``).
- Prints the row count only, never generated values. A missing
  ``demo.tutor`` exits 1.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import clock
from app.core.config import ConfigurationError, get_settings
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.catalog.models import EducationLevel, Subject
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.states import TUTOR_OCCUPYING_STATES
from app.seeds.runner import EngineFactory, create_seed_engine
from tests.factories.base import reseed
from tests.factories.demo import refusal
from tests.factories.families import persist_student
from tests.factories.identity import (
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_user_id,
)
from tests.factories.scheduling import add_participant, persist_class_session

NAME: Final = "scheduling.e2e_attendance"
SUBJECT_NAME: Final = "E2E Attendance"
CLOSEOUT_SUBJECT_NAME: Final = "E2E Closeout"
OVERDUE_SUBJECT_NAME: Final = "E2E Overdue"
LEVEL_NAME: Final = "E2E Asistencia"
SEARCH_DAYS: Final = 7
DURATION: Final = timedelta(minutes=60)
STUDENTS: Final = 2
SEED: Final = 76


def _hour_floor(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def _busy(
    session: AsyncSession, tutor_id: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """The tutor's occupying sessions overlapping ``[start, end)``."""
    rows = await session.execute(
        select(ClassSession.starts_at, ClassSession.ends_at).where(
            and_(
                ClassSession.tutor_user_id == tutor_id,
                ClassSession.state.in_(sorted(TUTOR_OCCUPYING_STATES)),
                ClassSession.starts_at < end,
                ClassSession.ends_at > start,
            )
        )
    )
    return [(row.starts_at, row.ends_at) for row in rows]


def latest_free_slot(
    now: datetime,
    busy: Sequence[tuple[datetime, datetime]],
    *,
    days: int = SEARCH_DAYS,
) -> tuple[datetime, datetime] | None:
    """The latest whole-hour 60-minute slot ending at or before ``now`` that
    overlaps none of ``busy`` (half-open ranges), within ``days`` days; or
    ``None``. Pure."""
    end = _hour_floor(now)
    earliest = now - timedelta(days=days)
    while end - DURATION >= earliest:
        start = end - DURATION
        if not any(b_start < end and b_end > start for b_start, b_end in busy):
            return start, end
        end -= timedelta(hours=1)
    return None


async def _subject(session: AsyncSession, name: str) -> tuple[Subject, int]:
    """A fixture subject (created in the fixture level, itself created when
    missing) and the rows written."""
    found = await session.scalar(select(Subject).where(Subject.name == name))
    if found is not None:
        return found, 0
    rows = 1
    level = await session.scalar(
        select(EducationLevel).where(EducationLevel.name == LEVEL_NAME)
    )
    if level is None:
        level = EducationLevel(name=LEVEL_NAME, sort_order=90)
        session.add(level)
        await session.flush()
        rows += 1
    subject = Subject(education_level_id=level.id, name=name)
    session.add(subject)
    await session.flush()
    return subject, rows


async def _existing(
    session: AsyncSession, tutor_id: uuid.UUID, subject_id: uuid.UUID
) -> ClassSession | None:
    found: ClassSession | None = await session.scalar(
        select(ClassSession).where(
            ClassSession.tutor_user_id == tutor_id,
            ClassSession.subject_id == subject_id,
        )
    )
    return found


async def _slot_before(
    session: AsyncSession, tutor_id: uuid.UUID, before: datetime
) -> tuple[datetime, datetime]:
    busy = await _busy(
        session, tutor_id, before - timedelta(days=SEARCH_DAYS + 1), before
    )
    slot = latest_free_slot(before, busy)
    if slot is None:  # pragma: no cover - a full week of classes
        raise DemoPrerequisiteMissingError(
            f"{DEMO_TUTOR_USERNAME} has no free hour in the last {SEARCH_DAYS} days"
        )
    return slot


async def _individual_before(
    session: AsyncSession,
    tutor_id: uuid.UUID,
    subject_id: uuid.UUID,
    before: datetime,
) -> ClassSession:
    """A ``confirmed`` individual session of the tutor with one new student
    (no attendance), in the latest free hour ending at or before
    ``before``."""
    start, end = await _slot_before(session, tutor_id, before)
    lesson = await persist_class_session(
        session,
        kind="individual",
        state="confirmed",
        subject_id=subject_id,
        tutor_user_id=tutor_id,
        starts_at=start,
        ends_at=end,
        scheduled_minutes=int(DURATION.total_seconds() // 60),
    )
    student = await persist_student(session, contacts=0)
    await add_participant(session, lesson, student.id)
    return lesson


async def generate(session: AsyncSession) -> int:
    """Insert the fixture (see the module docstring); returns the rows
    written."""
    tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, NAME)
    subject, rows = await _subject(session, SUBJECT_NAME)
    attendance = await _existing(session, tutor_id, subject.id)
    if attendance is None:
        start, end = await _slot_before(session, tutor_id, clock.now())
        attendance = await persist_class_session(
            session,
            kind="group",
            state="confirmed",
            subject_id=subject.id,
            tutor_user_id=tutor_id,
            starts_at=start,
            ends_at=end,
            scheduled_minutes=int(DURATION.total_seconds() // 60),
        )
        rows += 2  # the session and its group
        for _ in range(STUDENTS):
            student = await persist_student(session, contacts=0)
            await add_participant(session, attendance, student.id)
            rows += 2

    # #77: the closeout session, before the attendance one.
    closeout_subject, written = await _subject(session, CLOSEOUT_SUBJECT_NAME)
    rows += written
    closeout = await _existing(session, tutor_id, closeout_subject.id)
    if closeout is None:
        closeout = await _individual_before(
            session, tutor_id, closeout_subject.id, attendance.starts_at
        )
        rows += 3  # the session, the student and the participant

    # #301b: the overdue session, before the closeout one.
    overdue_subject, written = await _subject(session, OVERDUE_SUBJECT_NAME)
    rows += written
    if await _existing(session, tutor_id, overdue_subject.id) is None:
        await _individual_before(
            session, tutor_id, overdue_subject.id, closeout.starts_at
        )
        rows += 3  # the session, the student and the participant
    return rows


async def run(engine: AsyncEngine) -> int:
    reseed(SEED)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await generate(uow.session)
    finally:
        await engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: EngineFactory = create_seed_engine,
) -> int:
    """Run the command; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.factories.e2e_attendance",
        description=(
            "Insert the E2E attendance, closeout and overdue sessions"
            " (APP_ENV local or dev only)."
        ),
    )
    parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        return 1
    message = refusal(settings.app_env)
    if message is not None:
        print(message, file=sys.stderr)
        return 2
    try:
        rows = asyncio.run(run(engine_factory(settings)))
    except DemoPrerequisiteMissingError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{NAME}: {rows} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
