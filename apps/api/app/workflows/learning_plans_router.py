"""Learning plan routes (#81; :mod:`app.workflows.learning_plans` gives the
record policies and the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Every route is
``authenticated()`` plus a record policy (#21), and the ``404`` of a hidden
record is the body of an unknown id.

- ``GET /api/v1/students/{id}/learning-plans[?status&subject_id]``: the
  list (staff, tutors, the student and guardians with academic access);
  ``POST``: a draft (``learning_plan.manage`` holders and the tutors
  currently teaching the student in the subject).
- ``GET`` / ``PUT`` / ``DELETE /api/v1/learning-plans/{id}``, ``POST
  …/transition``: one plan.
- ``PATCH /api/v1/learning-plan-milestones/{id}``: a milestone's progress.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.workflows import learning_plans as workflow
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanList,
    LearningPlanMilestoneProgress,
    LearningPlanReplace,
    LearningPlanTransition,
    LearningPlanView,
    PlanStatus,
)

student_router = APIRouter(prefix="/api/v1/students", tags=["learning-plans"])
plan_router = APIRouter(prefix="/api/v1/learning-plans", tags=["learning-plans"])
milestone_router = APIRouter(
    prefix="/api/v1/learning-plan-milestones", tags=["learning-plans"]
)

_PLAN_NOT_FOUND: Final = problem_response_doc(
    "No such learning plan, it was deleted, or it isn't one you may see (not_found)."
)
_CONSENT_403: Final = problem_response_doc(
    "The adult student hasn't given you access to their academic records"
    " (student_consent_required); privacy_notice_required."
)
_WRITE_403: Final = problem_response_doc(
    "You teach this student another subject (plan_subject_not_assigned); the"
    " student is no longer one of your current assignments (plan_read_only);"
    " the adult student hasn't given you access to their academic records"
    " (student_consent_required); a failed Origin or X-CSRF-Token check"
    " (csrf_failed); privacy_notice_required."
)
_CONTENT_422: Final = (
    "title_required, too_long, objectives_required, objectives_too_many,"
    " milestones_required, milestones_too_many, target_before_start,"
    " due_on_invalid"
)


# --- Students -------------------------------------------------------------------------


@student_router.get(
    "/{student_id}/learning-plans",
    responses={
        **PERMISSION_RESPONSES,
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such student, or not one whose learning plans you may see (not_found)."
        ),
    },
)
async def list_student_learning_plans(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    status: Annotated[list[PlanStatus] | None, Query()] = None,
    subject_id: uuid.UUID | None = None,
) -> LearningPlanList:
    """A student's learning plans with their progress: active first, then
    drafts, then closed ones (at most 100). Staff with learning_plan.manage
    and tutors currently teaching the student see every plan; a tutor who
    taught the student before only the plans they created; the student and
    their guardians with access to their academic records only active and
    closed plans (never drafts). `status` may repeat."""
    return await workflow.list_student_plans(
        uow.session,
        principal,
        student_id,
        statuses=status or [],
        subject_id=subject_id,
    )


@student_router.post(
    "/{student_id}/learning-plans",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "You don't currently teach this student this subject"
            " (plan_subject_not_assigned); csrf_failed; privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not one you may write learning plans for (not_found)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): subject_unknown (unknown"
            f" or inactive subject), {_CONTENT_422}."
        ),
    },
)
async def create_learning_plan(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    body: LearningPlanCreate,
    uow: UnitOfWorkDep,
) -> LearningPlanView:
    """Start a draft learning plan for a student in a subject. Families
    never see a draft; nobody is notified until it is activated."""
    return await workflow.create_plan(
        uow.session, principal=principal, student_id=student_id, body=body
    )


# --- Plans ----------------------------------------------------------------------------


@plan_router.get(
    "/{plan_id}",
    responses={**PERMISSION_RESPONSES, 403: _CONSENT_403, 404: _PLAN_NOT_FOUND},
)
async def get_learning_plan(
    principal: Annotated[Principal, authenticated()],
    plan_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> LearningPlanView:
    """One learning plan with its objectives, milestones and progress."""
    return await workflow.get_plan(uow.session, principal, plan_id)


@plan_router.put(
    "/{plan_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _PLAN_NOT_FOUND,
        409: problem_response_doc(
            "The plan changed since it was read (version_conflict) or is closed"
            " (plan_closed)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, an unknown"
            " or immutable field such as subject_id (extra_forbidden),"
            f" {_CONTENT_422}, objective_unknown, milestone_unknown (an id not"
            " in this plan, or a milestone under another objective)."
        ),
    },
)
async def replace_learning_plan(
    principal: Annotated[Principal, authenticated()],
    plan_id: uuid.UUID,
    body: LearningPlanReplace,
    uow: UnitOfWorkDep,
) -> LearningPlanView:
    """Replace a plan's content. Objectives and milestones keep their id
    (and a milestone its progress) when you send it back; the ones you
    leave out are deleted. Families see the change at once on an active
    plan."""
    return await workflow.replace_plan(
        uow.session, principal=principal, plan_id=plan_id, body=body
    )


@plan_router.post(
    "/{plan_id}/transition",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _PLAN_NOT_FOUND,
        409: problem_response_doc(
            "The plan changed since it was read (version_conflict), is closed"
            " (plan_closed), isn't a draft (plan_not_draft: activate), isn't"
            " active (plan_not_active: complete), or the student already has an"
            " active plan in this subject (active_plan_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, or an"
            " unknown action."
        ),
    },
)
async def transition_learning_plan(
    principal: Annotated[Principal, authenticated()],
    plan_id: uuid.UUID,
    body: LearningPlanTransition,
    uow: UnitOfWorkDep,
) -> LearningPlanView:
    """Activate a draft (the student and their guardians with access to
    their academic records see it and are notified), complete an active
    plan (they are notified) or discontinue a draft or an active plan."""
    return await workflow.transition_plan(
        uow.session, principal=principal, plan_id=plan_id, body=body
    )


@plan_router.delete(
    "/{plan_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _PLAN_NOT_FOUND,
        409: problem_response_doc(
            "The plan changed since it was read (version_conflict) or isn't a"
            " draft (plan_not_draft)."
        ),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_learning_plan(
    principal: Annotated[Principal, authenticated()],
    plan_id: uuid.UUID,
    version: Annotated[int, Query(description="The plan's version, as last read.")],
    uow: UnitOfWorkDep,
) -> None:
    """Delete a draft plan. It disappears from every list."""
    await workflow.delete_plan(
        uow.session, principal=principal, plan_id=plan_id, version=version
    )


# --- Milestones -----------------------------------------------------------------------


@milestone_router.patch(
    "/{milestone_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: problem_response_doc(
            "No such milestone, its plan was deleted, or it isn't one you may"
            " see (not_found)."
        ),
        409: problem_response_doc(
            "The milestone changed since it was read (version_conflict), the"
            " plan is a draft (plan_not_active) or closed (plan_closed)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, or an"
            " unknown status."
        ),
    },
)
async def update_learning_plan_milestone(
    principal: Annotated[Principal, authenticated()],
    milestone_id: uuid.UUID,
    body: LearningPlanMilestoneProgress,
    uow: UnitOfWorkDep,
) -> LearningPlanView:
    """Set a milestone's progress on an active plan (pending, in progress,
    done). The whole plan comes back so the totals refresh."""
    return await workflow.set_milestone_progress(
        uow.session, principal=principal, milestone_id=milestone_id, body=body
    )
