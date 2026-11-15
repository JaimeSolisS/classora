"""Evaluation rules (#80 A3, C, D, E, F). Pure functions, no I/O.

- The limits (:data:`CRITERIA_MAX`, :data:`MAX_SCORE_MIN`,
  :data:`MAX_SCORE_MAX`, :data:`FEEDBACK_MAX`, :data:`COMMENT_MAX`,
  :data:`REASON_MAX`, :data:`HISTORY_MAX_ENTRIES`, …); the database CHECKs
  repeat the stored ones.
- :func:`score_totals`: ``(total, max_total, percent)`` with ``Decimal``
  and ``ROUND_HALF_UP`` to two places, never a float (E2).
- :func:`evaluation_relation`: how the caller relates to one evaluation
  (E5): its ``author`` while the student is one of their current
  assignments, its ``former_author`` once not, an ``evaluation.manage``
  holder (``reviewer``), another tutor whose current assignments include
  the student (``assigned_tutor``), or ``none``. The first that applies
  wins (a reviewer reads a superset of what an assigned tutor reads).
- :func:`can_read`: drafts reach only their author; a voided evaluation
  its author and reviewers; a final one also the assigned tutors.
- :func:`write_block`: the ``403`` of a write (D3 step 4), before the row
  lock; :func:`state_block`: the ``409`` of a write against the locked
  row's state (D3 step 7). :func:`can` combines them for the ``can_*``
  flags (D8).
- :func:`period_for`: the period of one type whose inclusive range holds a
  date (E3), or ``None``.
- :func:`is_evaluable`: a linked session must be ``confirmed`` or
  ``completed`` and have started (D2 step 7).
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Final, Literal, Protocol

from app.core.authz import Principal

CRITERIA_MAX: Final = 20
MAX_SCORE_MIN: Final = Decimal("0.5")
MAX_SCORE_MAX: Final = Decimal("1000")
FEEDBACK_MAX: Final = 4000
COMMENT_MAX: Final = 1000
REASON_MAX: Final = 500
HISTORY_MAX_ENTRIES: Final = 500
TEMPLATE_NAME_MAX: Final = 120
TEMPLATE_DESCRIPTION_MAX: Final = 2000
CRITERION_TITLE_MAX: Final = 120
CRITERION_DESCRIPTION_MAX: Final = 500
ASSESSMENT_TITLE_MAX: Final = 200
EARLIEST_EVALUATED_ON: Final = date(2000, 1, 1)
TEMPLATES_MAX: Final = 200

KINDS: Final = ("diagnostic", "ongoing")
DRAFT: Final = "draft"
FINAL: Final = "final"
STATUSES: Final = (DRAFT, FINAL)
GROUP_BY: Final = ("none", "school_year", "semester", "term")

EVALUABLE_STATES: Final = frozenset({"confirmed", "completed"})
"""D2 step 7: a linked session is ``confirmed`` or ``completed`` and has
started. Compared as text (academics imports no scheduling);
``tests/unit/test_evaluation_rules.py`` checks them against
``scheduling.states.SessionState``."""

COURSE_KIND: Final = "course"

TUTOR_ROLE: Final = "tutor"
EVALUATION_MANAGE: Final = "evaluation.manage"
"""The identity catalog's codes (repeated here: ADR-0002)."""

EVALUATION_TUTOR_ONLY: Final = "evaluation_tutor_only"
EVALUATION_AUTHOR_ONLY: Final = "evaluation_author_only"
EVALUATION_READ_ONLY: Final = "evaluation_read_only"
PERMISSION_DENIED: Final = "permission_denied"
EVALUATION_FINAL: Final = "evaluation_final"
EVALUATION_NOT_DRAFT: Final = "evaluation_not_draft"
EVALUATION_NOT_FINAL: Final = "evaluation_not_final"
EVALUATION_VOIDED: Final = "evaluation_voided"

type EvaluationAction = Literal["edit", "finalize", "delete", "void"]
type WriteBlock = Literal[
    "evaluation_author_only", "evaluation_read_only", "permission_denied"
]
type StateBlock = Literal[
    "evaluation_final",
    "evaluation_not_draft",
    "evaluation_not_final",
    "evaluation_voided",
]

ACTIONS: Final[tuple[EvaluationAction, ...]] = ("edit", "finalize", "delete", "void")

_CENT: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)


class EvaluationRelation(Enum):
    """How the caller relates to one evaluation (E5)."""

    AUTHOR = "author"
    FORMER_AUTHOR = "former_author"
    REVIEWER = "reviewer"
    ASSIGNED_TUTOR = "assigned_tutor"
    NONE = "none"


def is_tutor(principal: Principal) -> bool:
    return TUTOR_ROLE in principal.roles


def is_reviewer(principal: Principal) -> bool:
    return principal.has(EVALUATION_MANAGE)


# --- Decimals -------------------------------------------------------------------------


def to_cents(value: Decimal) -> Decimal:
    """``value`` rounded half-up to two places."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def has_cents_only(value: Decimal) -> bool:
    """At most two decimal places (``1.10`` yes, ``1.005`` no)."""
    return value.is_finite() and value == value.quantize(_CENT)


def score_totals(
    scores: Iterable[tuple[Decimal, Decimal]],
) -> tuple[Decimal, Decimal, Decimal]:
    """E2: ``(Σscore, Σmax, Σscore / Σmax × 100)`` from ``(score, max)``
    pairs, each rounded half-up to two places. ``Decimal`` only (a float
    raises ``TypeError``)."""
    total = Decimal(0)
    maximum = Decimal(0)
    for score, max_score in scores:
        if not isinstance(score, Decimal) or not isinstance(max_score, Decimal):
            raise TypeError("score_totals takes Decimal values only")
        total += score
        maximum += max_score
    if maximum <= 0:
        raise ValueError("an evaluation has at least one criterion")
    return to_cents(total), to_cents(maximum), to_cents(total * _HUNDRED / maximum)


def average_percent(percents: Sequence[Decimal]) -> Decimal | None:
    """F4: the mean of ``score_percent`` values, half-up to two places
    (``None`` for none)."""
    if not percents:
        return None
    return to_cents(sum(percents, Decimal(0)) / Decimal(len(percents)))


# --- E5: relation, reads and writes ---------------------------------------------------


def evaluation_relation(
    principal: Principal, *, author_user_id: uuid.UUID, student_assigned: bool
) -> EvaluationRelation:
    """E5. ``student_assigned``: the evaluation's student is one of the
    caller's current assignments (``scheduling.service
    .student_ids_for_tutor``; ``False`` without the ``tutor`` role)."""
    tutor = is_tutor(principal)
    if tutor and principal.user_id == author_user_id:
        return (
            EvaluationRelation.AUTHOR
            if student_assigned
            else EvaluationRelation.FORMER_AUTHOR
        )
    if is_reviewer(principal):
        return EvaluationRelation.REVIEWER
    if tutor and student_assigned:
        return EvaluationRelation.ASSIGNED_TUTOR
    return EvaluationRelation.NONE


def is_own(relation: EvaluationRelation) -> bool:
    return relation in (EvaluationRelation.AUTHOR, EvaluationRelation.FORMER_AUTHOR)


def can_read(relation: EvaluationRelation, *, status: str, voided: bool) -> bool:
    """The matrix's read columns: a draft only by its author (current or
    former); a voided evaluation by its author and reviewers; a final one
    also by the tutors with the student assigned."""
    if is_own(relation):
        return True
    if status != FINAL:
        return False
    if relation is EvaluationRelation.REVIEWER:
        return True
    return relation is EvaluationRelation.ASSIGNED_TUTOR and not voided


def write_block(
    action: EvaluationAction, relation: EvaluationRelation, *, reviewer: bool
) -> WriteBlock | None:
    """D3 step 4 for a caller who reads the evaluation. ``void`` needs
    ``evaluation.manage`` (``reviewer``; ``permission_denied``); the other
    actions need the author while the student is still one of their current
    assignments (``evaluation_read_only`` for a former author,
    ``evaluation_author_only`` for anyone else)."""
    if action == "void":
        return None if reviewer else PERMISSION_DENIED
    if relation is EvaluationRelation.AUTHOR:
        return None
    if relation is EvaluationRelation.FORMER_AUTHOR:
        return EVALUATION_READ_ONLY
    return EVALUATION_AUTHOR_ONLY


def state_block(
    action: EvaluationAction, *, status: str, voided: bool
) -> StateBlock | None:
    """D3 step 7 on the locked row: an edit (draft change or final
    amendment) of a voided evaluation, a finalize of anything but a draft,
    a delete of a final one, a void of a draft or of a voided one."""
    if action == "edit":
        return EVALUATION_VOIDED if voided else None
    if action == "finalize":
        return None if status == DRAFT else EVALUATION_NOT_DRAFT
    if action == "delete":
        return None if status == DRAFT else EVALUATION_FINAL
    if status != FINAL:
        return EVALUATION_NOT_FINAL
    return EVALUATION_VOIDED if voided else None


def can(
    action: EvaluationAction,
    relation: EvaluationRelation,
    *,
    reviewer: bool,
    status: str,
    voided: bool,
) -> bool:
    """One ``can_*`` flag of the working view (D8)."""
    return (
        can_read(relation, status=status, voided=voided)
        and write_block(action, relation, reviewer=reviewer) is None
        and state_block(action, status=status, voided=voided) is None
    )


# --- D2: create -----------------------------------------------------------------------


def is_evaluable(state: str, starts_at: datetime, now: datetime) -> bool:
    """D2 step 7: ``409 session_not_evaluable`` otherwise."""
    return state in EVALUABLE_STATES and starts_at <= now


# --- E3: periods ----------------------------------------------------------------------


class PeriodLike(Protocol):
    @property
    def starts_on(self) -> date: ...

    @property
    def ends_on(self) -> date: ...


def period_for[P: PeriodLike](evaluated_on: date, periods: Iterable[P]) -> P | None:
    """E3: the period whose inclusive ``[starts_on, ends_on]`` holds
    ``evaluated_on`` (active periods of one type never overlap, so at most
    one does), or ``None``."""
    for period in periods:
        if period.starts_on <= evaluated_on <= period.ends_on:
            return period
    return None


def clean(value: str | None) -> str | None:
    """Trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
