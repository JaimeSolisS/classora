"""The retention job and its rule registry (#129 C1, C2, C5, C6; ADR-0003;
``_docs/data-model.md`` "Retention and deletion").

**Registry** (C1): a :class:`RetentionRule` names a ``code``, the
``tables`` it deletes from and an async ``run(sessionmaker, now) -> int``
that deletes what has expired at ``now`` in its own transactions and
returns how many records it deleted. :func:`register_retention_rule`
records it; ``app/wiring.py`` registers the rules of the other modules
(``academics.deleted_records`` from ``app.workflows.deleted_records``, and
``jobs.finished_jobs`` from ``app.core.jobs``), so ``administration``
imports no module. Registering the same rule again changes nothing; another
rule under a registered code raises ``ValueError``.

**Protected tables** (C5): :func:`protected_tables` is every table of a
model in the ``billing``, ``payroll`` and ``finance`` modules (from the
model registry) plus :data:`PROTECTED_NAMED_TABLES`. No rule may delete from
them; ``tests/unit/test_retention_rules.py`` pins that every registered
rule's ``tables`` are disjoint from them, except
:data:`PROTECTED_EXCEPTIONS`: ``jobs.finished_jobs`` deletes the
``job_failures`` row of a succeeded job that once failed (the foreign key
cascades), never one of a ``failed`` job.

**The task** ``administration:run_retention`` (C2), daily at 09:30 UTC
(``30 9 * * *``: 03:30 in Mexico City, after the other nightly jobs),
registered in every environment by :func:`app.core.jobs.create_job_app`.
The only argument is Procrastinate's ``timestamp: int`` (unread); at most
one queued job (``queueing_lock`` on the periodic task and the task, a fixed
``periodic_id``). Each rule runs in turn; one that raises is logged with its
error class and doesn't stop the others, and the task raises
:class:`RetentionRunFailedError` at the end so the failed jobs page lists
it. Idempotent: a second run straight after deletes nothing.

**Summary** (C6): one ``INFO`` line ``retention.run_completed`` per run with
the counts per rule, the duration and the failed rule codes (no ids, no
values); when anything was deleted, one named audit event
``administration.retention_run`` with ``details = {"counts": {...}}`` and no
actor ("Sistema").

**Run by hand** (:func:`run_retention` builds its own direct engine and
disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.wiring import register_approval_wiring as w; w(); \\
    from app.modules.administration.retention import run_retention; \\
    print(asyncio.run(run_retention(get_settings())))"
"""

import functools
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from procrastinate import App
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.audit import record_audit_event
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import (
    RETENTION_QUEUEING_LOCK,
    RETENTION_TASK,
    create_worker_engine,
    ids_only,
)
from app.core.uow import UnitOfWork

logger = logging.getLogger(__name__)

TASK_NAME: Final = RETENTION_TASK
CRON: Final = "30 9 * * *"
PERIODIC_ID: Final = "administration-run-retention"
QUEUEING_LOCK: Final = RETENTION_QUEUEING_LOCK

RUN_COMPLETED: Final = "retention.run_completed"
"""The summary log line (C6)."""
AUDIT_ACTION: Final = "administration.retention_run"
"""The named audit event of a run that deleted something (C6)."""

type RetentionRun = Callable[
    [async_sessionmaker[AsyncSession], datetime], Awaitable[int]
]


@dataclass(frozen=True)
class RetentionRule:
    """One kind of data the retention job removes (C1)."""

    code: str
    tables: frozenset[str]
    """Every table ``run`` deletes from (pinned against
    :func:`protected_tables`)."""
    run: RetentionRun


_rules: dict[str, RetentionRule] = {}


def register_retention_rule(rule: RetentionRule) -> None:
    """Add ``rule`` (the same rule again: a no-op; another rule under its
    code: ``ValueError``)."""
    existing = _rules.get(rule.code)
    if existing is not None:
        if existing == rule:
            return
        raise ValueError(f"retention rule already registered: {rule.code!r}")
    _rules[rule.code] = rule


def unregister_retention_rule(code: str) -> None:
    """Remove a rule (tests)."""
    _rules.pop(code, None)


def registered_retention_rules() -> tuple[RetentionRule, ...]:
    """The registered rules, by code."""
    return tuple(_rules[code] for code in sorted(_rules))


# --- C5: protected tables -------------------------------------------------------------

PROTECTED_MODULES: Final = ("billing", "payroll", "finance")
"""Every table of these modules' models is protected (money, payroll and
expenses are kept forever)."""

PROTECTED_NAMED_TABLES: Final = frozenset(
    {
        "audit_events",
        "consent_records",
        "privacy_notice_acceptances",
        "import_jobs",
        "export_jobs",
        # Only the rows of ``failed`` jobs are protected: ``jobs.finished_jobs``
        # removes a succeeded, cancelled or aborted job, and the foreign key
        # cascades the row it left when it once failed
        # (:data:`PROTECTED_EXCEPTIONS`).
        "job_failures",
    }
)

PROTECTED_EXCEPTIONS: Final[Mapping[str, frozenset[str]]] = {
    "jobs.finished_jobs": frozenset({"job_failures"}),
}
"""The protected tables a rule may still touch, with the reason above."""


@functools.cache
def protected_tables() -> frozenset[str]:
    """Every protected table (C5): the tables of the
    :data:`PROTECTED_MODULES`' models plus :data:`PROTECTED_NAMED_TABLES`."""
    # Imported here: the registry imports every module's models.
    from app.core.model_registry import target_metadata  # noqa: F401
    from app.core.models import Base

    tables = set(PROTECTED_NAMED_TABLES)
    for mapper in Base.registry.mappers:
        parts = mapper.class_.__module__.split(".")
        if parts[:2] == ["app", "modules"] and parts[2] in PROTECTED_MODULES:
            tables.add(str(mapper.class_.__tablename__))
    return frozenset(tables)


# --- C2, C6: the run ------------------------------------------------------------------


class RetentionRunFailedError(Exception):
    """A rule failed; the task fails so the failed jobs page lists it (C2)."""


@dataclass(frozen=True)
class RetentionResult:
    """The counts of one run by rule code, and the codes of the rules that
    failed."""

    counts: dict[str, int] = field(default_factory=dict)
    failed: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def summary_extra(result: RetentionResult, duration_ms: int) -> dict[str, object]:
    """The structured fields of the summary line: counts by rule code, the
    duration and the failed rule codes; never an id or a value."""
    return {
        "counts": dict(sorted(result.counts.items())),
        "deleted_count": result.total,
        "duration_ms": duration_ms,
        "failed_rules": list(result.failed),
    }


async def run_retention_with(
    sessionmaker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> RetentionResult:
    """One run of every registered rule on ``sessionmaker`` (tests call it
    with their own engine and a moved clock). Doesn't raise for a failed
    rule; :func:`run_retention_task` does."""
    at = clock.now() if now is None else now
    started = time.monotonic()
    counts: dict[str, int] = {}
    failed: list[str] = []
    for rule in registered_retention_rules():
        try:
            counts[rule.code] = await rule.run(sessionmaker, at)
        except Exception as exc:  # noqa: BLE001 - one rule never blocks the rest
            failed.append(rule.code)
            logger.error(
                "A retention rule failed",
                extra={"rule_code": rule.code, "error": type(exc).__name__},
            )
    result = RetentionResult(counts=counts, failed=tuple(failed))
    if result.total > 0:
        async with UnitOfWork(sessionmaker) as uow:
            record_audit_event(
                uow.session,
                action=AUDIT_ACTION,
                details={"counts": dict(sorted(counts.items()))},
            )
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(RUN_COMPLETED, extra=summary_extra(result, duration_ms))
    return result


async def run_retention(settings: Settings) -> RetentionResult:
    """One run with its own direct engine (the worker's)."""
    engine = create_worker_engine(settings)
    try:
        return await run_retention_with(create_sessionmaker(engine))
    finally:
        await engine.dispose()


@ids_only
async def run_retention_task(timestamp: int) -> None:
    """The periodic task: one run; raises when a rule failed."""
    result = await run_retention(get_settings())
    if result.failed:
        raise RetentionRunFailedError(", ".join(result.failed))


def register(app: App) -> None:
    """Register the daily retention run on ``app`` (every environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=RETENTION_TASK, queueing_lock=QUEUEING_LOCK)(run_retention_task)
    )
