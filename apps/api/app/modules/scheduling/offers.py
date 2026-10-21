"""Assignment offer rules (#52 A3, A4, B1-B3, E1; ADR-0005 D2, D3).

Pure rules, no database; the writes are in
:mod:`~app.modules.scheduling.offer_flow`.

**Invariant (A3).** A session is ``offered`` **iff** it is covered by exactly
one ``pending`` offer, and that offer's ``tutor_user_id`` is the session's
tutor. Postgres can't express it across the two tables, so every writer
keeps it: an offer is created in the same transaction that moves its
session to ``offered``, and accepting, declining, expiring or withdrawing an
offer moves its still-``offered`` sessions out of ``offered`` in the same
transaction. The integration tests assert it after every path.

**Lock order (A4).** Every offer writer (create, accept, decline, expire,
withdraw, and the reassignment item's "Mark resolved") locks the covered
``class_sessions`` rows ``FOR UPDATE`` in ascending id **first**, **then**
the ``assignment_offers`` row ``FOR UPDATE``. Writers therefore queue on the
session rows and can't deadlock; a second pending offer for a session can't
be created because its creator waits on the same session row and then sees
it ``offered``.

**Statuses (B1).** ``pending → accepted | declined | expired | withdrawn``;
the other four are final.

**Deadline (B2, B3).** ``respond_by`` defaults to the scheduling policy's
:func:`~app.modules.administration.service.default_respond_by` at
``offered_at`` (``min(offered_at + response_hours, first start)``, #61). A
requested earlier deadline must be after ``offered_at`` and no later than
that default. Both are truncated to whole minutes. An offer is overdue from
``respond_by`` on (``now >= respond_by``).
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final

from app.modules.administration import service as administration
from app.modules.scheduling.models import (
    OFFER_ACCEPTED,
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_PENDING,
    OFFER_STATUSES,
    OFFER_WITHDRAWN,
)

OFFER_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    OFFER_PENDING: frozenset(
        {OFFER_ACCEPTED, OFFER_DECLINED, OFFER_EXPIRED, OFFER_WITHDRAWN}
    ),
    OFFER_ACCEPTED: frozenset(),
    OFFER_DECLINED: frozenset(),
    OFFER_EXPIRED: frozenset(),
    OFFER_WITHDRAWN: frozenset(),
}
"""The allowed offer moves (B1)."""

ANSWERED_STATUSES: Final = frozenset(OFFER_STATUSES) - {OFFER_PENDING}
"""The four final statuses (the tutor's "Answered" tab)."""

HIGH_PRIORITY_WITHIN: Final = timedelta(hours=24)
"""A reassignment item is ``high`` priority when the first covered session
starts within this time of now (E1)."""


class RespondByInvalidError(ValueError):
    """A requested ``respond_by`` is not after ``offered_at`` or later than
    the default deadline (B2; the routes answer ``422 respond_by_invalid``)."""


def can_transition_offer(from_status: str, to_status: str) -> bool:
    """Whether an offer may move from ``from_status`` to ``to_status``.
    Unknown statuses are never allowed."""
    return to_status in OFFER_TRANSITIONS.get(from_status, frozenset())


def whole_minutes(value: datetime) -> datetime:
    """``value`` truncated to the minute."""
    return value.replace(second=0, microsecond=0)


def resolve_respond_by(
    policy: administration.SchedulingPolicyValues,
    *,
    offered_at: datetime,
    first_starts_at: datetime,
    requested: datetime | None = None,
) -> datetime:
    """The offer's deadline (B2): the policy default, or ``requested`` when
    ``offered_at < requested <= default`` (both truncated to whole minutes),
    else :class:`RespondByInvalidError`."""
    default = whole_minutes(
        administration.default_respond_by(
            policy, offered_at=offered_at, first_starts_at=first_starts_at
        )
    )
    if requested is None:
        if default <= offered_at:
            raise RespondByInvalidError("the first session has already started")
        return default
    chosen = whole_minutes(requested)
    if not offered_at < chosen <= default:
        raise RespondByInvalidError("respond_by must be after now and by the default")
    return chosen


def is_overdue(respond_by: datetime, now: datetime) -> bool:
    """Whether an offer with this deadline can no longer be answered (B3):
    from ``respond_by`` on."""
    return now >= respond_by


def reassignment_priority(first_starts_at: datetime, now: datetime) -> str:
    """The reassignment item's priority (E1): ``high`` when the first covered
    session starts within :data:`HIGH_PRIORITY_WITHIN` of ``now`` (or has
    started), otherwise ``normal``."""
    return "high" if first_starts_at - now < HIGH_PRIORITY_WITHIN else "normal"
