"""Course templates and their snapshots (#64; spec §3 structured course).

**Reads** (``course.view``): :func:`list_course_templates` (at most three
statements per page) and :func:`get_course_template` (inactive templates
too; ``404 not_found`` when unknown).

**Writes** (``course.manage``): :func:`create_course_template`,
:func:`replace_course_template` (the full document with ``version``) and
:func:`set_course_template_status` (deactivate, reactivate). Templates are
never deleted.

Order of the checks: the body's ``422`` → ``404`` → ``409
version_conflict`` → ``422`` (the :mod:`~app.modules.courses.templates`
rules, unknown or inactive subjects, unknown item ids, all reported
together) → ``409 name_taken`` → the write. The template row is locked
``FOR UPDATE`` first, so concurrent writes from one version give one
success and one ``version_conflict``.

**Replacing the content:** modules and assessments with an ``id`` of this
template are kept and updated; items without ``id`` are created; stored
items left out are deleted (cohorts keep their syllabus in their snapshot).
Only changed rows are written, so the audit trail shows real changes. Any
change bumps the template ``version`` exactly once: the template row is the
version anchor of its subjects, modules and assessments, and the snapshot
relies on it. A document that changes nothing writes nothing.

**Subjects** (through ``catalog.service`` only, ADR-0002): a subject the
document adds must exist (``subject_unknown``) and be active with an active
level (``subject_inactive``); both rows are locked ``FOR SHARE`` until the
transaction ends. A subject linked earlier and deactivated since may stay.

**Snapshots** (owner decision #64 E1 (a)): the cohort module (#65) calls
:func:`snapshot_course_template` in its creation transaction and keeps the
snapshot id; :func:`get_course_template_snapshot` reads one back. A snapshot
is never updated or deleted (a database trigger), so later template edits
never change existing cohorts.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import SubjectReference
from app.modules.courses.errors import (
    CourseTemplateInactive,
    CourseTemplateInvalidError,
    CourseTemplateNameTakenError,
    CourseTemplateNotFound,
)
from app.modules.courses.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_VERSION_UNIQUE,
    TEMPLATE_NAME_INDEX,
    CourseTemplate,
    CourseTemplateAssessment,
    CourseTemplateModule,
    CourseTemplateSubject,
)
from app.modules.courses.models import (
    CourseTemplateSnapshot as SnapshotRow,
)
from app.modules.courses.schemas import (
    CourseTemplateAssessment as AssessmentItem,
)
from app.modules.courses.schemas import (
    CourseTemplateDetail,
    CourseTemplateReplace,
    CourseTemplateSnapshot,
    CourseTemplateStatus,
    CourseTemplateStatusUpdate,
    CourseTemplateSummary,
    CourseTemplateWrite,
)
from app.modules.courses.schemas import (
    CourseTemplateModule as ModuleItem,
)
from app.modules.courses.schemas import (
    CourseTemplateSubject as SubjectItem,
)
from app.modules.courses.templates import (
    SnapshotSubject,
    TemplateContent,
    build_snapshot,
    completion_of,
    validate_template,
)

TEMPLATE_FIELDS = (
    "name",
    "description",
    "session_count",
    "session_minutes",
    "min_attendance_percent",
)
MODULE_FIELDS = (
    "position",
    "title",
    "description",
    "subject_id",
    "planned_sessions",
    "is_required",
    "materials",
)
ASSESSMENT_FIELDS = (
    "position",
    "title",
    "module_id",
    "is_required",
    "min_score_percent",
)


# --- Building responses --------------------------------------------------------------


def _subject_sort_key(reference: SubjectReference) -> tuple[Any, ...]:
    # The catalog's order: the level's sort_order and name, then the subject.
    return (
        reference.education_level_sort_order,
        reference.education_level_name.lower(),
        reference.name.lower(),
        reference.id,
    )


def _subject_items(
    subject_ids: Iterable[uuid.UUID],
    references: dict[uuid.UUID, SubjectReference],
) -> list[SubjectItem]:
    found = sorted(
        (references[i] for i in subject_ids if i in references), key=_subject_sort_key
    )
    return [
        SubjectItem(
            id=reference.id,
            name=reference.name,
            level_name=reference.education_level_name,
            is_active=reference.is_active and reference.education_level_is_active,
        )
        for reference in found
    ]


def _module_item(module: CourseTemplateModule) -> ModuleItem:
    return ModuleItem(
        id=module.id,
        position=module.position,
        title=module.title,
        description=module.description,
        subject_id=module.subject_id,
        planned_sessions=module.planned_sessions,
        is_required=module.is_required,
        materials=module.materials,
    )


def _assessment_item(assessment: CourseTemplateAssessment) -> AssessmentItem:
    return AssessmentItem(
        id=assessment.id,
        position=assessment.position,
        title=assessment.title,
        module_id=assessment.module_id,
        is_required=assessment.is_required,
        min_score_percent=assessment.min_score_percent,
    )


def _summary(
    template: CourseTemplate,
    subjects: list[SubjectItem],
    module_count: int,
) -> CourseTemplateSummary:
    return CourseTemplateSummary(
        id=template.id,
        name=template.name,
        subjects=subjects,
        session_count=template.session_count,
        session_minutes=template.session_minutes,
        total_minutes=template.session_count * template.session_minutes,
        module_count=module_count,
        is_active=template.is_active,
        version=template.version,
        updated_at=template.updated_at,
    )


@dataclass
class _Stored:
    """A template's rows as loaded for a read or a write."""

    template: CourseTemplate
    subject_links: list[CourseTemplateSubject] = field(default_factory=list)
    modules: list[CourseTemplateModule] = field(default_factory=list)
    assessments: list[CourseTemplateAssessment] = field(default_factory=list)

    @property
    def subject_ids(self) -> list[uuid.UUID]:
        return [link.subject_id for link in self.subject_links]


async def _load_children(session: AsyncSession, stored: _Stored) -> _Stored:
    template_id = stored.template.id
    stored.subject_links = list(
        await session.scalars(
            select(CourseTemplateSubject).where(
                CourseTemplateSubject.template_id == template_id
            )
        )
    )
    stored.modules = list(
        await session.scalars(
            select(CourseTemplateModule)
            .where(CourseTemplateModule.template_id == template_id)
            .order_by(CourseTemplateModule.position)
        )
    )
    stored.assessments = list(
        await session.scalars(
            select(CourseTemplateAssessment)
            .where(CourseTemplateAssessment.template_id == template_id)
            .order_by(CourseTemplateAssessment.position)
        )
    )
    return stored


def _detail(
    stored: _Stored, references: dict[uuid.UUID, SubjectReference]
) -> CourseTemplateDetail:
    template = stored.template
    modules = [_module_item(m) for m in sorted(stored.modules, key=_position)]
    assessments = [
        _assessment_item(a) for a in sorted(stored.assessments, key=_position)
    ]
    summary = _summary(
        template, _subject_items(stored.subject_ids, references), len(modules)
    )
    return CourseTemplateDetail(
        **summary.model_dump(),
        description=template.description,
        min_attendance_percent=template.min_attendance_percent,
        modules=modules,
        assessments=assessments,
        completion=completion_of(template.min_attendance_percent, modules, assessments),
        created_at=template.created_at,
    )


def _position(row: CourseTemplateModule | CourseTemplateAssessment) -> int:
    return row.position


async def _detail_of(session: AsyncSession, stored: _Stored) -> CourseTemplateDetail:
    references = await catalog_service.subject_references(session, stored.subject_ids)
    return _detail(stored, references)


# --- Reads ---------------------------------------------------------------------------


def _status_condition(status: CourseTemplateStatus) -> list[ColumnElement[bool]]:
    active: ColumnElement[bool] = CourseTemplate.is_active.expression
    if status == "active":
        return [active]
    if status == "inactive":
        return [~active]
    return []


async def list_course_templates(
    session: AsyncSession,
    *,
    status: CourseTemplateStatus = "active",
    page: OffsetParams,
) -> OffsetPage[CourseTemplateSummary]:
    """The templates with ``status``, by ``lower(name)`` then ``id``, with
    their subjects and module count. Three statements whatever the page
    size (the page with module counts and subject ids, the total, the
    subjects)."""
    conditions = _status_condition(status)
    module_count = (
        select(func.count(CourseTemplateModule.id))
        .where(CourseTemplateModule.template_id == CourseTemplate.id)
        .correlate(CourseTemplate)
        .scalar_subquery()
    )
    subject_ids = (
        select(func.array_agg(CourseTemplateSubject.subject_id))
        .where(CourseTemplateSubject.template_id == CourseTemplate.id)
        .correlate(CourseTemplate)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(CourseTemplate, module_count, subject_ids)
            .where(*conditions)
            .order_by(func.lower(CourseTemplate.name), CourseTemplate.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    total = await session.scalar(
        select(func.count(CourseTemplate.id)).where(*conditions)
    )
    every_subject = {i for _, _, ids in rows for i in ids or ()}
    references = await catalog_service.subject_references(session, every_subject)
    return OffsetPage[CourseTemplateSummary](
        items=[
            _summary(template, _subject_items(ids or (), references), count or 0)
            for template, count, ids in rows
        ],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_course_template(
    session: AsyncSession, template_id: uuid.UUID
) -> CourseTemplateDetail:
    """One template, active or not, with its modules, assessments and
    completion criteria; ``404 not_found`` when unknown."""
    template = await session.get(CourseTemplate, template_id)
    if template is None:
        raise RecordNotFoundError()
    return await _detail_of(session, await _load_children(session, _Stored(template)))


# --- Writes --------------------------------------------------------------------------


async def _flush(session: AsyncSession) -> None:
    """Flush; a concurrent request that took the name after the pre-check
    gives ``409 name_taken``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if TEMPLATE_NAME_INDEX in str(exc.orig):
            raise CourseTemplateNameTakenError() from None
        raise


async def _ensure_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(CourseTemplate.id).where(
        func.lower(CourseTemplate.name) == func.lower(name)
    )
    if except_id is not None:
        query = query.where(CourseTemplate.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise CourseTemplateNameTakenError()


def _field_error(path: list[str | int], code: str, message: str) -> FieldError:
    return {"path": ["body", *path], "code": code, "message": message}


async def _subject_errors(
    session: AsyncSession,
    subject_ids: Sequence[uuid.UUID],
    already_linked: Collection[uuid.UUID],
) -> list[FieldError]:
    """``subject_unknown`` / ``subject_inactive`` for the subjects the
    document adds (each checked once, locked ``FOR SHARE``)."""
    errors: list[FieldError] = []
    checked: set[uuid.UUID] = set()
    for index, subject_id in enumerate(subject_ids):
        if subject_id in already_linked or subject_id in checked:
            continue
        checked.add(subject_id)
        reference = await catalog_service.get_subject_for_reference(
            session, subject_id, lock=True
        )
        if reference is None:
            errors.append(
                _field_error(
                    ["subject_ids", index],
                    "subject_unknown",
                    "Choose an existing subject.",
                )
            )
        elif not (reference.is_active and reference.education_level_is_active):
            errors.append(
                _field_error(
                    ["subject_ids", index],
                    "subject_inactive",
                    "The subject or its education level is inactive.",
                )
            )
    return errors


def _item_id_errors(
    body: CourseTemplateWrite, stored: _Stored | None
) -> list[FieldError]:
    """Item ids that are not this template's (on a create: any id)."""
    module_ids = {m.id for m in stored.modules} if stored else set()
    assessment_ids = {a.id for a in stored.assessments} if stored else set()
    errors: list[FieldError] = []
    lists: list[tuple[str, Sequence[uuid.UUID | None], set[uuid.UUID]]] = [
        ("modules", [m.id for m in body.modules], module_ids),
        ("assessments", [a.id for a in body.assessments], assessment_ids),
    ]
    for name, ids, known in lists:
        for index, item_id in enumerate(ids):
            if item_id is not None and item_id not in known:
                errors.append(
                    _field_error(
                        [name, index, "id"],
                        "item_unknown",
                        "Leave out the id of a new item."
                        if stored is None
                        else "This item is not part of the template.",
                    )
                )
    return errors


async def _validate(
    session: AsyncSession, body: CourseTemplateWrite, stored: _Stored | None
) -> None:
    errors = validate_template(body)
    errors += _item_id_errors(body, stored)
    errors += await _subject_errors(
        session, body.subject_ids, stored.subject_ids if stored else ()
    )
    if errors:
        errors.sort(key=lambda error: _path_key(error["path"]))
        raise CourseTemplateInvalidError(errors)


def _path_key(path: Sequence[str | int]) -> tuple[Any, ...]:
    order = {
        "name": 0,
        "subject_ids": 1,
        "modules": 2,
        "assessments": 3,
    }
    head = path[1] if len(path) > 1 else ""
    rest = tuple((0, part) if isinstance(part, int) else (1, part) for part in path[2:])
    return (order.get(str(head), 9), rest)


def _module_values(body: CourseTemplateWrite, index: int) -> dict[str, Any]:
    module = body.modules[index]
    return {
        "position": index + 1,
        "title": module.title,
        "description": module.description,
        "subject_id": module.subject_id,
        "planned_sessions": module.planned_sessions,
        "is_required": module.is_required,
        "materials": module.materials,
    }


def _assessment_values(
    body: CourseTemplateWrite, index: int, module_ids: Sequence[uuid.UUID]
) -> dict[str, Any]:
    assessment = body.assessments[index]
    return {
        "position": index + 1,
        "title": assessment.title,
        "module_id": None
        if assessment.module_index is None
        else module_ids[assessment.module_index],
        "is_required": assessment.is_required,
        "min_score_percent": assessment.min_score_percent,
    }


def _changed(row: Any, values: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {name: values[name] for name in fields if getattr(row, name) != values[name]}


async def create_course_template(
    session: AsyncSession, body: CourseTemplateWrite
) -> CourseTemplateDetail:
    """A new active template with its subjects, modules and assessments.
    ``422`` (rules, subjects, any item id) → ``409 name_taken``."""
    await _validate(session, body, None)
    await _ensure_name_free(session, body.name)
    template = CourseTemplate(
        **{name: getattr(body, name) for name in TEMPLATE_FIELDS}, is_active=True
    )
    session.add(template)
    await _flush(session)
    stored = _Stored(template)
    stored.subject_links = [
        CourseTemplateSubject(template_id=template.id, subject_id=subject_id)
        for subject_id in body.subject_ids
    ]
    stored.modules = [
        CourseTemplateModule(
            id=uuid.uuid7(), template_id=template.id, **_module_values(body, index)
        )
        for index in range(len(body.modules))
    ]
    module_ids = [module.id for module in stored.modules]
    stored.assessments = [
        CourseTemplateAssessment(
            template_id=template.id, **_assessment_values(body, index, module_ids)
        )
        for index in range(len(body.assessments))
    ]
    session.add_all(stored.subject_links)
    session.add_all(stored.modules)
    await session.flush()
    session.add_all(stored.assessments)
    await session.flush()
    return await _detail_of(session, stored)


async def _template_for_update(
    session: AsyncSession, template_id: uuid.UUID
) -> CourseTemplate:
    template = await session.scalar(
        select(CourseTemplate)
        .where(CourseTemplate.id == template_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if template is None:
        raise RecordNotFoundError()
    return template


async def _bump_version(
    session: AsyncSession, template: CourseTemplate, changes: dict[str, Any]
) -> None:
    """One versioned ``UPDATE`` of the template: the changed columns, or,
    for a change of its children only, ``updated_at`` alone (left out of the
    audit, so no ``course_templates`` event is written)."""
    if changes:
        for name, value in changes.items():
            setattr(template, name, value)
    else:
        template.updated_at = func.now()
    await _flush(session)
    await session.refresh(template, ["updated_at"])


async def replace_course_template(
    session: AsyncSession, template_id: uuid.UUID, body: CourseTemplateReplace
) -> CourseTemplateDetail:
    """Replace a template's content (the module docstring has the rules)."""
    template = await _template_for_update(session, template_id)
    ensure_version(template, body.version)
    stored = await _load_children(session, _Stored(template))
    await _validate(session, body, stored)

    changes = _changed(
        template,
        {name: getattr(body, name) for name in TEMPLATE_FIELDS},
        TEMPLATE_FIELDS,
    )

    wanted_subjects = list(dict.fromkeys(body.subject_ids))
    linked = {link.subject_id: link for link in stored.subject_links}
    new_links = [
        CourseTemplateSubject(template_id=template.id, subject_id=subject_id)
        for subject_id in wanted_subjects
        if subject_id not in linked
    ]
    removed_links = [
        link for subject_id, link in linked.items() if subject_id not in wanted_subjects
    ]

    modules_by_id = {module.id: module for module in stored.modules}
    module_ids: list[uuid.UUID] = []
    new_modules: list[CourseTemplateModule] = []
    module_updates: list[tuple[CourseTemplateModule, dict[str, Any]]] = []
    for index, written in enumerate(body.modules):
        values = _module_values(body, index)
        if written.id is None:
            module = CourseTemplateModule(
                id=uuid.uuid7(), template_id=template.id, **values
            )
            new_modules.append(module)
        else:
            module = modules_by_id[written.id]
            update = _changed(module, values, MODULE_FIELDS)
            if update:
                module_updates.append((module, update))
        module_ids.append(module.id)
    kept_module_ids = {m.id for m in body.modules if m.id is not None}
    removed_modules = [m for m in stored.modules if m.id not in kept_module_ids]

    assessments_by_id = {item.id: item for item in stored.assessments}
    new_assessments: list[CourseTemplateAssessment] = []
    assessment_updates: list[tuple[CourseTemplateAssessment, dict[str, Any]]] = []
    for index, written_assessment in enumerate(body.assessments):
        values = _assessment_values(body, index, module_ids)
        if written_assessment.id is None:
            new_assessments.append(
                CourseTemplateAssessment(template_id=template.id, **values)
            )
        else:
            assessment = assessments_by_id[written_assessment.id]
            update = _changed(assessment, values, ASSESSMENT_FIELDS)
            if update:
                assessment_updates.append((assessment, update))
    kept_assessment_ids = {a.id for a in body.assessments if a.id is not None}
    removed_assessments = [
        a for a in stored.assessments if a.id not in kept_assessment_ids
    ]

    if not any(
        (
            changes,
            new_links,
            removed_links,
            new_modules,
            module_updates,
            removed_modules,
            new_assessments,
            assessment_updates,
            removed_assessments,
        )
    ):
        return await _detail_of(session, stored)
    if "name" in changes:
        await _ensure_name_free(session, changes["name"], except_id=template.id)
    await _bump_version(session, template, changes)

    # Explicit flushes in dependency order: new modules exist before an
    # assessment points at them; assessments are repointed or deleted before
    # the modules they pointed at are deleted (the RESTRICT foreign key never
    # fires). Positions may collide in between: the unique constraints are
    # deferred to the commit.
    session.add_all(new_links)
    session.add_all(new_modules)
    for module, update in module_updates:
        for name, value in update.items():
            setattr(module, name, value)
    await session.flush()
    for assessment in removed_assessments:
        await session.delete(assessment)
    for assessment, update in assessment_updates:
        for name, value in update.items():
            setattr(assessment, name, value)
    session.add_all(new_assessments)
    await session.flush()
    for module in removed_modules:
        await session.delete(module)
    for link in removed_links:
        await session.delete(link)
    await session.flush()

    stored.subject_links = [
        link for link in stored.subject_links if link not in removed_links
    ] + new_links
    stored.modules = [
        m for m in stored.modules if m not in removed_modules
    ] + new_modules
    stored.assessments = [
        a for a in stored.assessments if a not in removed_assessments
    ] + new_assessments
    return await _detail_of(session, stored)


async def set_course_template_status(
    session: AsyncSession, template_id: uuid.UUID, body: CourseTemplateStatusUpdate
) -> CourseTemplateDetail:
    """Deactivate or reactivate a template: ``404`` → ``409
    version_conflict``; the same status changes nothing."""
    template = await _template_for_update(session, template_id)
    ensure_version(template, body.version)
    stored = await _load_children(session, _Stored(template))
    if template.is_active != body.is_active:
        await _bump_version(session, template, {"is_active": body.is_active})
    return await _detail_of(session, stored)


# --- Snapshots (#65) -----------------------------------------------------------------


def _snapshot(row: SnapshotRow) -> CourseTemplateSnapshot:
    return CourseTemplateSnapshot(
        id=row.id,
        template_id=row.template_id,
        template_version=row.template_version,
        content=row.content,
    )


async def _snapshot_at(
    session: AsyncSession, template_id: uuid.UUID, version: int
) -> SnapshotRow | None:
    row: SnapshotRow | None = await session.scalar(
        select(SnapshotRow).where(
            SnapshotRow.template_id == template_id,
            SnapshotRow.template_version == version,
        )
    )
    return row


async def _content(session: AsyncSession, stored: _Stored) -> dict[str, Any]:
    template = stored.template
    references = await catalog_service.subject_references(session, stored.subject_ids)
    subjects = sorted(
        (references[i] for i in stored.subject_ids if i in references),
        key=_subject_sort_key,
    )
    return build_snapshot(
        TemplateContent(
            name=template.name,
            description=template.description,
            session_count=template.session_count,
            session_minutes=template.session_minutes,
            min_attendance_percent=template.min_attendance_percent,
            subjects=[
                SnapshotSubject(
                    id=subject.id,
                    name=subject.name,
                    level_id=subject.education_level_id,
                    level_name=subject.education_level_name,
                )
                for subject in subjects
            ],
            modules=[_module_item(module) for module in stored.modules],
            assessments=[_assessment_item(item) for item in stored.assessments],
        )
    )


async def snapshot_course_template(
    session: AsyncSession, template_id: uuid.UUID
) -> CourseTemplateSnapshot:
    """The snapshot of the template's current version, for a new cohort
    (#65): the stored one when it exists, otherwise a new one.

    The template row is locked ``FOR SHARE`` until the caller's transaction
    ends, so the template can't change before the cohort commits (a
    concurrent ``PUT`` waits). Raises :class:`CourseTemplateNotFound` or
    :class:`CourseTemplateInactive` (nothing is written). A concurrent
    snapshot of the same version is absorbed (one row, the same id).
    Flushes in the caller's transaction; never commits.
    """
    template = await session.scalar(
        select(CourseTemplate)
        .where(CourseTemplate.id == template_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if template is None:
        raise CourseTemplateNotFound()
    if not template.is_active:
        raise CourseTemplateInactive()
    existing = await _snapshot_at(session, template.id, template.version)
    if existing is not None:
        return _snapshot(existing)
    content = await _content(session, await _load_children(session, _Stored(template)))
    row = SnapshotRow(
        template_id=template.id,
        template_version=template.version,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        content=content,
    )
    try:
        # A savepoint, so a concurrent snapshot of the same version (the
        # unique constraint) leaves the caller's transaction usable.
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as exc:
        if SNAPSHOT_VERSION_UNIQUE not in str(exc.orig):
            raise
        existing = await _snapshot_at(session, template.id, template.version)
        if existing is None:  # pragma: no cover (the winner committed)
            raise
        return _snapshot(existing)
    return _snapshot(row)


async def get_course_template_snapshot(
    session: AsyncSession, snapshot_id: uuid.UUID
) -> CourseTemplateSnapshot | None:
    """One snapshot by id, or ``None`` when unknown."""
    row = await session.get(SnapshotRow, snapshot_id)
    return None if row is None else _snapshot(row)
