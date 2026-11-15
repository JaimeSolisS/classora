"""Evaluation routes (#80; :mod:`app.workflows.evaluations` gives the
record policies and the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). The template writes
require ``evaluation.manage``; every other route is ``authenticated()``
plus a record policy (#21), and the ``404`` of a hidden record is the body
of an unknown id.

- ``GET /api/v1/evaluation-templates[?status&kind&subject_id&q]``,
  ``GET …/{id}``: ``evaluation.manage`` holders and tutors (active ones);
  ``POST`` / ``PUT``: ``evaluation.manage``.
- ``POST /api/v1/evaluations``: a tutor currently teaching the student.
- ``GET`` / ``PATCH`` / ``DELETE /api/v1/evaluations/{id}``, ``POST
  …/finalize``, ``POST …/void`` (``evaluation.manage``).
- ``GET /api/v1/students/{id}/evaluations``: the working list (staff and
  tutors).
- ``GET /api/v1/students/{id}/academic-history``: the unified history
  (staff, tutors, the student and guardians with academic access).
- ``GET /api/v1/class-sessions/{id}/evaluation-context``: the session's
  current tutor.
"""

import uuid
from datetime import date
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated, require
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage
from app.core.uow import UnitOfWorkDep
from app.workflows import evaluations as workflow
from app.workflows.evaluation_schemas import (
    AcademicHistory,
    EvaluationContext,
    EvaluationCreate,
    EvaluationKind,
    EvaluationPatch,
    EvaluationTemplateCreate,
    EvaluationTemplateList,
    EvaluationTemplateReplace,
    EvaluationTemplateView,
    EvaluationVersion,
    EvaluationView,
    EvaluationVoid,
    GroupBy,
)

template_router = APIRouter(prefix="/api/v1/evaluation-templates", tags=["evaluations"])
evaluation_router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])
student_router = APIRouter(prefix="/api/v1/students", tags=["evaluations"])
session_router = APIRouter(prefix="/api/v1/class-sessions", tags=["evaluations"])

EVALUATION_MANAGE: Final = "evaluation.manage"
PAGE_LIMIT: Final = 20
PAGE_MAX: Final = 50

type PageLimit = Annotated[int, Query(ge=1, le=PAGE_MAX)]

_TEMPLATE_403: Final = problem_response_doc(
    "Only staff with evaluation.manage and tutors read evaluation templates"
    " (permission_denied); privacy_notice_required."
)
_TEMPLATE_NOT_FOUND: Final = problem_response_doc(
    "No such template, or an inactive one and you are a tutor (not_found)."
)
_EVALUATION_NOT_FOUND: Final = problem_response_doc(
    "No such evaluation, it was deleted, or it isn't one you may see (not_found)."
)
_WRITE_403: Final = problem_response_doc(
    "Only the evaluation's author can do this (evaluation_author_only); the"
    " student is no longer one of your current assignments"
    " (evaluation_read_only); a failed Origin or X-CSRF-Token check"
    " (csrf_failed); privacy_notice_required."
)
_VERSION_422: Final = problem_response_doc(
    "The body is not valid (validation_error): no version, or an unknown field."
)


# --- Templates ------------------------------------------------------------------------


@template_router.get(
    "",
    responses={**PERMISSION_RESPONSES, 403: _TEMPLATE_403},
)
async def list_evaluation_templates(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    status: Literal["active", "inactive", "all"] = "active",
    kind: EvaluationKind | None = None,
    subject_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> EvaluationTemplateList:
    """Evaluation templates by name (at most 200). `subject_id` also
    matches any-subject templates; tutors always get active ones only."""
    return await workflow.list_templates(
        uow.session, principal, status=status, kind=kind, subject_id=subject_id, q=q
    )


@template_router.get(
    "/{template_id}",
    responses={**PERMISSION_RESPONSES, 403: _TEMPLATE_403, 404: _TEMPLATE_NOT_FOUND},
)
async def get_evaluation_template(
    principal: Annotated[Principal, authenticated()],
    template_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> EvaluationTemplateView:
    """One evaluation template with its criteria."""
    return await workflow.get_template(uow.session, principal, template_id)


_TEMPLATE_422: Final = problem_response_doc(
    "The body is not valid (validation_error): name_required, too_long,"
    " criteria_required, criteria_too_many, duplicate_title,"
    " max_score_out_of_range, score_precision, subject_unknown (unknown or"
    " inactive subject), kind_immutable (PUT)."
)


@template_router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "Another template has this name, ignoring case (duplicate_name)."
        ),
        422: _TEMPLATE_422,
    },
)
async def create_evaluation_template(
    _: Annotated[Principal, require(EVALUATION_MANAGE)],
    body: EvaluationTemplateCreate,
    uow: UnitOfWorkDep,
) -> EvaluationTemplateView:
    """Create an evaluation template (active)."""
    return await workflow.create_template(uow.session, body=body)


@template_router.put(
    "/{template_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such template (not_found)."),
        409: problem_response_doc(
            "The template changed since it was read (version_conflict), or"
            " another template has this name (duplicate_name)."
        ),
        422: _TEMPLATE_422,
    },
)
async def replace_evaluation_template(
    _: Annotated[Principal, require(EVALUATION_MANAGE)],
    template_id: uuid.UUID,
    body: EvaluationTemplateReplace,
    uow: UnitOfWorkDep,
) -> EvaluationTemplateView:
    """Replace a template (name, subject, description, criteria, active).
    Evaluations already made from it never change."""
    return await workflow.replace_template(
        uow.session, template_id=template_id, body=body
    )


# --- Evaluations ----------------------------------------------------------------------


@evaluation_router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Staff record no evaluations: only a tutor currently teaching the"
            " student (evaluation_tutor_only); csrf_failed;"
            " privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not one you currently teach (not_found)."
        ),
        409: problem_response_doc(
            "The template is inactive (template_inactive), or the linked class"
            " isn't confirmed or completed and started (session_not_evaluable,"
            " details.state)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): template_unknown,"
            " subject_mismatch, subject_required, subject_unknown,"
            " class_session_invalid, course_assessment_requires_course_session,"
            " course_assessment_unknown, evaluated_on_in_future,"
            " evaluated_on_invalid, criterion_unknown, duplicate_criterion,"
            " score_out_of_range, score_precision, too_long."
        ),
    },
)
async def create_evaluation(
    principal: Annotated[Principal, authenticated()],
    body: EvaluationCreate,
    uow: UnitOfWorkDep,
) -> EvaluationView:
    """Start a draft evaluation of a student you currently teach, from a
    template (its criteria are copied). Only you see a draft; nobody is
    notified."""
    return await workflow.create_evaluation(uow.session, principal=principal, body=body)


@evaluation_router.get(
    "/{evaluation_id}",
    responses={**PERMISSION_RESPONSES, 404: _EVALUATION_NOT_FOUND},
)
async def get_evaluation(
    principal: Annotated[Principal, authenticated()],
    evaluation_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> EvaluationView:
    """One evaluation: for its author, tutors currently teaching the student
    (final ones) and staff with evaluation.manage (final and voided ones)."""
    return await workflow.get_evaluation(uow.session, principal, evaluation_id)


@evaluation_router.patch(
    "/{evaluation_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _EVALUATION_NOT_FOUND,
        409: problem_response_doc(
            "The evaluation changed since it was read (version_conflict) or was"
            " voided (evaluation_voided)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, an unknown"
            " or immutable field (extra_forbidden), change_reason_required (a"
            " final evaluation), score_required, criterion_unknown,"
            " duplicate_criterion, score_out_of_range, score_precision,"
            " evaluated_on_in_future, evaluated_on_invalid, too_long."
        ),
    },
)
async def edit_evaluation(
    principal: Annotated[Principal, authenticated()],
    evaluation_id: uuid.UUID,
    body: EvaluationPatch,
    uow: UnitOfWorkDep,
) -> EvaluationView:
    """Change your draft, or amend your final evaluation with a reason
    (internal; families see an "Updated" marker). A request that changes
    nothing changes nothing."""
    return await workflow.edit_evaluation(
        uow.session, principal=principal, evaluation_id=evaluation_id, body=body
    )


@evaluation_router.post(
    "/{evaluation_id}/finalize",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _EVALUATION_NOT_FOUND,
        409: problem_response_doc(
            "The evaluation changed since it was read (version_conflict), is"
            " already final (evaluation_not_draft) or has unscored criteria"
            " (evaluation_incomplete, details.missing_positions)."
        ),
        422: _VERSION_422,
    },
)
async def finalize_evaluation(
    principal: Annotated[Principal, authenticated()],
    evaluation_id: uuid.UUID,
    body: EvaluationVersion,
    uow: UnitOfWorkDep,
) -> EvaluationView:
    """Finalize your draft: the student and their guardians with access to
    their academic records see it and are notified."""
    return await workflow.finalize_evaluation(
        uow.session,
        principal=principal,
        evaluation_id=evaluation_id,
        version=body.version,
    )


@evaluation_router.post(
    "/{evaluation_id}/void",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Voiding needs evaluation.manage (permission_denied); csrf_failed;"
            " privacy_notice_required."
        ),
        404: _EVALUATION_NOT_FOUND,
        409: problem_response_doc(
            "The evaluation changed since it was read (version_conflict), isn't"
            " final (evaluation_not_final) or is already voided"
            " (evaluation_voided)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, or no reason"
            " (reason_required)."
        ),
    },
)
async def void_evaluation(
    principal: Annotated[Principal, authenticated()],
    evaluation_id: uuid.UUID,
    body: EvaluationVoid,
    uow: UnitOfWorkDep,
) -> EvaluationView:
    """Void a final evaluation (staff with evaluation.manage), with a reason:
    it leaves the academic history and the author is notified."""
    return await workflow.void_evaluation(
        uow.session, principal=principal, evaluation_id=evaluation_id, body=body
    )


@evaluation_router.delete(
    "/{evaluation_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _EVALUATION_NOT_FOUND,
        409: problem_response_doc(
            "The evaluation changed since it was read (version_conflict) or is"
            " final (evaluation_final)."
        ),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_evaluation(
    principal: Annotated[Principal, authenticated()],
    evaluation_id: uuid.UUID,
    version: Annotated[
        int, Query(description="The evaluation's version, as last read.")
    ],
    uow: UnitOfWorkDep,
) -> None:
    """Delete your draft. It disappears from every list."""
    await workflow.delete_evaluation(
        uow.session, principal=principal, evaluation_id=evaluation_id, version=version
    )


# --- Students -------------------------------------------------------------------------


@student_router.get(
    "/{student_id}/evaluations",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such student, or not one whose evaluations you may see (not_found)."
        ),
    },
)
async def list_student_evaluations(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    status: Literal["draft", "final", "voided"] | None = None,
    subject_id: uuid.UUID | None = None,
    class_session_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: PageLimit = PAGE_LIMIT,
) -> CursorPage[EvaluationView]:
    """A student's evaluations, newest first (the working list): staff with
    evaluation.manage read every final one (voided too); tutors currently
    teaching the student every final one and their own drafts; a tutor who
    taught the student before only their own, read-only."""
    return await workflow.list_student_evaluations(
        uow.session,
        principal,
        student_id,
        status=status,
        subject_id=subject_id,
        class_session_id=class_session_id,
        cursor=cursor,
        limit=limit,
    )


@student_router.get(
    "/{student_id}/academic-history",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "The adult student hasn't given you access to their academic"
            " records (student_consent_required); privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not one whose history you may see (not_found)."
        ),
        422: problem_response_doc(
            "A query parameter is not valid (validation_error): 'from' after"
            " 'to' (range_invalid)."
        ),
    },
)
async def get_academic_history(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    group_by: GroupBy = "school_year",
    subject_id: uuid.UUID | None = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> AcademicHistory:
    """A student's academic history: final evaluations from every tutor,
    newest first, and grouped by academic period and subject. For staff
    with evaluation.manage, tutors currently teaching the student, the
    student and their guardians with access to their academic records."""
    return await workflow.academic_history(
        uow.session,
        principal,
        student_id,
        group_by=group_by,
        subject_id=subject_id,
        from_=from_,
        to=to,
    )


# --- The class ------------------------------------------------------------------------


@session_router.get(
    "/{class_session_id}/evaluation-context",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such class session, or not one you currently teach (not_found)."
        ),
    },
)
async def get_evaluation_context(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> EvaluationContext:
    """What the evaluation form needs of your class: its subject, whether it
    can be evaluated, its participants and, for a course, the cohort's
    assessments."""
    return await workflow.evaluation_context(uow.session, principal, class_session_id)
