"""Recurring expense template routes (#116 C; ADR-0017 E12, E17; data-model
§12.7).

Owners and Finance Staff (``expense.view`` for the reads, ``expense.manage``
for the writes; not delegable). Administrators, academic coordinators, tutors,
students and guardians get ``403``: a template holds an amount (ADR-0017 E12).

- ``GET /api/v1/finance/expense-templates`` and ``GET …/{template_id}``.
- ``POST /api/v1/finance/expense-templates``: no ``Idempotency-Key`` (the name
  is the natural key, #116 E3), no reauthentication.
- ``PATCH …/{template_id}``: ``version``; ``frequency`` and ``starts_on`` are
  unknown fields (#116 E4).

Check order of the writes: ``401`` → ``403 csrf_failed`` → ``403
permission_denied`` → ``422 text_invalid`` for a lone surrogate → ``422`` (the
body) → the rules of :mod:`~app.modules.finance.templates`. There is no
``DELETE`` (#116 E10): ``is_active: false`` pauses a template.

A create, a reactivation and an ``ends_on`` change queue one run of
``finance:generate_recurring_expenses`` after the commit
(:func:`app.core.jobs.defer_recurring_expenses`, a background task, #116 E8),
so a period already due is generated at once.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from procrastinate import App

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.idempotency import hashable_body
from app.core.jobs import defer_recurring_expenses, get_job_app
from app.core.uow import UnitOfWorkDep
from app.modules.finance import service as finance
from app.modules.finance.schemas import (
    ExpenseTemplateCreate,
    ExpenseTemplateItem,
    ExpenseTemplateUpdate,
    TemplateStatus,
)

EXPENSE_VIEW: Final = "expense.view"
EXPENSE_MANAGE: Final = "expense.manage"

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

JobAppDep = Annotated[App, Depends(get_job_app)]

_TEMPLATES: Final = "/expense-templates"
_TEMPLATE: Final = "/expense-templates/{template_id}"
_TEXT_RULES: Final = "text_empty, text_too_long, text_invalid"
_NOT_FOUND: Final = problem_response_doc("No such recurring expense (not_found).")
_USABLE: Final = (
    "the category or the vendor doesn't exist (expense_category_unknown,"
    " vendor_unknown)"
)
_INACTIVE: Final = (
    "the category or the vendor is inactive (expense_category_inactive,"
    " vendor_inactive)"
)


@router.get(
    _TEMPLATES,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown status or a"
            " malformed id."
        ),
    },
)
async def list_expense_templates(
    principal: Annotated[Principal, require(EXPENSE_VIEW)],
    uow: UnitOfWorkDep,
    status: Annotated[
        list[TemplateStatus] | None,
        Query(description="Only templates in these statuses (repeatable)."),
    ] = None,
    category_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
) -> list[ExpenseTemplateItem]:
    """Every recurring expense, not paginated: active first, then paused, then
    ended, then by name ignoring case. Inactive categories and vendors are
    listed and can be filtered on."""
    return await finance.list_templates(
        uow.session,
        finance.TemplateFilters(
            statuses=status, category_id=category_id, vendor_id=vendor_id
        ),
    )


@router.get(_TEMPLATE, responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_expense_template(
    template_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_VIEW)],
    uow: UnitOfWorkDep,
) -> ExpenseTemplateItem:
    """One recurring expense, with its next and latest generated periods."""
    return await finance.get_template(uow.session, template_id)


@router.post(
    _TEMPLATES,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "Another recurring expense has this name, ignoring case, a paused or"
            f" ended one included (expense_template_exists); or {_INACTIVE}."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: a missing or unknown"
            f" field, {_TEXT_RULES}, amount_not_positive, money_*,"
            f" due_offset_days outside 0-60, …); {_USABLE}; `starts_on` more than"
            " 31 days ago or 366 days ahead (expense_template_starts_on_invalid);"
            " or `ends_on` before `starts_on` (expense_template_ends_on_invalid)."
        ),
    },
)
async def create_expense_template(
    principal: Annotated[Principal, require(EXPENSE_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    body: ExpenseTemplateCreate,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> ExpenseTemplateItem:
    """Set up a weekly, monthly or annual recurring expense. Each period
    generates one ordinary expense, once: described with the template's name,
    dated on the period's first day and due `due_offset_days` later. Periods
    already due (from a `starts_on` up to 31 days back) are generated at once.
    No `Idempotency-Key`: the name is unique, so a retried create is `409
    expense_template_exists`."""
    item = await finance.create_template(uow.session, body)
    # Runs after the response, so after the Unit of Work committed.
    background.add_task(defer_recurring_expenses, job_app)
    return item


@router.patch(
    _TEMPLATE,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The version is stale (version_conflict); the name is taken"
            f" (expense_template_exists); or {_INACTIVE}, checked whenever the"
            " template stays or becomes active and not ended."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: an unknown field, frequency"
            " and starts_on included, no change (no_changes),"
            f" {_TEXT_RULES}, amount_not_positive, money_*, …); {_USABLE}; or"
            " `ends_on` before `starts_on` or before a period already generated"
            " (expense_template_ends_on_invalid)."
        ),
    },
)
async def update_expense_template(
    template_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    body: ExpenseTemplateUpdate,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> ExpenseTemplateItem:
    """Change a recurring expense. Changes apply to the expenses not generated
    yet; generated ones keep their values. `is_active: false` pauses it;
    `true` resumes it from the first period on or after today, with no
    back-fill. `frequency` and `starts_on` can't change: end this template
    and set up a new one."""
    change = await finance.change_template(uow.session, template_id, body)
    if change.queue_run:
        background.add_task(defer_recurring_expenses, job_app)
    return change.item
