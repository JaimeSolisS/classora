"""Evaluation template rows (#80 B1, C).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.evaluations``.

The template row is the **version anchor** of its criteria: a replace
that changes only criteria still bumps the template's ``version`` exactly
once (``updated_at`` alone, left out of the template's audit diff), and a
replace that changes nothing writes nothing. Criteria are matched by
position: changed rows are updated, extra positions deleted, new ones
inserted (the position unique constraint is deferred to the commit).

Names are unique ignoring case (inactive templates count): checked first,
and a unique violation at flush maps to the same ``409 duplicate_name``.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.errors import EvaluationTemplateNameTakenError
from app.modules.academics.models import (
    TEMPLATE_NAME_INDEX,
    EvaluationTemplate,
    EvaluationTemplateCriterion,
)


@dataclass(frozen=True)
class CriterionValues:
    """One criterion as written (trimmed; ``max_score`` two places)."""

    title: str
    description: str | None
    max_score: Decimal


@dataclass(frozen=True)
class TemplateRow:
    template: EvaluationTemplate
    criteria: list[EvaluationTemplateCriterion]


# --- Reads ----------------------------------------------------------------------------


async def _criteria(
    session: AsyncSession, template_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[EvaluationTemplateCriterion]]:
    found: dict[uuid.UUID, list[EvaluationTemplateCriterion]] = {
        template_id: [] for template_id in template_ids
    }
    if not template_ids:
        return found
    rows = await session.scalars(
        select(EvaluationTemplateCriterion)
        .where(EvaluationTemplateCriterion.template_id.in_(set(template_ids)))
        .order_by(
            EvaluationTemplateCriterion.template_id,
            EvaluationTemplateCriterion.position,
        )
        .execution_options(populate_existing=True)
    )
    for row in rows:
        found[row.template_id].append(row)
    return found


async def template_with_criteria(
    session: AsyncSession, template_id: uuid.UUID, *, lock: str | None = None
) -> TemplateRow | None:
    """The template and its criteria in position order (two statements), or
    ``None`` when unknown. ``lock`` takes the template row ``"update"``
    (writes) or ``"share"`` (an evaluation create copies its criteria: a
    concurrent replace waits, so the copy is never a mix, D9)."""
    query = select(EvaluationTemplate).where(EvaluationTemplate.id == template_id)
    if lock == "update":
        query = query.with_for_update()
    elif lock == "share":
        query = query.with_for_update(read=True)
    template = cast(
        EvaluationTemplate | None,
        await session.scalar(query.execution_options(populate_existing=True)),
    )
    if template is None:
        return None
    criteria = await _criteria(session, [template.id])
    return TemplateRow(template, criteria[template.id])


async def list_templates(
    session: AsyncSession,
    *,
    is_active: bool | None,
    kind: str | None,
    subject_id: uuid.UUID | None,
    q: str | None,
    limit: int,
) -> list[TemplateRow]:
    """C3: the templates (``is_active`` ``None`` = both), of one kind, for
    one subject (or any subject: ``subject_id IS NULL``), whose name
    contains ``q`` ignoring case; by ``lower(name)`` then ``id``, at most
    ``limit``. Two statements."""
    query = select(EvaluationTemplate)
    if is_active is not None:
        query = query.where(EvaluationTemplate.is_active.is_(is_active))
    if kind is not None:
        query = query.where(EvaluationTemplate.kind == kind)
    if subject_id is not None:
        query = query.where(
            or_(
                EvaluationTemplate.subject_id == subject_id,
                EvaluationTemplate.subject_id.is_(None),
            )
        )
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(EvaluationTemplate.name.ilike(f"%{escaped}%"))
    templates = list(
        await session.scalars(
            query.order_by(
                func.lower(EvaluationTemplate.name), EvaluationTemplate.id
            ).limit(limit)
        )
    )
    criteria = await _criteria(session, [t.id for t in templates])
    return [TemplateRow(t, criteria[t.id]) for t in templates]


async def _ensure_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(EvaluationTemplate.id).where(
        func.lower(EvaluationTemplate.name) == func.lower(name)
    )
    if except_id is not None:
        query = query.where(EvaluationTemplate.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise EvaluationTemplateNameTakenError()


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        if TEMPLATE_NAME_INDEX in str(exc.orig):
            raise EvaluationTemplateNameTakenError() from None
        raise


# --- Writes ---------------------------------------------------------------------------


async def create_template(
    session: AsyncSession,
    *,
    name: str,
    kind: str,
    subject_id: uuid.UUID | None,
    description: str | None,
    criteria: Sequence[CriterionValues],
) -> TemplateRow:
    """C1: a new active template with its criteria at positions 1…n
    (``409 duplicate_name`` first)."""
    await _ensure_name_free(session, name)
    template = EvaluationTemplate(
        id=uuid.uuid7(),
        name=name,
        kind=kind,
        subject_id=subject_id,
        description=description,
        is_active=True,
    )
    session.add(template)
    await _flush(session)
    rows = [
        EvaluationTemplateCriterion(
            id=uuid.uuid7(),
            template_id=template.id,
            position=index,
            title=values.title,
            description=values.description,
            max_score=values.max_score,
        )
        for index, values in enumerate(criteria, start=1)
    ]
    session.add_all(rows)
    await session.flush()
    return TemplateRow(template, rows)


async def replace_template(
    session: AsyncSession,
    stored: TemplateRow,
    *,
    name: str,
    subject_id: uuid.UUID | None,
    description: str | None,
    is_active: bool,
    criteria: Sequence[CriterionValues],
) -> TemplateRow:
    """C1 ``PUT`` on the locked template (the module docstring has the
    rules). Returns the template with its current criteria."""
    template = stored.template
    wanted: dict[str, Any] = {
        "name": name,
        "subject_id": subject_id,
        "description": description,
        "is_active": is_active,
    }
    changes = {
        key: value for key, value in wanted.items() if getattr(template, key) != value
    }
    by_position = {row.position: row for row in stored.criteria}
    updates: list[tuple[EvaluationTemplateCriterion, dict[str, Any]]] = []
    inserts: list[EvaluationTemplateCriterion] = []
    for position, values in enumerate(criteria, start=1):
        row = by_position.get(position)
        written = {
            "title": values.title,
            "description": values.description,
            "max_score": values.max_score,
        }
        if row is None:
            inserts.append(
                EvaluationTemplateCriterion(
                    id=uuid.uuid7(),
                    template_id=template.id,
                    position=position,
                    **written,
                )
            )
            continue
        update = {k: v for k, v in written.items() if getattr(row, k) != v}
        if update:
            updates.append((row, update))
    removed = [row for row in stored.criteria if row.position > len(criteria)]
    if not (changes or updates or inserts or removed):
        return stored
    if "name" in changes:
        await _ensure_name_free(session, name, except_id=template.id)
    if changes:
        for key, value in changes.items():
            setattr(template, key, value)
    else:
        template.updated_at = func.now()
    await _flush(session)
    await session.refresh(template, ["updated_at"])
    for row in removed:
        await session.delete(row)
    for row, update in updates:
        for key, value in update.items():
            setattr(row, key, value)
    session.add_all(inserts)
    await session.flush()
    kept = [row for row in stored.criteria if row.position <= len(criteria)]
    return TemplateRow(template, sorted([*kept, *inserts], key=lambda r: r.position))
