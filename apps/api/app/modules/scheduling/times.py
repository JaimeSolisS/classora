"""Class session time bounds (ADR-0005 "Time granularity"; ADR-0004; #47 B2).

Pure rules, no database. ``starts_at`` and ``ends_at`` are instants (aware
``datetime``), whole minutes; a session lasts 15–480 **elapsed** minutes and
starts and ends on the same local date in the center zone. Ending exactly at
the next local midnight is allowed (as for #45 windows). The database
enforces whole minutes and the duration with CHECKs; the local date depends
on the zone setting, so only this function checks it.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.modules.scheduling.errors import InvalidSessionTimesError

MIN_DURATION_MINUTES: Final = 15
MAX_DURATION_MINUTES: Final = 480

_ONE_MINUTE: Final = timedelta(minutes=1)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def scheduled_minutes(starts_at: datetime, ends_at: datetime) -> int:
    """The real elapsed minutes between two aware instants (a DST change
    inside the session counts as it happened).

    Both are converted to UTC first: Python subtracts two datetimes that
    share a ``tzinfo`` in wall-clock time, which would miss the change."""
    return int((ends_at.astimezone(UTC) - starts_at.astimezone(UTC)) // _ONE_MINUTE)


def validate_session_bounds(starts_at: datetime, ends_at: datetime, zone: str) -> None:
    """Raise :class:`InvalidSessionTimesError` unless the bounds are valid.

    Checked in order: ``naive_datetime``, ``not_whole_minutes``,
    ``ends_before_start`` (``ends_at <= starts_at``), ``duration_out_of_range``
    (15–480 elapsed minutes) and ``crosses_local_date`` (local start and end
    dates in ``zone`` differ, except an end exactly at the next local
    midnight).
    """
    if not (_is_aware(starts_at) and _is_aware(ends_at)):
        raise InvalidSessionTimesError("naive_datetime")
    for value in (starts_at, ends_at):
        if value.second or value.microsecond:
            raise InvalidSessionTimesError("not_whole_minutes")
    if ends_at <= starts_at:
        raise InvalidSessionTimesError("ends_before_start")
    minutes = scheduled_minutes(starts_at, ends_at)
    if not MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES:
        raise InvalidSessionTimesError("duration_out_of_range")
    tz = ZoneInfo(zone)
    local_start = starts_at.astimezone(tz)
    local_end = ends_at.astimezone(tz)
    if local_start.date() == local_end.date():
        return
    ends_at_next_midnight = (
        local_end.date() == local_start.date() + timedelta(days=1)
        and local_end.time() == datetime.min.time()
    )
    if not ends_at_next_midnight:
        raise InvalidSessionTimesError("crosses_local_date")
