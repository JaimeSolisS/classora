"""Opening payroll periods (#106 D; spec §15 W7; ADR-0017 E6; data-model §15).

The daily job ``payroll:open_periods``
(:mod:`app.workflows.payroll_period_tasks`, ``50 7 * * *``) opens every
period due, for every tutor with terms. The calendar and the insert are the
payroll module's; this workflow adds the center's local today
(``administration.center_today``, ADR-0002).

- **Off:** while ``payroll_starts_on`` is ``NULL`` the run opens nothing and
  reads no tutor.
- **Tutors:** everyone in ``tutor_pay_terms``, whatever their role or state,
  because a stop row ends periods and archiving doesn't (#106 E8).
- **One Unit of Work per tutor** (``actor_id=None``, the row isn't audited):
  :func:`open_tutor_periods`. A failure rolls back that tutor only, is logged
  with the id and the error class, and the run continues. The next run
  catches up, oldest first.
- **Repeatable:** a rerun, a retry and two concurrent runs leave the same
  rows. See :func:`app.modules.payroll.service.open_due_periods` for the lock
  order.

Log lines carry counts and ids: no names, no amounts.

**Run by hand** (:func:`run_open_periods` builds its own direct engine and
disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.payroll_periods import run_open_periods as run; \\
    print(asyncio.run(run(get_settings())))"
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.payroll import service as payroll

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenPeriodsRunResult:
    """The counts of one run (also its log line)."""

    tutors: int = 0
    opened: int = 0
    failed: int = 0


async def open_tutor_periods(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, today: date
) -> int:
    """One tutor, in the caller's Unit of Work; the number of periods
    opened."""
    return await payroll.open_due_periods(session, tutor_user_id, today=today)


async def run_open_periods(settings: Settings) -> OpenPeriodsRunResult:
    """One run: every tutor with terms, one Unit of Work each. Returns the
    counts."""
    engine = create_worker_engine(settings)
    opened = failed = 0
    tutors: list[uuid.UUID] = []
    today: date | None = None
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            if await payroll.payroll_starts_on(uow.session) is not None:
                today = await administration.center_today(uow.session)
                tutors = await payroll.tutors_with_terms(uow.session)
        for tutor_user_id in tutors:
            assert today is not None  # noqa: S101 - set with the tutors
            try:
                async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                    opened += await open_tutor_periods(
                        uow.session, tutor_user_id, today=today
                    )
            except Exception as exc:  # noqa: BLE001 - one tutor never blocks the rest
                failed += 1
                logger.error(
                    "Opening payroll periods failed",
                    extra={
                        "tutor_user_id": str(tutor_user_id),
                        "error": type(exc).__name__,
                    },
                )
    finally:
        await engine.dispose()
    result = OpenPeriodsRunResult(tutors=len(tutors), opened=opened, failed=failed)
    logger.info(
        "Opened payroll periods",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result
