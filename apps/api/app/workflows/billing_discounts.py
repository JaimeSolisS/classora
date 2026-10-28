"""Discounts, scholarships, sibling discounts and promotions (#95 D; H2,
H8, H10): the cross-module reads behind
``app.workflows.billing_discounts_router``.

The billing module owns the rows and their rules
(``app.modules.billing.service``, ``discount_config.py``). This workflow
adds what needs other modules (ADR-0002):

- **References** of new criteria (create, and a ``PATCH`` that sends
  ``eligibility``), after the body's ``422`` and all in one ``422``: each
  subject (``subject_unknown``; ``subject_inactive`` for an inactive subject
  or level) and each level (``education_level_unknown``,
  ``education_level_inactive``), with the list index in the path
  (``catalog.service``, two statements). Existing criteria keep working
  after a reference is deactivated.
- **Assignment targets:** the student (``student_unknown``) or the family
  (``family_unknown``), inactive ones included (D6 choice).
- **Names:** subjects and levels of the criteria (one batch each per page),
  students and families of the assignments (unexecuted name selects of
  ``families.service`` that billing joins).
- **Today:** the center's local date, for the statuses.

Statements: a list page is at most four (the zone, the page with its total
and assignment counts, the subject and level names; a count when the page
is past the end), a detail five (the zone, the row, the assignments, the
names).
"""

import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.workflows.billing_discount_schemas import (
    BillingReference,
    DiscountAssignmentItem,
    DiscountDetail,
    DiscountEligibilityDetail,
    DiscountItem,
)


def target_names() -> billing.TargetNames:
    """The unexecuted student and family name selects billing joins."""
    return billing.TargetNames(
        students=families.student_name_columns(),
        families=families.family_name_columns(),
    )


def _error(path: list[str | int], code: str, message: str) -> FieldError:
    return {"path": ["body", *path], "code": code, "message": message}


async def reference_errors(
    session: AsyncSession, eligibility: billing.DiscountEligibility
) -> list[FieldError]:
    """The ``422`` reference errors of the criteria (see the module
    docstring)."""
    errors: list[FieldError] = []
    subject_ids = eligibility.subject_ids or []
    subjects = await catalog.subject_references(session, subject_ids)
    for index, subject_id in enumerate(subject_ids):
        path: list[str | int] = ["eligibility", "subject_ids", index]
        subject = subjects.get(subject_id)
        if subject is None:
            errors.append(
                _error(path, "subject_unknown", "Choose an existing subject.")
            )
        elif not (subject.is_active and subject.education_level_is_active):
            errors.append(
                _error(
                    path,
                    "subject_inactive",
                    "The subject or its level is inactive. Choose an active subject.",
                )
            )
    level_ids = eligibility.education_level_ids or []
    levels = await catalog.education_level_references(session, level_ids)
    for index, level_id in enumerate(level_ids):
        path = ["eligibility", "education_level_ids", index]
        level = levels.get(level_id)
        if level is None:
            errors.append(
                _error(
                    path,
                    "education_level_unknown",
                    "Choose an existing education level.",
                )
            )
        elif not level.is_active:
            errors.append(
                _error(
                    path,
                    "education_level_inactive",
                    "The education level is inactive. Choose an active one.",
                )
            )
    return errors


async def _items(
    session: AsyncSession, rows: Sequence[billing.DiscountRow]
) -> list[DiscountItem]:
    subject_ids = {s for row in rows for s in row.eligibility.subject_ids or []}
    level_ids = {lv for row in rows for lv in row.eligibility.education_level_ids or []}
    subjects = await catalog.subject_references(session, subject_ids)
    levels = await catalog.education_level_references(session, level_ids)

    def _named(ids: list[uuid.UUID] | None, names: dict[uuid.UUID, str]) -> Any:
        if ids is None:
            return None
        return [BillingReference(id=i, name=names.get(i, "")) for i in ids]

    subject_names = {i: ref.name for i, ref in subjects.items()}
    level_names = {i: ref.name for i, ref in levels.items()}
    return [
        DiscountItem(
            id=row.id,
            kind=row.kind,  # type: ignore[arg-type]
            name=row.name,
            audience=row.audience,  # type: ignore[arg-type]
            percent=None if row.percent is None else f"{row.percent:.4f}",
            amount=row.amount,
            eligibility=DiscountEligibilityDetail(
                charge_kinds=row.eligibility.charge_kinds,
                class_types=row.eligibility.class_types,
                subjects=_named(row.eligibility.subject_ids, subject_names),
                education_levels=_named(
                    row.eligibility.education_level_ids, level_names
                ),
                min_family_students=row.eligibility.min_family_students,
            ),
            starts_on=row.starts_on,
            expires_on=row.expires_on,
            is_active=row.is_active,
            status=row.status,  # type: ignore[arg-type]
            assignment_count=row.assignment_count,
            version=row.version,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def list_discounts(
    session: AsyncSession,
    *,
    kinds: Collection[str],
    statuses: Collection[str],
    q: str | None,
    page: OffsetParams,
) -> OffsetPage[DiscountItem]:
    """``GET /api/v1/billing/discounts`` (D2)."""
    today = await administration.center_today(session)
    rows, total = await billing.list_discounts(
        session,
        kinds=kinds,
        statuses=statuses,
        q=q,
        today=today,
        limit=page.limit,
        offset=page.offset,
    )
    return OffsetPage[DiscountItem](
        items=await _items(session, rows),
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_discount(session: AsyncSession, discount_id: uuid.UUID) -> DiscountDetail:
    """``GET /api/v1/billing/discounts/{id}`` (D4)."""
    today = await administration.center_today(session)
    row = await billing.get_discount(session, discount_id, today=today)
    assignments = await billing.list_assignments(session, target_names(), discount_id)
    [item] = await _items(session, [row])
    return DiscountDetail(
        **item.model_dump(),
        assignments=[
            DiscountAssignmentItem(
                id=a.id,
                student=None
                if a.student_id is None
                else BillingReference(id=a.student_id, name=a.student_name),
                family=None
                if a.family_id is None
                else BillingReference(id=a.family_id, name=a.family_name),
                created_at=a.created_at,
            )
            for a in assignments
        ],
    )


async def create_discount(
    session: AsyncSession, body: billing.DiscountCreate
) -> DiscountDetail:
    """``POST /api/v1/billing/discounts`` (D3)."""
    discount_id = await billing.create_discount(
        session,
        body,
        check_references=lambda criteria: reference_errors(session, criteria),
    )
    return await get_discount(session, discount_id)


async def update_discount(
    session: AsyncSession, discount_id: uuid.UUID, body: billing.DiscountUpdate
) -> DiscountDetail:
    """``PATCH /api/v1/billing/discounts/{id}`` (D5)."""
    await billing.update_discount(
        session,
        discount_id,
        body,
        check_references=lambda criteria: reference_errors(session, criteria),
    )
    return await get_discount(session, discount_id)


async def add_assignment(
    session: AsyncSession,
    discount_id: uuid.UUID,
    body: billing.DiscountAssignmentCreate,
) -> DiscountDetail:
    """``POST /api/v1/billing/discounts/{id}/assignments`` (D6): ``404`` →
    ``422 audience_not_assigned`` → the target (``422 student_unknown`` /
    ``family_unknown``) → ``409 discount_assignment_exists`` → insert."""
    await billing.lock_for_assignment(session, discount_id)
    if body.student_id is not None:
        known = bool(await families.student_references(session, [body.student_id]))
        unknown = _error(
            ["student_id"], "student_unknown", "Choose an existing student."
        )
    else:
        assert body.family_id is not None  # noqa: S101 - the body has one target
        known = (
            await families.family_record_reference(session, body.family_id) is not None
        )
        unknown = _error(["family_id"], "family_unknown", "Choose an existing family.")
    if not known:
        raise billing.BillingValidationError([unknown])
    await billing.add_assignment(
        session, discount_id, student_id=body.student_id, family_id=body.family_id
    )
    return await get_discount(session, discount_id)


async def delete_assignment(
    session: AsyncSession, discount_id: uuid.UUID, assignment_id: uuid.UUID
) -> None:
    """``DELETE /api/v1/billing/discounts/{id}/assignments/{assignment_id}``
    (D7)."""
    await billing.delete_assignment(session, discount_id, assignment_id)
