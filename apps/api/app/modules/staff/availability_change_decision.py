"""What approving an availability change request does in ``staff`` (M4-18
#63 D3 steps 2, 3 and 5).

The ``staff.availability_change`` handler (``app/workflows/
availability_change.py``) calls, in this order, after locking the request:

1. :func:`lock_availability`: the tutor's settings row ``FOR UPDATE`` for a
   weekly request (the row the weekly ``PUT`` locks), nothing otherwise;
2. (the workflow takes ``scheduling.service.lock_tutor``);
3. :func:`plan_change`: ``409 availability_change_stale`` when a weekly
   request's ``base_version`` is not the current version, or the exception
   a delete request names no longer exists; otherwise the tutor's
   **current** rules and the **proposed** ones (as they are now, so the
   workflow recomputes the affected sessions) and the dates an exception
   touches;
4. (the workflow recomputes the affected sessions);
5. :func:`apply_change`: the same writes as a direct change (the weekly
   windows with the version bump, the exception insert or delete), so the
   same audit events, with the approver as actor.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.staff.availability import TutorAvailabilityRules
from app.modules.staff.errors import AvailabilityChangeStaleError
from app.modules.staff.models import (
    CHANGE_EXCEPTION_CREATE,
    CHANGE_WEEKLY,
    TutorAvailabilityChangeRequest,
    TutorAvailabilityException,
)
from app.modules.staff.schemas import AvailabilityExceptionCreate
from app.modules.staff.tutor_availability import (
    WindowKey,
    _apply_weekly,
    _exception_row,
    _exceptions,
    _flush,
    _insert_exception,
    _key,
    _rule_exception,
    _rules,
    _settings,
    _windows,
)


@dataclass(frozen=True, slots=True)
class ChangePlan:
    """The rules before and after a change, and the local dates an
    exception change touches (``None`` for a weekly change)."""

    current: TutorAvailabilityRules
    proposed: TutorAvailabilityRules
    dates: tuple[date, date] | None


def _wanted(row: TutorAvailabilityChangeRequest) -> set[WindowKey]:
    return {
        (
            item["weekday"],
            time.fromisoformat(item["starts_at"]),
            time.fromisoformat(item["ends_at"]),
        )
        for item in row.payload["windows"]
    }


def _new_exception(row: TutorAvailabilityChangeRequest) -> TutorAvailabilityException:
    return _exception_row(
        row.tutor_user_id, AvailabilityExceptionCreate.model_validate(row.payload)
    )


async def _named_exception(
    session: AsyncSession, row: TutorAvailabilityChangeRequest
) -> TutorAvailabilityException:
    """The exception a delete request names, ``FOR UPDATE``; stale when it
    is gone."""
    found: TutorAvailabilityException | None = await session.scalar(
        select(TutorAvailabilityException)
        .where(TutorAvailabilityException.id == uuid.UUID(row.payload["exception_id"]))
        .where(TutorAvailabilityException.tutor_user_id == row.tutor_user_id)
        .with_for_update()
    )
    if found is None:
        raise AvailabilityChangeStaleError()
    return found


async def lock_availability(
    session: AsyncSession, row: TutorAvailabilityChangeRequest
) -> None:
    """Step 1: the settings row ``FOR UPDATE`` for a weekly request."""
    if row.kind == CHANGE_WEEKLY:
        await _settings(session, row.tutor_user_id, for_update=True)


async def plan_change(
    session: AsyncSession, row: TutorAvailabilityChangeRequest
) -> ChangePlan:
    """Step 3 (see the module docstring)."""
    tutor = row.tutor_user_id
    windows = {_key(window) for window in await _windows(session, tutor)}
    if row.kind == CHANGE_WEEKLY:
        settings = await _settings(session, tutor)
        current_version = settings.version if settings is not None else 0
        if current_version != row.payload["base_version"]:
            raise AvailabilityChangeStaleError()
        exceptions = [
            _rule_exception(item) for item in await _exceptions(session, tutor)
        ]
        return ChangePlan(
            current=_rules(windows, exceptions),
            proposed=_rules(_wanted(row), exceptions),
            dates=None,
        )
    if row.kind == CHANGE_EXCEPTION_CREATE:
        added = _new_exception(row)
        dates = (added.starts_on, added.ends_on)
        existing = [
            _rule_exception(item) for item in await _exceptions(session, tutor, dates)
        ]
        return ChangePlan(
            current=_rules(windows, existing),
            proposed=_rules(windows, [*existing, _rule_exception(added)]),
            dates=dates,
        )
    removed = await _named_exception(session, row)
    dates = (removed.starts_on, removed.ends_on)
    existing_rows = await _exceptions(session, tutor, dates)
    return ChangePlan(
        current=_rules(windows, [_rule_exception(item) for item in existing_rows]),
        proposed=_rules(
            windows,
            [_rule_exception(item) for item in existing_rows if item.id != removed.id],
        ),
        dates=dates,
    )


async def apply_change(
    session: AsyncSession, row: TutorAvailabilityChangeRequest
) -> None:
    """Step 5: write the change as a direct change would."""
    tutor = row.tutor_user_id
    if row.kind == CHANGE_WEEKLY:
        settings = await _settings(session, tutor, for_update=True)
        await _apply_weekly(
            session, tutor, settings, await _windows(session, tutor), _wanted(row)
        )
    elif row.kind == CHANGE_EXCEPTION_CREATE:
        await _insert_exception(session, _new_exception(row))
    else:
        await session.delete(await _named_exception(session, row))
        await _flush(session)
