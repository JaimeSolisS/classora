"""Accrual recognition, pure (#117 E3-E8; ADR-0006 E7, ADR-0017 E18).

No I/O: :mod:`~app.modules.reporting.finance` reads the rows and passes plain
values here. Every function returns :class:`Share` items whose amounts sum
**exactly** to the net they were given; a share dated ``None`` is not
recognized yet (deferred, with no date).

- :func:`package_shares` (E4): each consumption on its session's local date
  at ``amount.prorate(C_k, G) − amount.prorate(C_(k−1), G)`` (the ``allocate``
  prefix property), ordered by session start then transaction id; the rest on
  ``expires_on`` (an ``expire`` row, breakage), or on the closing date (a
  cancellation ``reversal`` or a withdrawal ``refund`` units row: the
  remainder plus the credit, which may be negative when the credit exceeds
  the remainder), else deferred.
- :func:`course_shares` (E5): equal ``split`` shares over the counted
  occurrences starting after ``enrolled_at`` and, when withdrawn, at or before
  ``withdrawn_at``, each on its local date; an empty window recognizes the
  whole net on ``service_date``.
- :func:`charge_shares` (E7 and the dating table of the issue): one charge,
  any kind.
- :func:`cost_class` (E8): the class type of a payroll line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from app.core.money import Money, split

type ClassType = Literal["individual", "group", "course", "unallocated"]
type DeferredBucket = Literal["packages", "courses", "scheduled_sessions", "other"]

CLASS_ROWS: Final[tuple[ClassType, ...]] = (
    "individual",
    "group",
    "course",
    "unallocated",
)
SESSION_LINE_KINDS: Final = frozenset(
    {"session", "late_cancellation", "no_show", "makeup"}
)


@dataclass(frozen=True)
class Share:
    """A part of a net recognized on ``on`` (``None``: deferred, undated)."""

    on: date | None
    amount: Money


@dataclass(frozen=True)
class Consumption:
    """One ``consume`` row: the consumed session's start (ordering), its
    local date, the units (positive) and the transaction id (tie-break)."""

    starts_at: datetime
    on: date
    units: int
    order_key: str = ""


def package_shares(
    *,
    amount: Money,
    net: Money,
    units_granted: int,
    consumptions: Sequence[Consumption],
    expired_on: date | None,
    closed_on: date | None,
) -> list[Share]:
    """E4. ``amount`` is the purchase charge's amount (the base), ``net`` its
    current net (amount + the cancellation or withdrawal credit).
    ``expired_on`` is the package's ``expires_on`` when it has an ``expire``
    row, ``closed_on`` the local posting date of its cancellation or
    withdrawal; both ``None`` leave the remainder deferred."""
    shares: list[Share] = []
    cumulative = 0
    before = Money.zero()
    for item in sorted(consumptions, key=lambda c: (c.starts_at, c.order_key)):
        cumulative += item.units
        now = amount.prorate(min(cumulative, units_granted), units_granted)
        shares.append(Share(item.on, now - before))
        before = now
    remainder = net - before
    if expired_on is not None:
        shares.append(Share(expired_on, remainder))
    elif closed_on is not None:
        shares.append(Share(closed_on, remainder))
    else:
        shares.append(Share(None, remainder))
    return shares


@dataclass(frozen=True)
class Occurrence:
    """A counted occurrence of a cohort: its instant and its local date."""

    starts_at: datetime
    on: date


def course_shares(
    *,
    net: Money,
    occurrences: Sequence[Occurrence],
    enrolled_at: datetime,
    withdrawn_at: datetime | None,
    service_date: date,
) -> list[Share]:
    """E5: the enrollment's window over the cohort's counted occurrences."""
    window = sorted(
        (
            o
            for o in occurrences
            if o.starts_at > enrolled_at
            and (withdrawn_at is None or o.starts_at <= withdrawn_at)
        ),
        key=lambda o: o.starts_at,
    )
    if not window:
        return [Share(service_date, net)]
    return [
        Share(o.on, part)
        for o, part in zip(window, split(net, len(window)), strict=True)
    ]


@dataclass(frozen=True)
class RecognizedCharge:
    """A charge's shares with its class type (E7) and its deferred bucket."""

    class_type: ClassType
    bucket: DeferredBucket
    shares: list[Share]


def revenue_class(
    kind: str, *, session_kind: str | None, tuition_class_type: str | None
) -> ClassType:
    """E7: the class type of a charge's revenue."""
    if kind == "session":
        return _class(session_kind)
    if kind == "package_purchase":
        return "individual"
    if kind == "group_enrollment":
        return "group"
    if kind == "cohort_enrollment":
        return "course"
    if kind == "tuition_period":
        return _class(tuition_class_type)
    return "unallocated"


def deferred_bucket(kind: str) -> DeferredBucket:
    """The deferred-revenue group of a charge kind (#117 E9)."""
    if kind == "package_purchase":
        return "packages"
    if kind == "cohort_enrollment":
        return "courses"
    if kind == "session":
        return "scheduled_sessions"
    return "other"


def dated_shares(
    *, kind: str, net: Money, service_date: date, session_on: date | None
) -> list[Share]:
    """The whole net on one date: a ``session`` charge on its session's
    **current** local date (a reschedule moves it), every other single-date
    kind on ``service_date``."""
    if kind == "session" and session_on is not None:
        return [Share(session_on, net)]
    return [Share(service_date, net)]


def cost_class(
    *,
    line_kind: str,
    line_class_kind: str | None,
    adjustment_kind: str | None,
    adjustment_class_kind: str | None,
    origin_line_kind: str | None,
    origin_class_kind: str | None,
    adjustment_session_kind: str | None,
) -> ClassType:
    """The class type of a finalized payroll line (ADR-0017 E18; #117 E8):

    - session-sourced lines: their ``class_kind``;
    - ``salary`` and ``pay_item``: unallocated;
    - ``adjustment`` lines: ``late_approval``/``hours_correction`` their
      snapshot; a ``manual`` on a line that line's kind when it is
      session-sourced (else unallocated); a ``manual`` on unresolved work the
      session's kind; a free ``manual`` and ``carry_forward`` unallocated.
    """
    if line_kind in SESSION_LINE_KINDS:
        return _class(line_class_kind)
    if line_kind != "adjustment":
        return "unallocated"
    if adjustment_kind in ("late_approval", "hours_correction"):
        return _class(adjustment_class_kind)
    if adjustment_kind == "manual":
        if origin_line_kind is not None:
            if origin_line_kind in SESSION_LINE_KINDS:
                return _class(origin_class_kind)
            return "unallocated"
        if adjustment_session_kind is not None:
            return _class(adjustment_session_kind)
    return "unallocated"


def _class(value: str | None) -> ClassType:
    if value in ("individual", "group", "course"):
        return value  # type: ignore[return-value]
    return "unallocated"
