"""Course template routes (#64; ``app.modules.courses.service``).

- Reads, ``require("course.view")`` (Owners, Administrators, Academic
  Coordinators, Finance Staff): ``GET /api/v1/course-templates`` and ``GET
  /api/v1/course-templates/{template_id}``.
- Writes, ``require("course.manage")`` (Owners, Administrators, Academic
  Coordinators): ``POST``, ``PUT`` (the full document) and ``PATCH`` (status
  only). No reauthentication (not in ADR-0007 D10).

Check order of a ``PUT``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` (the body's schema) → ``404`` → ``409 version_conflict`` → ``422``
(the template rules, subjects and item ids) → ``409 name_taken``.
Snapshots have no route: the cohort module (#65) creates them through the
service.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.courses import service
from app.modules.courses.schemas import (
    CourseTemplateDetail,
    CourseTemplateReplace,
    CourseTemplateStatus,
    CourseTemplateStatusUpdate,
    CourseTemplateSummary,
    CourseTemplateWrite,
)

router = APIRouter(prefix="/api/v1", tags=["courses"])

COURSE_VIEW = "course.view"
COURSE_MANAGE = "course.manage"

NOT_FOUND = problem_response_doc("No such course template (not_found).")
BODY_INVALID = (
    "The body is not valid: a field out of its bounds, an unknown field, or a"
    " template rule: 1-10 subjects (duplicate_subject), 1-50 modules and 0-30"
    " assessments with unique titles (duplicate_title), a module subject that"
    " is not a template subject (module_subject_not_in_template), planned"
    " sessions above the number of sessions (planned_sessions_exceed_total), a"
    " module_index outside the body's modules (module_index_invalid), an"
    " unknown or inactive new subject (subject_unknown, subject_inactive), an"
    " item id that is not this template's (item_unknown)"
)
NAME_TAKEN = (
    "Another course template already has this name, ignoring case; inactive"
    " templates count (name_taken)."
)
VERSION_CONFLICT = (
    "The template changed since it was loaded (version_conflict); reload and retry."
)


@router.get(
    "/course-templates",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown status, or a limit or offset out of range (validation_error)."
        ),
    },
)
async def list_course_templates(
    principal: Annotated[Principal, require(COURSE_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: Annotated[
        CourseTemplateStatus,
        Query(description="active (the default), inactive or all."),
    ] = "active",
) -> OffsetPage[CourseTemplateSummary]:
    """The course templates with ``status``, by name."""
    return await service.list_course_templates(uow.session, status=status, page=page)


@router.post(
    "/course-templates",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(NAME_TAKEN),
        422: problem_response_doc(
            f"{BODY_INVALID}; any item id (item_unknown) (validation_error)."
        ),
    },
)
async def create_course_template(
    principal: Annotated[Principal, require(COURSE_MANAGE)],
    body: CourseTemplateWrite,
    uow: UnitOfWorkDep,
) -> CourseTemplateDetail:
    """Create an active template from a whole document (no item ids)."""
    return await service.create_course_template(uow.session, body)


@router.get(
    "/course-templates/{template_id}",
    responses={**PERMISSION_RESPONSES, 404: NOT_FOUND},
)
async def get_course_template(
    principal: Annotated[Principal, require(COURSE_VIEW)],
    template_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> CourseTemplateDetail:
    """One course template, active or not."""
    return await service.get_course_template(uow.session, template_id)


@router.put(
    "/course-templates/{template_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(f"{VERSION_CONFLICT} Or {NAME_TAKEN}"),
        422: problem_response_doc(
            f"{BODY_INVALID}, or a missing version (validation_error)."
        ),
    },
)
async def replace_course_template(
    principal: Annotated[Principal, require(COURSE_MANAGE)],
    template_id: uuid.UUID,
    body: CourseTemplateReplace,
    uow: UnitOfWorkDep,
) -> CourseTemplateDetail:
    """Replace the template's content: modules and assessments with an
    ``id`` are kept and updated, those without are created, stored ones
    left out are deleted. Cohorts already created keep their snapshot."""
    return await service.replace_course_template(uow.session, template_id, body)


@router.patch(
    "/course-templates/{template_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(VERSION_CONFLICT),
        422: problem_response_doc(
            "A missing version or is_active, or an unknown field (validation_error)."
        ),
    },
)
async def set_course_template_status(
    principal: Annotated[Principal, require(COURSE_MANAGE)],
    template_id: uuid.UUID,
    body: CourseTemplateStatusUpdate,
    uow: UnitOfWorkDep,
) -> CourseTemplateDetail:
    """Deactivate (``is_active: false``) or reactivate a template."""
    return await service.set_course_template_status(uow.session, template_id, body)
