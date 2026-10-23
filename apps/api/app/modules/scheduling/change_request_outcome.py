"""The outcome of a change request (#62 B2 step 10, C2 step 10, C5;
ADR-0005 D6; #61).

Pure functions, no I/O.

- **Request-time outcome:** ``classify_change`` (#61) with the policy
  version in force when the request was made, for the session's start at
  that time; stored with the request and **never recomputed**.
- **Effective outcome** (:func:`effective_outcome`), fixed on approval: the
  request-time outcome, or with an **exception** the ``excused`` outcome of
  the same stored policy version for the same start and request time
  (``initiated_by = "center"``): a cancellation gets a makeup until the
  start + ``makeup_window_days``, a reschedule never does (#61). An
  exception on a request that is already ``excused`` (every tutor request)
  changes nothing and is refused (:func:`exception_applicable`).
- **Shown outcome** (:func:`shown_outcome`): the effective one once
  ``approved``; otherwise the request-time one, "as requested" (a rejected
  or withdrawn request has no billing effect).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.modules.administration import service as administration
from app.modules.scheduling.models import (
    CHANGE_REQUEST_APPROVED,
    SessionChangeRequest,
)

EXCUSED: Final = "excused"


@dataclass(frozen=True)
class Outcome:
    """A classification and its makeup eligibility."""

    classification: str
    makeup_allowed: bool
    makeup_until: datetime | None


def request_outcome(row: SessionChangeRequest) -> Outcome:
    """The stored request-time outcome."""
    return Outcome(row.classification, row.makeup_allowed, row.makeup_until)


def final_outcome(row: SessionChangeRequest) -> Outcome | None:
    """The stored effective outcome (only when approved)."""
    if row.final_classification is None or row.final_makeup_allowed is None:
        return None
    return Outcome(
        row.final_classification, row.final_makeup_allowed, row.final_makeup_until
    )


def shown_outcome(row: SessionChangeRequest) -> Outcome:
    """C5: the effective outcome once approved, else the request-time one."""
    if row.status == CHANGE_REQUEST_APPROVED:
        final = final_outcome(row)
        if final is not None:
            return final
    return request_outcome(row)


def exception_applicable(classification: str) -> bool:
    """An exception makes the outcome ``excused``; it has no effect (and is
    refused) when the request is already ``excused``."""
    return classification != EXCUSED


def effective_outcome(
    *,
    stored: Outcome,
    exception: bool,
    kind: str,
    policy: administration.SchedulingPolicyValues | None,
    session_starts_at: datetime,
    requested_at: datetime,
) -> Outcome:
    """C2 step 10 (module docstring). ``policy`` is the **stored** version
    (needed only with ``exception``)."""
    if not exception:
        return stored
    if policy is None:  # pragma: no cover - the stored version can't be deleted
        raise ValueError("an exception needs the request's policy version")
    excused = administration.classify_change(
        policy,
        kind="cancellation" if kind == "cancellation" else "reschedule",
        initiated_by="center",
        starts_at=session_starts_at,
        at=requested_at,
    )
    return Outcome(excused.classification, excused.makeup_allowed, excused.makeup_until)
