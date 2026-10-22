"""Substitution rules (#55 A3, B1, F3, G1; ADR-0005 D2, D3 amendment).

Pure rules, no database; the writes are in
:mod:`~app.modules.scheduling.substitution_flow` (substitute, withdraw,
answer) and :mod:`~app.modules.scheduling.cancellation` (cancel one
occurrence).

**What a substitution is (E1).** A one-occurrence offer of kind
``substitution`` to another tutor for a ``confirmed`` session that hasn't
started. While it is pending the session **stays ``confirmed`` with its
original tutor** (no ``confirmed → offered`` transition; families never
lose sight of it). The substitute accepting swaps the tutor in one
transaction; a decline or an expiry leaves the session as it is and raises
the #52 reassignment inbox item.

**Invariants (A3)**, service-enforced under the #52 lock order and asserted
by the integration tests after every path:

- (#52 A3, restated) a session is ``offered`` **iff** it is covered by
  exactly one pending offer of kind **``assignment``**, made to the
  session's tutor;
- a session has **at most one** pending ``substitution`` offer, and only
  while it is ``confirmed``, its tutor is the substitution's
  ``original_tutor_user_id`` and its times equal the snapshot
  (``slot_starts_at`` / ``slot_ends_at``). Every ``substitution`` offer has
  exactly one ``class_session_substitutions`` row.

Every writer that moves a ``confirmed`` row (``PATCH``, series edits and
cancellations, the closure skip, the cancel) withdraws its pending
substitution in the same transaction (G3); the accept's validity re-check
is the safety net for a writer added later.

**Lock order (G1)**, the #52 A4 order extended:

- substitution create, withdraw and the substitute's answers (and the
  expiry): the session ``FOR UPDATE``, then the offer ``FOR UPDATE``;
- cancelling one occurrence: the series row ``FOR UPDATE`` when the row
  belongs to a series (the #233 B3 step 3 position), then the session
  ``FOR UPDATE``, then the offers.

Neither ever takes a series row after a session row, so neither can
deadlock with the series offers (#233), the series edits (#54) or the
nightly materialization.
"""

from datetime import datetime
from typing import Final, Literal

from app.modules.scheduling.states import SessionState

SubstitutionRefusal = Literal["state", "started", "substitution_pending"]
CancelRefusal = Literal["state", "started"]
CancelAudience = Literal["tutor", "family"]

CANCELLABLE_STATES: Final = frozenset(
    {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.CONFLICT,
    }
)
"""The states a session can be cancelled from (D2 ``→ cancelled``); a
``skipped_closure`` occurrence holds nothing and isn't cancelled (C1
step 6)."""


def substitution_refusal(
    state: str, starts_at: datetime, now: datetime, *, has_pending: bool
) -> SubstitutionRefusal | None:
    """Why a session can't get a substitute now (B1), or ``None``: ``state``
    unless it is ``confirmed``, ``started`` from ``starts_at`` on, then
    ``substitution_pending`` while one is pending."""
    if state != SessionState.CONFIRMED:
        return "state"
    if starts_at <= now:
        return "started"
    if has_pending:
        return "substitution_pending"
    return None


def cancel_refusal(
    state: str, starts_at: datetime, now: datetime
) -> CancelRefusal | None:
    """Why a session can't be cancelled by the center now (C1 step 6), or
    ``None``: ``state`` for ``completed``, ``cancelled`` and
    ``skipped_closure`` (and any unknown state), ``started`` from
    ``starts_at`` on (what happens during or after a lesson is attendance,
    M5)."""
    try:
        known = SessionState(state)
    except ValueError:
        return "state"
    if known not in CANCELLABLE_STATES:
        return "state"
    if starts_at <= now:
        return "started"
    return None


def cancel_audience(previous_state: str) -> frozenset[CancelAudience]:
    """Who hears about a cancellation, from the state before it (F3): the
    tutor of an ``offered`` or ``confirmed`` session, and the family of a
    ``confirmed`` one only (D2 family visibility). Nobody for
    ``unassigned`` or ``conflict`` rows. A pending substitute is told as
    well (the caller adds them)."""
    if previous_state == SessionState.CONFIRMED:
        return frozenset({"tutor", "family"})
    if previous_state == SessionState.OFFERED:
        return frozenset({"tutor"})
    return frozenset()


def substitution_still_valid(
    *,
    state: str,
    tutor_user_id: object,
    starts_at: datetime,
    ends_at: datetime,
    original_tutor_user_id: object,
    slot_starts_at: datetime,
    slot_ends_at: datetime,
) -> bool:
    """D1's re-check at accept: the session is still ``confirmed`` with the
    original tutor and the snapshot's times."""
    return (
        state == SessionState.CONFIRMED
        and tutor_user_id == original_tutor_user_id
        and starts_at == slot_starts_at
        and ends_at == slot_ends_at
    )
