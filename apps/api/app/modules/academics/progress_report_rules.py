"""Progress report rules (#83 A3, C, D, E). Pure functions, no I/O.

- The limits (:data:`SUMMARY_MAX`, :data:`SECTION_MAX`, :data:`REASON_MAX`,
  :data:`ON_DEMAND_MAX_DAYS`, :data:`FACT_EVALUATIONS_MAX`,
  :data:`FACT_PLANS_MAX`); the database CHECKs repeat the stored ones.
- :func:`period_for` (E2): a ``weekly`` report covers an ISO week (Monday to
  Sunday), a ``monthly`` one a calendar month, an ``on_demand`` one any
  ``period_start <= period_end`` of at most 366 days; :func:`period_in_future`.
- :func:`report_relation` (E5): how the caller relates to one report: its
  ``author`` (a tutor-role user who still teaches the student), a
  ``former_author`` (a tutor-role author who no longer does), a
  ``reviewer`` (``progress_report.review``), an ``assigned_tutor`` (a tutor
  currently teaching the student, not the author) or ``none``. The first
  that applies wins.
- :func:`can_read` (the working routes, R2/R3): the author and a former
  author read their own reports in any status; a reviewer every non-draft
  report; nobody else (an assigned tutor reads approved reports through the
  closed family shape only).
- :func:`access_block` (the ``403`` of a write, before the row lock),
  :func:`state_block` (the ``409`` against the locked report's status) and
  :func:`block`, which combines them for the ``can_*`` flags
  (:func:`can_flags`).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final, Literal

SUMMARY_MAX: Final = 4000
SECTION_MAX: Final = 2000
REASON_MAX: Final = 1000
ON_DEMAND_MAX_DAYS: Final = 366
"""Inclusive: ``period_end - period_start`` is at most 365 days."""
FACT_EVALUATIONS_MAX: Final = 50
FACT_PLANS_MAX: Final = 20
LIST_LIMIT_MAX: Final = 50
LIST_LIMIT_DEFAULT: Final = 20

WEEKLY: Final = "weekly"
MONTHLY: Final = "monthly"
ON_DEMAND: Final = "on_demand"
KINDS: Final = (WEEKLY, MONTHLY, ON_DEMAND)

DRAFT: Final = "draft"
SUBMITTED: Final = "submitted"
CHANGES_REQUESTED: Final = "changes_requested"
APPROVED: Final = "approved_for_family"
RETRACTED: Final = "retracted"
STATUSES: Final = (DRAFT, SUBMITTED, CHANGES_REQUESTED, APPROVED, RETRACTED)
EDITABLE: Final = frozenset({DRAFT, CHANGES_REQUESTED})
CLOSED: Final = frozenset({APPROVED, RETRACTED})

TUTOR_ROLE: Final = "tutor"
OWNER_ROLE: Final = "owner"
PROGRESS_REPORT_REVIEW: Final = "progress_report.review"
"""The identity catalog's codes (repeated here: ADR-0002)."""

NOT_FOUND: Final = "not_found"
PERIOD_INVALID: Final = "period_invalid"
PERMISSION_DENIED: Final = "permission_denied"
AUTHOR_ONLY: Final = "progress_report_author_only"
READ_ONLY: Final = "progress_report_read_only"
SELF_REVIEW: Final = "progress_report_self_review"
UNDER_REVIEW: Final = "progress_report_under_review"
NOT_SUBMITTED: Final = "progress_report_not_submitted"
REPORT_CLOSED: Final = "progress_report_closed"

type ReportKind = Literal["weekly", "monthly", "on_demand"]
type ReportStatus = Literal[
    "draft", "submitted", "changes_requested", "approved_for_family", "retracted"
]
type ReportAction = Literal[
    "edit", "submit", "delete", "approve", "request_changes", "retract"
]
type AccessBlock = Literal[
    "not_found",
    "permission_denied",
    "progress_report_author_only",
    "progress_report_read_only",
    "progress_report_self_review",
]
type StateBlock = Literal[
    "progress_report_under_review",
    "progress_report_not_submitted",
    "progress_report_closed",
]

AUTHOR_ACTIONS: Final[tuple[ReportAction, ...]] = ("edit", "submit", "delete")
REVIEW_ACTIONS: Final[tuple[ReportAction, ...]] = (
    "approve",
    "request_changes",
    "retract",
)
ACTIONS: Final[tuple[ReportAction, ...]] = (*AUTHOR_ACTIONS, *REVIEW_ACTIONS)


class ReportRelation(Enum):
    """How the caller relates to one report (E5). The first that applies
    wins."""

    AUTHOR = "author"
    FORMER_AUTHOR = "former_author"
    REVIEWER = "reviewer"
    ASSIGNED_TUTOR = "assigned_tutor"
    NONE = "none"


OWN: Final = frozenset({ReportRelation.AUTHOR, ReportRelation.FORMER_AUTHOR})


# --- E2: periods ----------------------------------------------------------------------


def month_end(start: date) -> date:
    """The last day of ``start``'s month."""
    following = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return following - timedelta(days=1)


def period_for(
    kind: str, period_start: date, period_end: date | None
) -> tuple[date, date] | Literal["period_invalid"]:
    """E2: ``(start, end)`` of a report, or ``period_invalid``.

    - ``weekly``: ``period_start`` is a Monday; the end is the Sunday after
      it (a different ``period_end`` is refused);
    - ``monthly``: ``period_start`` is the 1st; the end is the month's last
      day (likewise);
    - ``on_demand``: ``period_end`` is required, not before
      ``period_start``, and the period has at most 366 days (both ends
      included).
    """
    if kind == WEEKLY:
        if period_start.isoweekday() != 1:
            return PERIOD_INVALID
        end = period_start + timedelta(days=6)
    elif kind == MONTHLY:
        if period_start.day != 1:
            return PERIOD_INVALID
        end = month_end(period_start)
    elif kind == ON_DEMAND:
        if period_end is None or period_end < period_start:
            return PERIOD_INVALID
        if (period_end - period_start).days + 1 > ON_DEMAND_MAX_DAYS:
            return PERIOD_INVALID
        return period_start, period_end
    else:
        return PERIOD_INVALID
    if period_end is not None and period_end != end:
        return PERIOD_INVALID
    return period_start, end


def period_in_future(period_start: date, today: date) -> bool:
    """C2: a report can't start after the center's today."""
    return period_start > today


# --- E5: relation and reads -----------------------------------------------------------


def report_relation(
    *,
    is_author: bool,
    tutor: bool,
    student_current: bool,
    reviewer: bool,
) -> ReportRelation:
    """E5. ``student_current``: the report's student is one of the caller's
    current assignments (``scheduling.service.student_ids_for_tutor``;
    ``False`` without the ``tutor`` role). An author who lost the ``tutor``
    role is no author any more (``404``, or a reviewer's relation)."""
    if tutor and is_author:
        return (
            ReportRelation.AUTHOR if student_current else ReportRelation.FORMER_AUTHOR
        )
    if reviewer:
        return ReportRelation.REVIEWER
    if tutor and student_current:
        return ReportRelation.ASSIGNED_TUTOR
    return ReportRelation.NONE


def can_read(
    relation: ReportRelation, *, status: str, deleted: bool, reviewer: bool
) -> bool:
    """The working routes' read rule (R2, R3; the matrix): a deleted report
    never; the author and a former author their own in any status; a
    reviewer every non-draft report; anyone else never."""
    if deleted:
        return False
    if relation in OWN:
        return True
    return reviewer and status != DRAFT


# --- C, D: writes ---------------------------------------------------------------------


def access_block(
    relation: ReportRelation,
    action: ReportAction,
    *,
    reviewer: bool,
    is_owner_role: bool,
) -> AccessBlock | None:
    """The step-4 ``403`` of a write by a caller who reads the report (C3,
    C4, D1). Author actions: a former author ``progress_report_read_only``,
    a reviewer ``progress_report_author_only``. Review actions: without
    ``progress_report.review`` ``permission_denied``; on one's own report
    ``progress_report_self_review`` unless the caller holds the Owner role
    (E4). A caller who can't read the report gets ``not_found``."""
    if action in AUTHOR_ACTIONS:
        if relation is ReportRelation.AUTHOR:
            return None
        if relation is ReportRelation.FORMER_AUTHOR:
            return READ_ONLY
        if relation is ReportRelation.REVIEWER:
            return AUTHOR_ONLY
        return NOT_FOUND
    if relation not in (*OWN, ReportRelation.REVIEWER):
        return NOT_FOUND
    if not reviewer:
        return PERMISSION_DENIED
    if relation in OWN and not is_owner_role:
        return SELF_REVIEW
    return None


def state_block(status: str, action: ReportAction) -> StateBlock | None:
    """The ``409`` of a write against the locked report (C3, C4, D2-D5):

    - edit, submit, delete: ``draft`` or ``changes_requested`` only
      (``submitted``: ``progress_report_under_review``; approved or
      retracted: ``progress_report_closed``);
    - approve, request changes: ``submitted`` only
      (``progress_report_not_submitted``);
    - retract: ``submitted`` or ``approved_for_family`` (retracted:
      ``progress_report_closed``; otherwise
      ``progress_report_not_submitted``).
    """
    if action in AUTHOR_ACTIONS:
        if status in EDITABLE:
            return None
        return UNDER_REVIEW if status == SUBMITTED else REPORT_CLOSED
    if action == "retract":
        if status in (SUBMITTED, APPROVED):
            return None
        return REPORT_CLOSED if status == RETRACTED else NOT_SUBMITTED
    return None if status == SUBMITTED else NOT_SUBMITTED


def block(
    relation: ReportRelation,
    status: str,
    deleted: bool,
    action: ReportAction,
    *,
    reviewer: bool,
    is_owner_role: bool,
) -> AccessBlock | StateBlock | None:
    """Every reason a write would be refused, in the route order (a deleted
    or unreadable report is ``not_found``). ``None``: allowed."""
    if not can_read(relation, status=status, deleted=deleted, reviewer=reviewer):
        return NOT_FOUND
    blocked = access_block(
        relation, action, reviewer=reviewer, is_owner_role=is_owner_role
    )
    if blocked is not None:
        return blocked
    return state_block(status, action)


@dataclass(frozen=True)
class CanFlags:
    can_edit: bool
    can_submit: bool
    can_delete: bool
    can_approve: bool
    can_request_changes: bool
    can_retract: bool


def can_flags(
    relation: ReportRelation,
    status: str,
    *,
    reviewer: bool,
    is_owner_role: bool,
    author_current: bool,
) -> CanFlags:
    """The view's ``can_*`` flags (E1). ``author_current``: the author still
    teaches the student (request changes answers ``409
    progress_report_author_not_current`` otherwise, D4)."""

    def can(action: ReportAction) -> bool:
        return (
            block(
                relation,
                status,
                False,
                action,
                reviewer=reviewer,
                is_owner_role=is_owner_role,
            )
            is None
        )

    return CanFlags(
        can_edit=can("edit"),
        can_submit=can("submit"),
        can_delete=can("delete"),
        can_approve=can("approve"),
        can_request_changes=can("request_changes") and author_current,
        can_retract=can("retract"),
    )


def clean(value: str | None) -> str | None:
    """Trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
