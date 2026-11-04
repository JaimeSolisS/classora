"""Generating recurring expenses (#116 D; spec §8; ADR-0017 E17; data-model
§12.7, §13.1 X2, §15; architecture ISSUE-J4).

The daily job ``finance:generate_recurring_expenses``
(:mod:`app.workflows.recurring_expense_tasks`, ``55 7 * * *``, also queued
after a template create, reactivation or ``ends_on`` change) posts **exactly
one** ordinary expense per template and period, on the natural key
``(expense_template_id, period_start)`` (``uq_expenses_template_period``,
X2). It never uses ``idempotency_keys``. The template rules and the grid are
the finance module's; this workflow adds the center's local today
(``administration.center_today``, read once per run, never UTC).

- **Candidates** (D2), read with no lock, in keyset pages by id:
  ``finance.service.due_template_ids``: active, not ended
  (``ends_on IS NULL OR next_period_start <= ends_on``) and
  ``next_period_start <= today``.
- **One Unit of Work per template** (``actor_id=None``, so the generated
  expenses' audit events have no actor):
  ``finance.service.generate_template_periods``: the template row ``FOR
  UPDATE`` and re-read (a template paused, ended or advanced by a concurrent
  run is skipped, or resumes from the new value) → each due period, oldest
  first, in a savepoint through ``finance.service.insert_expense`` (the
  category and vendor ``FOR SHARE``); a natural-key violation counts
  ``already_generated`` → ``next_period_start`` after the last period looked
  at. A failure rolls back that template only, is logged with the template id
  and the error class, and the run continues; the next run catches up.
- **Catch-up** (D4): every missing period up to today and ``ends_on``,
  oldest first, in one run.
- **Repeatable** (D5, D6): a rerun, a retry and two concurrent runs leave one
  expense per template and period: the second waits on the template row and
  finds nothing due; without that lock the unique index stops the second
  insert and the savepoint keeps the run going.
- **Category and vendor** (D10): not re-checked here beyond
  ``insert_expense``'s own ``usable_*``. A live template's category and vendor
  can't be deactivated (``409 expense_category_in_use`` / ``vendor_in_use``),
  and a write that makes a template live reads them ``FOR SHARE``.

Log lines carry counts and ids only: no name, amount or description (#116
E9).

**Run by hand** (:func:`run_generate_recurring_expenses` builds its own
direct engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.recurring_expenses import \\
    run_generate_recurring_expenses as run; print(asyncio.run(run(get_settings())))"
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
from app.modules.finance import service as finance

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecurringExpenseRunResult:
    """The counts of one run (also its log line)."""

    templates: int = 0
    generated: int = 0
    already_generated: int = 0
    failed: int = 0


async def generate_for_template(
    session: AsyncSession, template_id: uuid.UUID, *, today: date
) -> finance.GeneratedPeriods:
    """One template, in the caller's Unit of Work (the job's unit; seeds and
    reconciliation fixtures call it under their own clock)."""
    return await finance.generate_template_periods(session, template_id, today=today)


async def run_generate_recurring_expenses(
    settings: Settings,
) -> RecurringExpenseRunResult:
    """One run: every due template, one Unit of Work each. Returns the
    counts."""
    engine = create_worker_engine(settings)
    templates = generated = already = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            today = await administration.center_today(uow.session)
        after: uuid.UUID | None = None
        while True:
            async with UnitOfWork(sessionmaker) as uow:
                page = await finance.due_template_ids(
                    uow.session, today=today, after=after
                )
            for template_id in page:
                templates += 1
                try:
                    async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                        done = await generate_for_template(
                            uow.session, template_id, today=today
                        )
                except Exception as exc:  # noqa: BLE001 - one template never blocks the rest
                    failed += 1
                    logger.error(
                        "Generating recurring expenses failed",
                        extra={
                            "expense_template_id": str(template_id),
                            "error": type(exc).__name__,
                        },
                    )
                    continue
                # After the commit, so a failed commit counts nothing.
                generated += done.generated
                already += done.already_generated
            if len(page) < finance.TEMPLATE_CANDIDATE_PAGE:
                break
            after = page[-1]
    finally:
        await engine.dispose()
    result = RecurringExpenseRunResult(
        templates=templates,
        generated=generated,
        already_generated=already,
        failed=failed,
    )
    logger.info(
        "Generated recurring expenses",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result
