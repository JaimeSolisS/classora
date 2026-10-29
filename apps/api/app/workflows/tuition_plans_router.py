"""Tuition plan routes (#93 D; ``app.workflows.tuition_plans``; ADR-0006
E1, E8, E9, E11).

Reads need ``billing.view`` and writes ``billing.manage`` (Owners,
Administrators and Finance Staff; E8). No reauthentication and no
``Idempotency-Key``: a plan is configuration; the daily job posts its
charges on their natural key (#86 E6, the #89/#95 precedent). The check
order of the writes is ``401`` → ``403 csrf_failed`` → ``403
privacy_notice_required`` → ``403 permission_denied`` → ``422`` (the body)
→ ``404`` → the locks → ``409 version_conflict`` → the rules.

- ``GET /api/v1/billing/tuition-plans``: filters ``student_id``,
  ``family_id``, ``group_id`` and ``status`` (repeatable).
- ``POST /api/v1/billing/tuition-plans``.
- ``GET /api/v1/billing/tuition-plans/eligible-enrollments?student_id=``
  (``billing.manage``: Finance Staff can't read groups through the
  scheduling routes).
- ``GET`` / ``PATCH`` / ``DELETE /api/v1/billing/tuition-plans/{id}``.
- ``POST /api/v1/billing/tuition-plans/{id}/pauses`` and ``…/resume``.

After the commit, a create whose plan starts today or earlier, a resume and
an ``ends_on`` extension queue one ``billing:post_tuition_charges`` run
(``app.core.jobs.defer_tuition_charges``), so a period that is due now is
posted within seconds.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from procrastinate import App

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.jobs import defer_tuition_charges, get_job_app
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import tuition_plans
from app.workflows.tuition_plan_schemas import (
    EligibleEnrollment,
    TuitionPauseCreate,
    TuitionPlanCreate,
    TuitionPlanDetail,
    TuitionPlanItem,
    TuitionPlanStatus,
    TuitionPlanUpdate,
    TuitionResume,
)

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/tuition-plans", tags=["billing"])

JobAppDep = Annotated[App, Depends(get_job_app)]

_NOT_FOUND = problem_response_doc("No such tuition plan (not_found).")
_VERSION = "The plan changed since it was loaded (version_conflict)."
_ENDED = "The plan has ended (tuition_plan_ended): create a new plan."
_ENDS_ON = (
    "`ends_on` must be the last day of one of the plan's periods"
    " (tuition_plan_ends_on_invalid)"
)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed id, an"
            " unknown status, or `limit`/`offset` out of range."
        ),
    },
)
async def list_tuition_plans(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    status: Annotated[list[TuitionPlanStatus] | None, Query()] = None,
) -> OffsetPage[TuitionPlanItem]:
    """The tuition plans by student name, newest start first. `status`
    (scheduled, active, paused, ended; for the center's today) may be
    repeated."""
    return await tuition_plans.list_tuition_plans(
        uow.session,
        student_id=student_id,
        family_id=family_id,
        group_id=group_id,
        statuses=status or (),
        page=page,
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "The student isn't in a family (student_has_no_family); another"
            " plan for the same group enrollment, or for the same student and"
            " price, overlaps these dates (tuition_plan_overlap); the group"
            " enrollment already has a flat fee charged"
            " (tuition_plan_enrollment_fee_charged) or session charges inside"
            " the plan's dates (tuition_plan_sessions_charged)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, `due_offset_days` outside 0-60, a date outside 2000-2099"
            " (date_out_of_range); an unknown or inactive student"
            " (student_unknown, student_inactive). Then, one at a time: the"
            " price must be a per-period price, individual for a plan without"
            " a group enrollment, and for the group (or unscoped for its"
            " subject) for a group plan (tuition_plan_price_invalid); the"
            " group enrollment must be the student's and active"
            " (tuition_plan_enrollment_invalid); `starts_on` at most 31 days"
            " ago and 366 days ahead, not before the enrollment"
            " (tuition_plan_starts_on_invalid); " + _ENDS_ON + "."
        ),
    },
)
async def create_tuition_plan(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: TuitionPlanCreate,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> TuitionPlanDetail:
    """Create a weekly, every-two-weeks or monthly tuition plan (its
    frequency is the price's). Each period is charged once, at the price in
    effect on its first day. A plan linked to a group enrollment replaces the
    group's flat fee and its per-session charges inside the plan's dates. An
    individual plan never changes per-session charges."""
    detail, due_now = await tuition_plans.create_tuition_plan(uow.session, body)
    if due_now:
        # Runs after the response, so after the Unit of Work committed.
        background.add_task(defer_tuition_charges, job_app)
    return detail


@router.get(
    "/eligible-enrollments",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "`student_id` is missing or malformed (validation_error)."
        ),
    },
)
async def list_eligible_enrollments(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    uow: UnitOfWorkDep,
    student_id: uuid.UUID,
) -> list[EligibleEnrollment]:
    """The student's active group enrollments a plan can be linked to,
    oldest first, with whether each already has a flat fee charge or a
    plan."""
    return await tuition_plans.eligible_enrollments(uow.session, student_id)


@router.get("/{tuition_plan_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_tuition_plan(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    tuition_plan_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> TuitionPlanDetail:
    """A tuition plan with its pauses, its last 24 posted periods and its
    next 12 periods."""
    return await tuition_plans.get_tuition_plan(uow.session, tuition_plan_id)


@router.patch(
    "/{tuition_plan_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            _VERSION
            + " Extending: "
            + _ENDED
            + " Another plan overlaps the new dates (tuition_plan_overlap); a"
            " group plan's added dates have session charges"
            " (tuition_plan_sessions_charged)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `version` is required;"
            " `due_offset_days` outside 0-60; date_out_of_range. "
            + _ENDS_ON
            + ", on or after the first period's end and not before a period"
            " already charged."
        ),
    },
)
async def update_tuition_plan(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    tuition_plan_id: uuid.UUID,
    body: TuitionPlanUpdate,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> TuitionPlanDetail:
    """End a plan (`ends_on`, the last day of a period), make it open-ended
    again (`ends_on: null`) or change its due offset (periods charged later
    only). Ending never changes a charged period."""
    detail, extended = await tuition_plans.update_tuition_plan(
        uow.session, tuition_plan_id, body
    )
    if extended:
        background.add_task(defer_tuition_charges, job_app)
    return detail


@router.delete(
    "/{tuition_plan_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            _VERSION + " A plan with charges can't be deleted"
            " (tuition_plan_has_charges): end it instead."
        ),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_tuition_plan(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    tuition_plan_id: uuid.UUID,
    version: Annotated[int, Query(description="The plan's version, as last read.")],
    uow: UnitOfWorkDep,
) -> None:
    """Delete a plan that has no charge, with its pauses."""
    await tuition_plans.delete_tuition_plan(uow.session, tuition_plan_id, version)


@router.post(
    "/{tuition_plan_id}/pauses",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            _VERSION + " " + _ENDED + " The pause overlaps another pause"
            " (tuition_pause_overlap)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `version` and"
            " `starts_on` are required; date_out_of_range. A pause starts on the"
            " first day of a period after the last charged one, not before"
            " today's period and not after the plan's end, and ends on the last"
            " day of a period or stays open (tuition_pause_invalid)."
        ),
    },
)
async def add_tuition_pause(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    tuition_plan_id: uuid.UUID,
    body: TuitionPauseCreate,
    uow: UnitOfWorkDep,
) -> TuitionPlanDetail:
    """Pause a plan from a period (until a period's end, or until resumed).
    Paused periods are never charged, not even after a resume."""
    return await tuition_plans.add_tuition_pause(uow.session, tuition_plan_id, body)


@router.post(
    "/{tuition_plan_id}/resume",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            _VERSION + " The plan has no open pause (tuition_pause_not_open)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `version` and"
            " `resume_on` are required; date_out_of_range. `resume_on` is the"
            " first day of a period, not before the pause's start and not"
            " before today's period (tuition_resume_invalid)."
        ),
    },
)
async def resume_tuition_plan(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    tuition_plan_id: uuid.UUID,
    body: TuitionResume,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> TuitionPlanDetail:
    """End the open pause the day before `resume_on` (a pause that hasn't
    begun is removed when `resume_on` is its start). Charging restarts from
    `resume_on`; paused periods are never caught up."""
    detail = await tuition_plans.resume_tuition_plan(uow.session, tuition_plan_id, body)
    background.add_task(defer_tuition_charges, job_app)
    return detail
