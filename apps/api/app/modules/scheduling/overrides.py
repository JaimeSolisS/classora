"""Overrides of soft scheduling conflicts (#49 C, D; spec §3, W1.6; ADR-0005
"Conflict wire format and overrides").

A caller who holds ``schedule.override`` resends a write with
``override: {codes, reason}``
(:class:`~app.modules.scheduling.schemas.SessionOverride`).
The server then **recomputes** every conflict inside the write's
transaction, under the student locks, and decides with
:func:`evaluate_override` (pure):

- **any hard conflict**: refused (``409 scheduling_conflict`` with the
  complete sorted list, hard and soft). A hard code is never overridden; one
  inside ``codes`` is already ``422 code_not_overridable`` (the schema);
- **a soft conflict whose code is not listed**: refused the same way;
- **every conflict soft and listed**: allowed. The overridden conflicts are
  the computed soft conflicts (code and details), not the client's list;
- **listed codes that don't occur** are ignored (#49 E1): never stored or
  audited;
- **no conflict at all**: allowed with nothing overridden, so no override
  row and no override event.

Without an ``override`` the same function allows only a write with no
conflict. The ``403`` for a caller without ``schedule.override`` comes
before anything is read or locked (the route checks it).

When the write is allowed and something was overridden, the caller writes
the row and the event with :func:`record_session_override`, in the same
transaction, after the write itself: one ``class_session_overrides`` row
(append-only) and one named audit event ``scheduling.session_override``
(``details``: ``override_id``, ``codes``, ``conflicts``, ``reason``). No
notification is sent.

**Every later write path that runs the conflict checks** (#53 series,
#56 / #65 enrollments, #58 reschedule, #59 / #60 requests) uses these two
functions, so an override means the same thing everywhere.

**Series (#236, ADR-0005 D4: "listed occurrence and code pairs").** A
series create or edit sends ``override: {pairs: [{occurrence_start, code}],
reason}`` (:class:`~app.modules.scheduling.schemas.SeriesOverride`).
:func:`evaluate_series_override` (pure) groups the recomputed conflicts by
occurrence (instants compared as UTC values, never as text) and runs
:func:`evaluate_override`'s rule for each occurrence with the codes paired
with it, so an override means the same thing everywhere: any hard conflict
on any occurrence, or any soft one whose pair isn't listed, refuses the
whole write (the caller answers the complete per-occurrence ``409``);
listed pairs that don't occur (an occurrence out of the window, no longer
conflicting, or no such instant) are ignored. After the write is flushed,
:func:`record_series_overrides` stores **one row per overridden
occurrence** (its own ``class_session_id``, plus the write's series in the
context column ``class_series_id``) with one flush, and **one** named event
``scheduling.series_override`` on the series (``write``,
``occurrence_count``, ``occurrences[{override_id, class_session_id,
occurrence_start, codes}]``, ``reason``); the full conflicts stay on the
rows. #56's group series create reuses both functions.

**No carry-forward (#236 G1).** Nothing reads these rows to decide a later
write: the nightly materialization and #232's closure restore never
consult them, and an edit that re-checks an overridden occurrence needs a
new pair. Each override is a fresh authorization against conflicts
recomputed at that moment (W1.6): a qualification revoked later must not
stay overridden. A ``conflict`` row the job creates is overridden on its
own (``POST /api/v1/class-sessions/{id}/conflict-overrides``,
:mod:`~app.modules.scheduling.conflict_override`).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.modules.scheduling.conflicts import Conflict
from app.modules.scheduling.models import ClassSession, ClassSessionOverride
from app.modules.scheduling.schemas import SeriesOverride, SessionOverride

OVERRIDE_EVENT: Final = "scheduling.session_override"
SERIES_OVERRIDE_EVENT: Final = "scheduling.series_override"

SeriesWrite = Literal["create", "edit"]


@dataclass(frozen=True)
class OverrideDecision:
    """``allowed``: the write may go ahead. ``overridden``: the computed soft
    conflicts it overrides (empty when there was no conflict)."""

    allowed: bool
    overridden: tuple[Conflict, ...] = ()


def evaluate_override(
    conflicts: Sequence[Conflict], override: SessionOverride | None
) -> OverrideDecision:
    """The decision for the conflicts just computed (see the module
    docstring). Pure."""
    return _decide(conflicts, None if override is None else override.codes)


def _decide(
    conflicts: Sequence[Conflict], listed: Collection[str] | None
) -> OverrideDecision:
    # The one rule (#49 C2): ``listed`` None means no override was sent.
    if not conflicts:
        return OverrideDecision(allowed=True)
    if listed is None or any(c.severity == "hard" for c in conflicts):
        return OverrideDecision(allowed=False)
    if any(conflict.code not in listed for conflict in conflicts):
        return OverrideDecision(allowed=False)
    return OverrideDecision(allowed=True, overridden=tuple(conflicts))


@dataclass(frozen=True)
class OccurrenceConflict:
    """One recomputed conflict of one occurrence of a series write;
    ``occurrence_start`` is the instant its ``409`` item names in
    ``details.occurrence_start``."""

    occurrence_start: datetime
    conflict: Conflict


@dataclass(frozen=True)
class OverriddenOccurrence:
    """The computed soft conflicts an allowed series override covers on one
    occurrence (``occurrence_start`` in UTC)."""

    occurrence_start: datetime
    conflicts: tuple[Conflict, ...]


@dataclass(frozen=True)
class SeriesOverrideDecision:
    """``allowed``: the series write may go ahead. ``occurrences``: the
    overridden occurrences, by instant (empty when nothing conflicted)."""

    allowed: bool
    occurrences: tuple[OverriddenOccurrence, ...] = ()


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def evaluate_series_override(
    conflicts: Sequence[OccurrenceConflict], override: SeriesOverride | None
) -> SeriesOverrideDecision:
    """The decision for a series write's recomputed conflicts (see the module
    docstring): the :func:`evaluate_override` rule once per occurrence, with
    the codes paired with that occurrence's instant. Pure."""
    by_start: dict[datetime, list[Conflict]] = {}
    for item in conflicts:
        by_start.setdefault(_utc(item.occurrence_start), []).append(item.conflict)
    listed: dict[datetime, set[str]] = {}
    if override is not None:
        for pair in override.pairs:
            listed.setdefault(_utc(pair.occurrence_start), set()).add(pair.code)
    overridden: list[OverriddenOccurrence] = []
    for start in sorted(by_start):
        decision = _decide(
            by_start[start], None if override is None else listed.get(start, set())
        )
        if not decision.allowed:
            return SeriesOverrideDecision(allowed=False)
        if decision.overridden:
            overridden.append(OverriddenOccurrence(start, decision.overridden))
    return SeriesOverrideDecision(allowed=True, occurrences=tuple(overridden))


def overridden_codes(conflicts: Sequence[Conflict]) -> list[str]:
    """The codes of ``conflicts`` once each, in their (rule) order."""
    return list(dict.fromkeys(conflict.code for conflict in conflicts))


async def record_session_override(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    conflicts: Sequence[Conflict],
    reason: str,
    *,
    actor_id: uuid.UUID,
    class_series_id: uuid.UUID | None = None,
) -> ClassSessionOverride:
    """Store the override of ``conflicts`` (the computed, sorted soft
    conflicts) on the session and write its audit event, in the caller's
    transaction. ``reason`` is the trimmed reason; ``class_series_id`` is the
    context of an occurrence's override (#236 D1)."""
    row = _override_row(
        class_session_id,
        conflicts,
        reason,
        actor_id=actor_id,
        class_series_id=class_series_id,
    )
    codes, wire = row.codes, row.conflicts
    session.add(row)
    await session.flush()
    record_audit_event(
        session,
        action=OVERRIDE_EVENT,
        record_type="class_sessions",
        record_id=class_session_id,
        details={
            "override_id": row.id,
            "codes": codes,
            "conflicts": wire,
            "reason": reason,
        },
    )
    return row


def _override_row(
    class_session_id: uuid.UUID,
    conflicts: Sequence[Conflict],
    reason: str,
    *,
    actor_id: uuid.UUID,
    class_series_id: uuid.UUID | None,
) -> ClassSessionOverride:
    if not conflicts or any(c.severity != "soft" for c in conflicts):
        raise ValueError("only soft conflicts are overridden")
    return ClassSessionOverride(
        id=uuid.uuid7(),
        class_session_id=class_session_id,
        class_series_id=class_series_id,
        codes=overridden_codes(conflicts),
        conflicts=[{"code": c.code, "details": c.details} for c in conflicts],
        reason=reason,
        actor_user_id=actor_id,
    )


def _iso_z(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


async def record_series_overrides(
    session: AsyncSession,
    *,
    class_series_id: uuid.UUID,
    decision: SeriesOverrideDecision,
    reason: str,
    write: SeriesWrite,
    actor_id: uuid.UUID,
) -> list[ClassSessionOverride]:
    """Store an allowed series override (#236 C4, D1-D3), after the write's
    occurrences are flushed: one row per overridden occurrence of
    ``class_series_id`` (the write's series: the new one after a split),
    found by its rule instant (``original_start``, unique in a series), all
    inserted with one flush, then one ``scheduling.series_override`` event
    on the series. Nothing is written when nothing was overridden. For
    #56's group series create too."""
    if not decision.allowed:
        raise ValueError("a refused override is never recorded")
    if not decision.occurrences:
        return []
    starts = [item.occurrence_start for item in decision.occurrences]
    ids = {
        _utc(start): session_id
        for session_id, start in await session.execute(
            select(ClassSession.id, ClassSession.original_start).where(
                ClassSession.series_id == class_series_id,
                ClassSession.original_start.in_(starts),
            )
        )
        if start is not None
    }
    rows = [
        _override_row(
            ids[item.occurrence_start],
            item.conflicts,
            reason,
            actor_id=actor_id,
            class_series_id=class_series_id,
        )
        for item in decision.occurrences
    ]
    session.add_all(rows)
    await session.flush()
    record_audit_event(
        session,
        action=SERIES_OVERRIDE_EVENT,
        record_type="class_series",
        record_id=class_series_id,
        details={
            "write": write,
            "occurrence_count": len(rows),
            "occurrences": [
                {
                    "override_id": row.id,
                    "class_session_id": row.class_session_id,
                    "occurrence_start": _iso_z(item.occurrence_start),
                    "codes": row.codes,
                }
                for row, item in zip(rows, decision.occurrences, strict=True)
            ],
            "reason": reason,
        },
    )
    return rows
