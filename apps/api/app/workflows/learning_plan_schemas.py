"""Learning plan bodies and closed views (#81 C1, D1, D2, F, AM1).

Every model is a closed allow-list (``extra="forbid"``). The bodies only
check types and shapes (``422`` at step 2 of every order) and trim text
(whitespace-only is ``null``); the content checks (``title_required``,
``too_long``, ``objectives_required``, …) run in
``app.workflows.learning_plans`` after the access checks (C3 step 6, C4
step 8). Percentages are decimals: JSON strings on the way out (``"50"``,
ADR-0004). The views carry display names only, never user ids; one shape
serves staff, tutors and families (E5).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

from app.modules.academics.service import learning_plan_rules as rules

type PlanStatus = Literal["draft", "active", "completed", "discontinued"]
type MilestoneStatus = Literal["pending", "in_progress", "done"]
type PlanAction = Literal["activate", "complete", "discontinue"]


def _trim(value: Any) -> Any:
    return rules.clean(value) if isinstance(value, str) else value


type Text = Annotated[str | None, BeforeValidator(_trim)]

type PercentOut = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:f}", return_type=str),
    WithJsonSchema({"type": "string", "format": "decimal", "examples": ["50"]}),
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Bodies (C1, D1, D2) --------------------------------------------------------------


class LearningPlanMilestoneInput(_Closed):
    title: Text = Field(description="1-200 characters (title_required / too_long).")
    activities: Text = Field(
        default=None,
        description="Plain text: what the student will do; at most 2000 characters.",
    )
    due_on: date | None = Field(
        default=None,
        description="A local date, not before 2000-01-01 (due_on_invalid); it may"
        " fall outside the plan's dates.",
    )


class LearningPlanObjectiveInput(_Closed):
    title: Text = Field(description="1-200 characters (title_required / too_long).")
    description: Text = Field(default=None, description="At most 1000 characters.")
    milestones: list[LearningPlanMilestoneInput] = Field(
        description="1-10 milestones, in order (milestones_required /"
        " milestones_too_many); at most 50 in the plan."
    )


class LearningPlanCreate(_Closed):
    """`POST /api/v1/students/{student_id}/learning-plans` (#81 C1): a draft
    plan. Nobody sees it but staff and the student's tutors until it is
    activated; nobody is notified."""

    subject_id: uuid.UUID = Field(
        description="An active subject; a tutor only one they currently teach"
        " the student (plan_subject_not_assigned). It never changes."
    )
    title: Text = Field(description="1-120 characters (title_required / too_long).")
    description: Text = Field(
        default=None, description="The overall goal; at most 2000 characters."
    )
    starts_on: date | None = Field(
        default=None, description="A local date; defaults to the center's today."
    )
    target_on: date | None = Field(
        default=None, description="Not before starts_on (target_before_start)."
    )
    objectives: list[LearningPlanObjectiveInput] = Field(
        description="1-10 objectives, in order (objectives_required /"
        " objectives_too_many)."
    )


class LearningPlanMilestoneReplace(LearningPlanMilestoneInput):
    id: uuid.UUID | None = Field(
        default=None,
        description="An existing milestone of this objective, updated in place"
        " (its progress is kept); omit for a new one (milestone_unknown).",
    )


class LearningPlanObjectiveReplace(LearningPlanObjectiveInput):
    id: uuid.UUID | None = Field(
        default=None,
        description="An existing objective of this plan, updated in place; omit"
        " for a new one (objective_unknown).",
    )
    milestones: list[LearningPlanMilestoneReplace] = Field(  # type: ignore[assignment]
        description="1-10 milestones, in order; at most 50 in the plan. A"
        " milestone never moves to another objective (delete it and add a"
        " new one)."
    )


class LearningPlanReplace(_Closed):
    """`PUT /api/v1/learning-plans/{plan_id}` (#81 C1, C4): the whole content
    with the version you loaded. Objectives and milestones you leave out are
    deleted. `subject_id` never changes (extra_forbidden). A body that
    changes nothing writes nothing."""

    version: int = Field(description="The plan's version, as last read.")
    title: Text = Field(description="1-120 characters (title_required / too_long).")
    description: Text = Field(
        default=None, description="The overall goal; at most 2000 characters."
    )
    starts_on: date
    target_on: date | None = Field(
        default=None, description="Not before starts_on (target_before_start)."
    )
    objectives: list[LearningPlanObjectiveReplace] = Field(
        description="1-10 objectives, in order."
    )


class LearningPlanTransition(_Closed):
    """`POST /api/v1/learning-plans/{plan_id}/transition` (#81 D1)."""

    version: int = Field(description="The plan's version, as last read.")
    action: PlanAction = Field(
        description="activate (a draft: the student and their guardians with"
        " academic access see it and are notified), complete (an active plan:"
        " they are notified) or discontinue (a draft or an active plan)."
    )


class LearningPlanMilestoneProgress(_Closed):
    """`PATCH /api/v1/learning-plan-milestones/{milestone_id}` (#81 D2)."""

    version: int = Field(description="The milestone's version, as last read.")
    status: MilestoneStatus


# --- Views (F) ------------------------------------------------------------------------


class LearningPlanSubject(_Closed):
    id: uuid.UUID
    name: str


class LearningPlanPerson(_Closed):
    display_name: str


class LearningPlanProgress(_Closed):
    done: int
    total: int
    percent: PercentOut = Field(description="done / total × 100, a whole number.")


class LearningPlanSummary(_Closed):
    """A learning plan in a student's list (#81 F2): the same shape for
    staff, tutors and families."""

    id: uuid.UUID
    version: int = Field(description="The plan's version (send it back).")
    student_id: uuid.UUID
    subject: LearningPlanSubject
    title: str
    status: PlanStatus
    starts_on: date
    target_on: date | None
    activated_at: datetime | None
    closed_at: datetime | None
    created_by: LearningPlanPerson
    progress: LearningPlanProgress
    objectives_complete: int = Field(
        description="Objectives whose milestones are all done."
    )
    objectives_total: int
    overdue_milestones: int = Field(
        description="Milestones not done whose due date is before the center's"
        " today; always 0 once the plan is completed or discontinued."
    )
    updated_at: datetime


class LearningPlanMilestoneView(_Closed):
    id: uuid.UUID
    version: int = Field(description="The milestone's version (send it back).")
    position: int
    title: str
    activities: str | None
    due_on: date | None
    status: MilestoneStatus
    overdue: bool = Field(
        description="Not done and due before the center's today; always false"
        " once the plan is completed or discontinued."
    )
    completed_at: datetime | None
    completed_by: LearningPlanPerson | None


class LearningPlanObjectiveView(_Closed):
    id: uuid.UUID
    position: int
    title: str
    description: str | None
    complete: bool = Field(description="All its milestones are done.")
    progress: LearningPlanProgress
    milestones: list[LearningPlanMilestoneView]


class LearningPlanView(LearningPlanSummary):
    """A learning plan with its objectives and milestones (#81 F3): the same
    shape for staff, tutors and families. The `can_*` flags say what you may
    do now (always false for families)."""

    description: str | None
    objectives: list[LearningPlanObjectiveView]
    can_edit: bool
    can_activate: bool
    can_complete: bool
    can_discontinue: bool
    can_delete: bool = Field(description="Delete this draft.")
    can_update_progress: bool


class LearningPlanList(_Closed):
    """A student's learning plans (#81 E2, AM1): active first, then drafts,
    then closed ones; at most 100. `can_create` / `creatable_subjects` are
    for you and ignore the filters."""

    items: list[LearningPlanSummary]
    truncated: bool = Field(description="More than 100 plans: the rest are left out.")
    can_create: bool = Field(description="You may create a plan for this student.")
    creatable_subjects: list[LearningPlanSubject] | None = Field(
        description="The active subjects you may create a plan in, by name;"
        " null means any active subject (staff with learning_plan.manage)."
    )
