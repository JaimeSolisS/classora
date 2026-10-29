"""The pure package rules (#94 D4, L4; ADR-0006 E10; data-model §4.2).

No database access: :mod:`app.modules.billing.packages` reads the rows and
calls these.

**Consumption plan (L4, first-expiring-first).** The eligible packages of
one student for one session come ordered by ``(expires_on, purchased_on,
id)``, each with its remaining units (> 0). A session is never split
between unit kinds:

- **sessions path:** the first ``sessions`` package gives **1** unit,
  whatever the session's length;
- **minutes path:** the ``minutes`` packages in order, taking
  ``min(remaining, still needed)`` from each until the session's
  ``scheduled_minutes`` are covered; possible only when their sum is
  enough;
- **choice:** both possible → the path whose first package expires first
  (a tie goes to sessions); one possible → that one; neither → ``None``
  (short: nothing is consumed, all or nothing, E10).

**Status** (D4): ``expired`` (an ``expire`` row, or ``expires_on`` before
today) → ``exhausted`` (no units left) → ``active``.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

type PackageUnit = Literal["sessions", "minutes"]
type PackageStatus = Literal["active", "exhausted", "expired"]

PACKAGE_STATUSES: Final[tuple[PackageStatus, ...]] = ("active", "exhausted", "expired")


@dataclass(frozen=True)
class EligiblePackage:
    """A package that may cover a session, with its remaining units."""

    id: uuid.UUID
    unit: PackageUnit
    remaining: int
    expires_on: date
    purchased_on: date

    @property
    def order(self) -> tuple[date, date, uuid.UUID]:
        """The first-expiring-first order (L4)."""
        return (self.expires_on, self.purchased_on, self.id)


type ConsumptionPlan = tuple[tuple[uuid.UUID, int], ...]
"""``(package_id, units taken)`` per package used, in order (> 0 each)."""


def _sessions_path(packages: Sequence[EligiblePackage]) -> ConsumptionPlan | None:
    for package in packages:
        if package.unit == "sessions" and package.remaining >= 1:
            return ((package.id, 1),)
    return None


def _minutes_path(
    packages: Sequence[EligiblePackage], needed: int
) -> ConsumptionPlan | None:
    taken: list[tuple[uuid.UUID, int]] = []
    left = needed
    for package in packages:
        if package.unit != "minutes" or package.remaining <= 0:
            continue
        take = min(package.remaining, left)
        taken.append((package.id, take))
        left -= take
        if left == 0:
            return tuple(taken)
    return None


def plan_consumption(
    packages: Sequence[EligiblePackage], scheduled_minutes: int
) -> ConsumptionPlan | None:
    """The L4 plan for one session over ``packages`` (any order: they are
    sorted first-expiring-first here), or ``None`` when neither path can
    cover the whole session (module docstring)."""
    if scheduled_minutes <= 0:
        raise ValueError("scheduled_minutes must be positive")
    ordered = sorted(packages, key=lambda p: p.order)
    by_id = {p.id: p for p in ordered}
    sessions = _sessions_path(ordered)
    minutes = _minutes_path(ordered, scheduled_minutes)
    if sessions is None or minutes is None:
        return sessions or minutes
    first_sessions = by_id[sessions[0][0]].expires_on
    first_minutes = by_id[minutes[0][0]].expires_on
    return minutes if first_minutes < first_sessions else sessions


def package_status(
    remaining: int, expires_on: date, *, has_expire_row: bool, today: date
) -> PackageStatus:
    """D4: ``expired`` wins (an ``expire`` row or ``expires_on < today``),
    then ``exhausted`` (``remaining <= 0``), else ``active``."""
    if has_expire_row or expires_on < today:
        return "expired"
    if remaining <= 0:
        return "exhausted"
    return "active"
