"""The academic catalog: education levels and subjects (#32; spec §4).

Academic periods and center closures (#38) are implemented in
:mod:`app.modules.catalog.periods` and re-exported below: the reads and
writes of both, and the closure checks other modules use
(:func:`closures_between`, :func:`is_center_closed_on`,
:func:`is_center_closed_at`). Classrooms (#39) are implemented in
:mod:`app.modules.catalog.classrooms` and re-exported below, with the checks
other modules use (:func:`get_classroom`, :func:`classroom_references`,
:func:`active_classroom_ids`, :func:`is_classroom_available`; for the
M4-05 slot search, :func:`classroom_weekly_windows`, one statement, and the
pure :func:`windows_cover`).

**Reads** (every signed-in user): :func:`list_education_levels`,
:func:`get_education_level`, :func:`list_subjects`, :func:`get_subject`. The
list functions take a ``status`` (``active`` by default, so a select offers
only usable values) and run at most three statements whatever the page size.

**Other modules** (ADR-0002) call this module only through these functions
and :mod:`app.modules.catalog.schemas`, never through its models. A record
that points at a level or subject keeps it by id with a foreign key (``ON
DELETE RESTRICT``); a new or edited record offers only active ones, and an
existing reference to a deactivated one stays valid and is still shown
(:func:`get_education_level` returns inactive rows).
:func:`ensure_active_education_level` checks a level a new record picks.
:func:`get_level_for_reference` and :func:`education_level_references` give
the id, name and active flag of levels a record points at (#33: one row,
optionally locked ``FOR SHARE``, or one statement for a page of records);
:func:`get_subject_for_reference` and :func:`subject_references` do the same
for subjects, with their level (#44 tutor qualifications).

**Writes** (``catalog.manage``): create and ``PATCH`` (rename, reorder,
deactivate, reactivate). Nothing is ever deleted. Every change sends the
``version`` the caller loaded (``409 version_conflict``); a body that changes
nothing changes no version and writes no audit event.

**Rules:**

- names are unique ignoring case (inactive records count): level names across
  all levels, subject names within their level (``409 name_taken``). The
  service checks first and maps a unique violation at flush to the same error,
  so concurrent creates give one success and one ``409``;
- an active subject always has an active level: a level with an active
  subject can't be deactivated (``409 education_level_has_active_subjects``;
  the level row is locked ``FOR UPDATE`` before counting), and a subject can't
  be created or reactivated under an inactive level (``409
  education_level_inactive``; the level row is locked ``FOR SHARE`` first).
  Either lock order makes a concurrent pair end consistent: one of them fails;
- a subject's level never changes.

**Audit:** automatic only (#22): ``create`` and ``update`` events of both
tables with the actor and request ID. No named event and no ``details`` key.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from typing import Final

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.catalog.classrooms import (
    active_classroom_ids as active_classroom_ids,
)
from app.modules.catalog.classrooms import (
    classroom_references as classroom_references,
)
from app.modules.catalog.classrooms import (
    classroom_weekly_windows as classroom_weekly_windows,
)
from app.modules.catalog.classrooms import (
    create_classroom as create_classroom,
)
from app.modules.catalog.classrooms import (
    get_classroom as get_classroom,
)
from app.modules.catalog.classrooms import (
    is_classroom_available as is_classroom_available,
)
from app.modules.catalog.classrooms import (
    list_classrooms as list_classrooms,
)
from app.modules.catalog.classrooms import (
    update_classroom as update_classroom,
)
from app.modules.catalog.classrooms import (
    windows_cover as windows_cover,
)
from app.modules.catalog.errors import (
    EducationLevelHasActiveSubjectsError,
    EducationLevelInactiveError,
    NameTakenError,
)
from app.modules.catalog.models import (
    LEVEL_NAME_INDEX,
    SORT_ORDER_MAX,
    SUBJECT_NAME_INDEX,
    EducationLevel,
    Subject,
)
from app.modules.catalog.periods import (
    check_closures as check_closures,
)
from app.modules.catalog.periods import (
    closures_between as closures_between,
)
from app.modules.catalog.periods import (
    create_academic_period as create_academic_period,
)
from app.modules.catalog.periods import (
    create_closure as create_closure,
)
from app.modules.catalog.periods import (
    get_academic_period as get_academic_period,
)
from app.modules.catalog.periods import (
    get_closure as get_closure,
)
from app.modules.catalog.periods import (
    is_center_closed_at as is_center_closed_at,
)
from app.modules.catalog.periods import (
    is_center_closed_on as is_center_closed_on,
)
from app.modules.catalog.periods import (
    list_academic_periods as list_academic_periods,
)
from app.modules.catalog.periods import (
    list_closures as list_closures,
)
from app.modules.catalog.periods import (
    update_academic_period as update_academic_period,
)
from app.modules.catalog.periods import (
    update_closure as update_closure,
)
from app.modules.catalog.schemas import (
    CatalogStatus,
    EducationLevelCreate,
    EducationLevelDetail,
    EducationLevelReference,
    EducationLevelUpdate,
    SubjectCreate,
    SubjectDetail,
    SubjectReference,
    SubjectUpdate,
)

SORT_ORDER_STEP: Final = 10
"""A level created without ``sort_order`` goes this far after the last one."""


def _status_condition(
    column: InstrumentedAttribute[bool], status: CatalogStatus
) -> list[ColumnElement[bool]]:
    if status == "active":
        return [column.is_(True)]
    if status == "inactive":
        return [column.is_(False)]
    return []


def _active_subject_count() -> ColumnElement[int]:
    """The number of active subjects of the level in the outer query."""
    return (
        select(func.count(Subject.id))
        .where(Subject.education_level_id == EducationLevel.id)
        .where(Subject.is_active.is_(True))
        .correlate(EducationLevel)
        .scalar_subquery()
    )


def _level_detail(level: EducationLevel, active_subjects: int) -> EducationLevelDetail:
    return EducationLevelDetail(
        id=level.id,
        name=level.name,
        sort_order=level.sort_order,
        is_active=level.is_active,
        active_subject_count=active_subjects,
        version=level.version,
        created_at=level.created_at,
        updated_at=level.updated_at,
    )


def _subject_detail(subject: Subject, level_name: str) -> SubjectDetail:
    return SubjectDetail(
        id=subject.id,
        education_level_id=subject.education_level_id,
        education_level_name=level_name,
        name=subject.name,
        is_active=subject.is_active,
        version=subject.version,
        created_at=subject.created_at,
        updated_at=subject.updated_at,
    )


# --- Reads ---------------------------------------------------------------------------


async def list_education_levels(
    session: AsyncSession, *, status: CatalogStatus = "active", page: OffsetParams
) -> OffsetPage[EducationLevelDetail]:
    """The levels with ``status``, by ``sort_order``, then ``lower(name)``,
    then ``id``. Two statements: the page (with each level's active-subject
    count) and the total."""
    conditions = _status_condition(EducationLevel.is_active, status)
    rows = await session.execute(
        select(EducationLevel, _active_subject_count())
        .where(*conditions)
        .order_by(
            EducationLevel.sort_order,
            func.lower(EducationLevel.name),
            EducationLevel.id,
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [_level_detail(level, count) for level, count in rows]
    total = await session.scalar(
        select(func.count(EducationLevel.id)).where(*conditions)
    )
    return OffsetPage[EducationLevelDetail](
        items=items, total=total or 0, limit=page.limit, offset=page.offset
    )


async def _level_detail_of(
    session: AsyncSession, level: EducationLevel
) -> EducationLevelDetail:
    count = await session.scalar(
        select(func.count(Subject.id))
        .where(Subject.education_level_id == level.id)
        .where(Subject.is_active.is_(True))
    )
    return _level_detail(level, count or 0)


async def get_education_level(
    session: AsyncSession, level_id: uuid.UUID
) -> EducationLevelDetail:
    """One level, active or not; ``404 not_found`` when unknown."""
    level = await session.get(EducationLevel, level_id)
    if level is None:
        raise RecordNotFoundError()
    return await _level_detail_of(session, level)


async def ensure_active_education_level(
    session: AsyncSession, level_id: uuid.UUID
) -> None:
    """For a module whose new record picks a level (#33): ``404 not_found``
    when unknown, ``409 education_level_inactive`` when inactive. The level
    row is locked ``FOR SHARE`` until the transaction ends, so it can't be
    deactivated in between."""
    await _active_level_for_share(session, level_id)


def _reference(level: EducationLevel) -> EducationLevelReference:
    return EducationLevelReference(
        id=level.id, name=level.name, is_active=level.is_active
    )


async def get_level_for_reference(
    session: AsyncSession, level_id: uuid.UUID, *, lock: bool
) -> EducationLevelReference | None:
    """The level a record points at (active or not), or ``None`` when unknown.

    With ``lock`` the row is locked ``FOR SHARE`` until the transaction ends,
    so a level checked as active can't be deactivated before the record that
    picks it commits (#33 C2). One statement.
    """
    query = select(EducationLevel).where(EducationLevel.id == level_id)
    if lock:
        query = query.with_for_update(read=True)
    level = await session.scalar(query.execution_options(populate_existing=True))
    return None if level is None else _reference(level)


async def education_level_references(
    session: AsyncSession, level_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, EducationLevelReference]:
    """The levels with these ids (unknown ids are left out), in one statement;
    none when ``level_ids`` is empty."""
    if not level_ids:
        return {}
    levels = await session.scalars(
        select(EducationLevel).where(EducationLevel.id.in_(set(level_ids)))
    )
    return {level.id: _reference(level) for level in levels}


def _subject_reference(subject: Subject, level: EducationLevel) -> SubjectReference:
    return SubjectReference(
        id=subject.id,
        name=subject.name,
        is_active=subject.is_active,
        education_level_id=level.id,
        education_level_name=level.name,
        education_level_is_active=level.is_active,
        education_level_sort_order=level.sort_order,
    )


def _subject_reference_query() -> Select[Subject, EducationLevel]:
    return select(Subject, EducationLevel).join(
        EducationLevel, EducationLevel.id == Subject.education_level_id
    )


async def get_subject_for_reference(
    session: AsyncSession, subject_id: uuid.UUID, *, lock: bool
) -> SubjectReference | None:
    """The subject a new record picks (active or not) with its level, or
    ``None`` when unknown (#44).

    With ``lock`` both rows are locked ``FOR SHARE`` until the transaction
    ends, so a subject or level checked as active can't be deactivated
    before the record that picks it commits. One statement.
    """
    query = _subject_reference_query().where(Subject.id == subject_id)
    if lock:
        query = query.with_for_update(read=True)
    row = (
        await session.execute(query.execution_options(populate_existing=True))
    ).first()
    return None if row is None else _subject_reference(*row)


async def subject_references(
    session: AsyncSession, subject_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, SubjectReference]:
    """The subjects with these ids, with their levels (unknown ids are left
    out), in one statement; none when ``subject_ids`` is empty (#44)."""
    if not subject_ids:
        return {}
    rows = await session.execute(
        _subject_reference_query().where(Subject.id.in_(set(subject_ids)))
    )
    return {subject.id: _subject_reference(subject, level) for subject, level in rows}


def _subjects_query() -> Select[Subject, str]:
    return select(Subject, EducationLevel.name).join(
        EducationLevel, EducationLevel.id == Subject.education_level_id
    )


async def list_subjects(
    session: AsyncSession,
    *,
    education_level_id: uuid.UUID | None = None,
    status: CatalogStatus = "active",
    page: OffsetParams,
) -> OffsetPage[SubjectDetail]:
    """The subjects with ``status`` (of one level when given; an unknown level
    gives an empty page), by the level's ``sort_order`` and ``lower(name)``,
    then the subject's ``lower(name)``, then ``id``. Two statements."""
    conditions = _status_condition(Subject.is_active, status)
    if education_level_id is not None:
        conditions.append(Subject.education_level_id == education_level_id)
    rows = await session.execute(
        _subjects_query()
        .where(*conditions)
        .order_by(
            EducationLevel.sort_order,
            func.lower(EducationLevel.name),
            func.lower(Subject.name),
            Subject.id,
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [_subject_detail(subject, level_name) for subject, level_name in rows]
    total = await session.scalar(select(func.count(Subject.id)).where(*conditions))
    return OffsetPage[SubjectDetail](
        items=items, total=total or 0, limit=page.limit, offset=page.offset
    )


async def get_subject(session: AsyncSession, subject_id: uuid.UUID) -> SubjectDetail:
    """One subject, active or not; ``404 not_found`` when unknown."""
    row = (
        await session.execute(_subjects_query().where(Subject.id == subject_id))
    ).first()
    if row is None:
        raise RecordNotFoundError()
    subject, level_name = row
    return _subject_detail(subject, level_name)


# --- Writes --------------------------------------------------------------------------


async def _flush_unique_name(session: AsyncSession, index_name: str) -> None:
    """Flush; a violation of the name index ``index_name`` (a concurrent
    request took the name after the pre-check) is ``409 name_taken``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if index_name in str(exc.orig):
            raise NameTakenError() from None
        raise


async def _ensure_level_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(EducationLevel.id).where(
        func.lower(EducationLevel.name) == func.lower(name)
    )
    if except_id is not None:
        query = query.where(EducationLevel.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise NameTakenError()


async def _ensure_subject_name_free(
    session: AsyncSession,
    level_id: uuid.UUID,
    name: str,
    *,
    except_id: uuid.UUID | None = None,
) -> None:
    query = (
        select(Subject.id)
        .where(Subject.education_level_id == level_id)
        .where(func.lower(Subject.name) == func.lower(name))
    )
    if except_id is not None:
        query = query.where(Subject.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise NameTakenError()


async def _next_sort_order(session: AsyncSession) -> int:
    highest = await session.scalar(select(func.max(EducationLevel.sort_order)))
    return min((highest or 0) + SORT_ORDER_STEP, SORT_ORDER_MAX)


async def create_education_level(
    session: AsyncSession, body: EducationLevelCreate
) -> EducationLevelDetail:
    """A new active level (``409 name_taken`` when the name is used)."""
    await _ensure_level_name_free(session, body.name)
    sort_order = (
        body.sort_order
        if body.sort_order is not None
        else await _next_sort_order(session)
    )
    level = EducationLevel(name=body.name, sort_order=sort_order, is_active=True)
    session.add(level)
    await _flush_unique_name(session, LEVEL_NAME_INDEX)
    return _level_detail(level, 0)


async def _level_for_update(
    session: AsyncSession, level_id: uuid.UUID
) -> EducationLevel:
    level = await session.scalar(
        select(EducationLevel)
        .where(EducationLevel.id == level_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if level is None:
        raise RecordNotFoundError()
    return level


async def update_education_level(
    session: AsyncSession, level_id: uuid.UUID, body: EducationLevelUpdate
) -> EducationLevelDetail:
    """Rename, reorder, deactivate or reactivate a level.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``409 name_taken`` → ``409 education_level_has_active_subjects`` (when
    deactivating) → the update. The level row is locked ``FOR UPDATE`` first,
    so the active-subject count can't race a subject being created or
    reactivated. Reactivating changes no subject.
    """
    level = await _level_for_update(session, level_id)
    ensure_version(level, body.version)
    sent = body.model_fields_set
    changed = False
    if "name" in sent and body.name is not None and body.name != level.name:
        await _ensure_level_name_free(session, body.name, except_id=level.id)
        level.name = body.name
        changed = True
    if (
        "sort_order" in sent
        and body.sort_order is not None
        and body.sort_order != level.sort_order
    ):
        level.sort_order = body.sort_order
        changed = True
    if "is_active" in sent and body.is_active is not None:
        if level.is_active and not body.is_active:
            if await _has_active_subjects(session, level.id):
                raise EducationLevelHasActiveSubjectsError()
        if body.is_active != level.is_active:
            level.is_active = body.is_active
            changed = True
    if changed:
        await _flush_unique_name(session, LEVEL_NAME_INDEX)
    return await _level_detail_of(session, level)


async def _has_active_subjects(session: AsyncSession, level_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Subject.id)
        .where(Subject.education_level_id == level_id)
        .where(Subject.is_active.is_(True))
        .limit(1)
    )
    return found is not None


async def _level_for_share(
    session: AsyncSession, level_id: uuid.UUID
) -> EducationLevel | None:
    level: EducationLevel | None = await session.scalar(
        select(EducationLevel)
        .where(EducationLevel.id == level_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return level


async def _active_level_for_share(
    session: AsyncSession, level_id: uuid.UUID
) -> EducationLevel:
    level = await _level_for_share(session, level_id)
    if level is None:
        raise RecordNotFoundError()
    if not level.is_active:
        raise EducationLevelInactiveError()
    return level


async def create_subject(
    session: AsyncSession, level_id: uuid.UUID, body: SubjectCreate
) -> SubjectDetail:
    """A new active subject of the level: ``404`` when the level is unknown,
    ``409 education_level_inactive`` when it is inactive, ``409 name_taken``
    when the level already has the name."""
    level = await _active_level_for_share(session, level_id)
    await _ensure_subject_name_free(session, level.id, body.name)
    subject = Subject(education_level_id=level.id, name=body.name, is_active=True)
    session.add(subject)
    await _flush_unique_name(session, SUBJECT_NAME_INDEX)
    return _subject_detail(subject, level.name)


async def update_subject(
    session: AsyncSession, subject_id: uuid.UUID, body: SubjectUpdate
) -> SubjectDetail:
    """Rename, deactivate or reactivate a subject.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``409 name_taken`` → ``409 education_level_inactive`` (when reactivating;
    the level row is locked ``FOR SHARE`` first) → the update. Renaming or
    deactivating a subject of an inactive level is allowed.
    """
    subject = await session.get(Subject, subject_id)
    if subject is None:
        raise RecordNotFoundError()
    ensure_version(subject, body.version)
    sent = body.model_fields_set
    changed = False
    if "name" in sent and body.name is not None and body.name != subject.name:
        await _ensure_subject_name_free(
            session, subject.education_level_id, body.name, except_id=subject.id
        )
        subject.name = body.name
        changed = True
    level: EducationLevel | None = None
    if "is_active" in sent and body.is_active is not None:
        if body.is_active and not subject.is_active:
            level = await _active_level_for_share(session, subject.education_level_id)
        if body.is_active != subject.is_active:
            subject.is_active = body.is_active
            changed = True
    if changed:
        await _flush_unique_name(session, SUBJECT_NAME_INDEX)
    if level is None:
        level = await session.get(EducationLevel, subject.education_level_id)
        if level is None:  # pragma: no cover - the foreign key prevents it
            raise RecordNotFoundError()
    return _subject_detail(subject, level.name)
