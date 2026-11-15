"""Learning plan rules (#81 A3, C, D, E, F). Pure functions, no I/O.

- The limits (:data:`OBJECTIVES_MAX`, :data:`MILESTONES_PER_OBJECTIVE_MAX`,
  :data:`MILESTONES_PER_PLAN_MAX`, the text maximums); the database CHECKs
  repeat the stored ones.
- :func:`progress`: ``(done, total, percent)`` with ``Decimal`` and
  ``ROUND_HALF_UP`` to whole percent (E2); :func:`is_overdue`.
- :func:`plan_relation`: how the caller relates to one plan (E4): a
  ``learning_plan.manage`` holder (``manager``), a tutor currently teaching
  the student in the plan's subject (``subject_tutor``), a tutor currently
  teaching the student another subject (``assigned_tutor``), a tutor who
  created the plan and no longer teaches the student (``former_creator``),
  the student or a guardian with academic access (``family``), or
  ``none``. The first that applies wins (each reads a superset of the
  next).
- :func:`family_visible` (E3): active, completed, and discontinued plans
  that were once active; never a draft or a deleted plan.
- :func:`can_read`, :func:`access_block` (the ``403`` / ``404`` of a write,
  before the row lock), :func:`state_block` (the ``409`` against the locked
  plan's state; :func:`transition_block` for the three transitions) and
  :func:`write_block`, which combines them for the ``can_*`` flags (F3).
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Final, Literal

OBJECTIVES_MAX: Final = 10
MILESTONES_PER_OBJECTIVE_MAX: Final = 10
MILESTONES_PER_PLAN_MAX: Final = 50
TITLE_MAX: Final = 120
ITEM_TITLE_MAX: Final = 200
DESCRIPTION_MAX: Final = 2000
OBJECTIVE_DESCRIPTION_MAX: Final = 1000
ACTIVITIES_MAX: Final = 2000
LIST_MAX: Final = 100
EARLIEST_DATE: Final = date(2000, 1, 1)
"""``due_on_invalid`` before it (C2)."""

DRAFT: Final = "draft"
ACTIVE: Final = "active"
COMPLETED: Final = "completed"
DISCONTINUED: Final = "discontinued"
STATUSES: Final = (DRAFT, ACTIVE, COMPLETED, DISCONTINUED)
CLOSED: Final = frozenset({COMPLETED, DISCONTINUED})

PENDING: Final = "pending"
IN_PROGRESS: Final = "in_progress"
DONE: Final = "done"
MILESTONE_STATUSES: Final = (PENDING, IN_PROGRESS, DONE)

TUTOR_ROLE: Final = "tutor"
LEARNING_PLAN_MANAGE: Final = "learning_plan.manage"
"""The identity catalog's codes (repeated here: ADR-0002)."""

NOT_FOUND: Final = "not_found"
PLAN_SUBJECT_NOT_ASSIGNED: Final = "plan_subject_not_assigned"
PLAN_READ_ONLY: Final = "plan_read_only"
PLAN_CLOSED: Final = "plan_closed"
PLAN_NOT_ACTIVE: Final = "plan_not_active"
PLAN_NOT_DRAFT: Final = "plan_not_draft"

type PlanAction = Literal[
    "edit", "progress", "delete", "activate", "complete", "discontinue"
]
type TransitionAction = Literal["activate", "complete", "discontinue"]
type AccessBlock = Literal["not_found", "plan_subject_not_assigned", "plan_read_only"]
type StateBlock = Literal["plan_closed", "plan_not_active", "plan_not_draft"]

ACTIONS: Final[tuple[PlanAction, ...]] = (
    "edit",
    "progress",
    "delete",
    "activate",
    "complete",
    "discontinue",
)
TRANSITIONS: Final[tuple[TransitionAction, ...]] = (
    "activate",
    "complete",
    "discontinue",
)

_WHOLE: Final = Decimal(1)
_HUNDRED: Final = Decimal(100)


class PlanRelation(Enum):
    """How the caller relates to one plan (E4)."""

    MANAGER = "manager"
    SUBJECT_TUTOR = "subject_tutor"
    ASSIGNED_TUTOR = "assigned_tutor"
    FORMER_CREATOR = "former_creator"
    FAMILY = "family"
    NONE = "none"


WRITERS: Final = frozenset({PlanRelation.MANAGER, PlanRelation.SUBJECT_TUTOR})
STAFF_READERS: Final = frozenset(
    {
        PlanRelation.MANAGER,
        PlanRelation.SUBJECT_TUTOR,
        PlanRelation.ASSIGNED_TUTOR,
        PlanRelation.FORMER_CREATOR,
    }
)


# --- E2: progress ---------------------------------------------------------------------


def progress(statuses: Iterable[str]) -> tuple[int, int, Decimal]:
    """``(done, total, percent)`` of milestone statuses; ``percent`` is
    ``done / total × 100`` rounded half-up to a whole number (``Decimal``,
    never a float). An empty plan or objective never occurs (C2); it gives
    ``(0, 0, 0)``."""
    done = 0
    total = 0
    for status in statuses:
        total += 1
        if status == DONE:
            done += 1
    if total == 0:
        return 0, 0, Decimal(0)
    percent = (Decimal(done) * _HUNDRED / Decimal(total)).quantize(
        _WHOLE, rounding=ROUND_HALF_UP
    )
    return done, total, percent


def is_overdue(
    status: str, due_on: date | None, today: date, *, plan_status: str
) -> bool:
    """A milestone that isn't ``done`` whose ``due_on`` is before the
    center's today, while its plan is draft or active. A completed or
    discontinued plan has no overdue milestones (#332)."""
    if plan_status in CLOSED:
        return False
    return status != DONE and due_on is not None and due_on < today


# --- E3, E4: relation and reads -------------------------------------------------------


def plan_relation(
    *,
    manager: bool,
    tutor: bool,
    student_assigned: bool,
    subject_assigned: bool,
    is_creator: bool,
    family: bool,
) -> PlanRelation:
    """E4. ``student_assigned``: the plan's student is one of the caller's
    current assignments (``scheduling.service.student_ids_for_tutor``);
    ``subject_assigned``: also in the plan's subject
    (``subject_ids_for_tutor_student``); both ``False`` without the
    ``tutor`` role. ``family``: the #67 family rule allows the caller (the
    student, or a linked guardian with academic access)."""
    if manager:
        return PlanRelation.MANAGER
    if tutor and student_assigned and subject_assigned:
        return PlanRelation.SUBJECT_TUTOR
    if tutor and student_assigned:
        return PlanRelation.ASSIGNED_TUTOR
    if tutor and is_creator:
        return PlanRelation.FORMER_CREATOR
    if family:
        return PlanRelation.FAMILY
    return PlanRelation.NONE


def family_visible(status: str, activated_at: datetime | None, deleted: bool) -> bool:
    """E3: what a family ever sees. Drafts never; a discontinued plan only
    when it was activated first; a deleted plan never."""
    if deleted:
        return False
    if status in (ACTIVE, COMPLETED):
        return True
    return status == DISCONTINUED and activated_at is not None


def can_read(
    relation: PlanRelation,
    *,
    status: str,
    activated_at: datetime | None,
    deleted: bool,
) -> bool:
    """The matrix's read columns. The caller decides who a former creator
    reads (only plans they created: :func:`plan_relation` gives
    ``former_creator`` for those only)."""
    if deleted:
        return False
    if relation in STAFF_READERS:
        return True
    if relation is PlanRelation.FAMILY:
        return family_visible(status, activated_at, deleted)
    return False


# --- C, D: writes ---------------------------------------------------------------------


def access_block(relation: PlanRelation) -> AccessBlock | None:
    """The step-4 answer of a write by a caller who reads the plan (C4, D1,
    D2, D3): a tutor who teaches the student another subject ``403
    plan_subject_not_assigned``; a former creator ``403 plan_read_only``;
    a family member (or anyone else) the ``404`` of an unknown id."""
    if relation in WRITERS:
        return None
    if relation is PlanRelation.ASSIGNED_TUTOR:
        return PLAN_SUBJECT_NOT_ASSIGNED
    if relation is PlanRelation.FORMER_CREATOR:
        return PLAN_READ_ONLY
    return NOT_FOUND


def transition_block(status: str, action: TransitionAction) -> StateBlock | None:
    """E3: ``draft → active`` (``activate``), ``active → completed``
    (``complete``), ``draft | active → discontinued`` (``discontinue``).
    A closed plan answers ``plan_closed`` to every action; activating an
    active plan ``plan_not_draft``; completing a draft ``plan_not_active``."""
    if status in CLOSED:
        return PLAN_CLOSED
    if action == "activate":
        return None if status == DRAFT else PLAN_NOT_DRAFT
    if action == "complete":
        return None if status == ACTIVE else PLAN_NOT_ACTIVE
    return None


def state_block(status: str, action: PlanAction) -> StateBlock | None:
    """The ``409`` of a write against the locked plan: closed plans are
    read-only; progress is tracked only while ``active``; only a draft is
    deleted."""
    if action == "delete":
        return None if status == DRAFT else PLAN_NOT_DRAFT
    if action == "edit":
        return PLAN_CLOSED if status in CLOSED else None
    if action == "progress":
        if status in CLOSED:
            return PLAN_CLOSED
        return None if status == ACTIVE else PLAN_NOT_ACTIVE
    return transition_block(status, action)


def write_block(
    relation: PlanRelation, *, status: str, deleted: bool, action: PlanAction
) -> AccessBlock | StateBlock | None:
    """Every reason a write would be refused, in the route order (a deleted
    plan is ``not_found``). ``None``: the ``can_*`` flag is true."""
    if deleted:
        return NOT_FOUND
    blocked = access_block(relation)
    if blocked is not None:
        return blocked
    return state_block(status, action)


def clean(value: str | None) -> str | None:
    """Trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
