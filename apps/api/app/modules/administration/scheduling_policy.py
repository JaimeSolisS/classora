"""The scheduling policy evaluator (#61 B; spec §3; ADR-0005 D1, D3, D6).

Pure functions over one policy version (:class:`SchedulingPolicyValues`):
no database access, no clock read and no time zone. Every ``datetime``
argument must be aware (a naive one raises ``ValueError``); the functions
compare instants, so the same moment written in two UTC offsets gives the
same answer.

- :func:`classify_change`: is a cancellation, reschedule or absence
  ``on_time``, ``late``, a ``no_show`` or ``excused``, and is a makeup
  allowed (until when)?
- :func:`violates_booking_notice`: the input of the soft ``notice_window``
  conflict (#48) and of slot filtering (#59).
- :func:`default_respond_by`: the default offer deadline (#52, ADR-0005 D3).

Consumers read the version in force with
``administration.service.get_scheduling_policy(session, at)`` and import these
names from ``administration.service`` (ADR-0002).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

ChangeKind = Literal["cancellation", "reschedule", "absence"]
"""What happened to a class: cancelled, moved, or the student was absent."""

ChangeInitiator = Literal["family", "tutor", "center"]
"""Who asked: ``family`` (a student or guardian), ``tutor``, or ``center``
(staff or the system, for example a closure or a series edit)."""

Classification = Literal["on_time", "late", "no_show", "excused"]


@dataclass(frozen=True)
class SchedulingPolicyValues:
    """One scheduling policy version (the ``scheduling_policies`` row)."""

    id: uuid.UUID
    effective_from: datetime
    booking_notice_hours: int
    change_notice_hours: int
    response_hours: int
    makeup_after_on_time: bool
    makeup_after_late: bool
    makeup_after_no_show: bool
    makeup_window_days: int


@dataclass(frozen=True)
class PolicyOutcome:
    """The classification of one change under one policy version."""

    classification: Classification
    makeup_allowed: bool
    makeup_until: datetime | None
    policy_id: uuid.UUID


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")


def _classification(
    policy: SchedulingPolicyValues,
    *,
    kind: ChangeKind,
    initiated_by: ChangeInitiator,
    starts_at: datetime,
    at: datetime,
) -> Classification:
    if initiated_by in ("tutor", "center"):
        return "excused"
    if kind == "absence":
        return "no_show"
    if at >= starts_at:
        return "no_show"
    if at > starts_at - timedelta(hours=policy.change_notice_hours):
        return "late"
    return "on_time"


def classify_change(
    policy: SchedulingPolicyValues,
    *,
    kind: ChangeKind,
    initiated_by: ChangeInitiator,
    starts_at: datetime,
    at: datetime,
) -> PolicyOutcome:
    """Classify a change of the class starting at ``starts_at``, requested
    (or, for an absence, recorded) at ``at``.

    1. ``tutor`` or ``center`` → ``excused``, whatever the time;
    2. an ``absence`` → ``no_show``;
    3. ``at >= starts_at`` → ``no_show``;
    4. ``at`` later than ``change_notice_hours`` before the start → ``late``
       (exactly the notice before the start is on time);
    5. otherwise ``on_time``.

    A ``reschedule`` never allows a makeup (it is its own replacement).
    Otherwise ``excused`` allows one, and ``on_time``/``late``/``no_show``
    follow ``makeup_after_*``. An allowed makeup must happen before
    ``starts_at + makeup_window_days`` (days of 24 hours).
    """
    _aware("starts_at", starts_at)
    _aware("at", at)
    classification = _classification(
        policy, kind=kind, initiated_by=initiated_by, starts_at=starts_at, at=at
    )
    if kind == "reschedule":
        allowed = False
    elif classification == "excused":
        allowed = True
    elif classification == "on_time":
        allowed = policy.makeup_after_on_time
    elif classification == "late":
        allowed = policy.makeup_after_late
    else:
        allowed = policy.makeup_after_no_show
    return PolicyOutcome(
        classification=classification,
        makeup_allowed=allowed,
        makeup_until=(
            starts_at + timedelta(days=policy.makeup_window_days) if allowed else None
        ),
        policy_id=policy.id,
    )


def violates_booking_notice(
    policy: SchedulingPolicyValues, *, starts_at: datetime, at: datetime
) -> bool:
    """True when a class starting at ``starts_at`` booked at ``at`` gives less
    than ``booking_notice_hours`` of notice. Exactly the notice is fine; with
    0, only a start before ``at`` violates."""
    _aware("starts_at", starts_at)
    _aware("at", at)
    return starts_at - at < timedelta(hours=policy.booking_notice_hours)


def default_respond_by(
    policy: SchedulingPolicyValues, *, offered_at: datetime, first_starts_at: datetime
) -> datetime:
    """The default deadline of an offer made at ``offered_at``:
    ``response_hours`` later, but never after the first offered class starts
    (ADR-0005 D3)."""
    _aware("offered_at", offered_at)
    _aware("first_starts_at", first_starts_at)
    return min(offered_at + timedelta(hours=policy.response_hours), first_starts_at)
