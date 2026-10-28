"""Family groupings and their membership (#172; spec §2, §6/W4, §16).

A **family** is a named grouping of students and guardian profiles that
M6-05 attaches a billing account to. Owner decision #172 E2 (a): each student
and each guardian profile belongs to **at most one** family (the unique
constraints ``uq_family_students_student_id`` and
``uq_family_guardians_guardian_id``).

**Membership is not access.** What a guardian sees of a student follows the
#34 links only (``facts.load_access_facts``); nothing here adds to
``linked_student_ids``. A family member who isn't linked to a child doesn't
see that child's record (ADR-0007 D17: billing follows the family, academic
visibility follows the link and consent).

**Reads** (``guardian.view``; there is no ``family.view``): staff with
``guardian.view`` see every family; a ``guardian`` sees only the family of
their active profile, with its students' names and only their own profile in
``guardians`` (#34 E1: never the co-guardians). A hidden family is ``404``,
the same as a missing one.

**Writes** (``family.manage``): create, rename, deactivate/reactivate
(``PATCH`` with ``version``). Families are never deleted. Membership is a
separate, idempotent ``PUT`` per member and a ``DELETE``; it never changes
the family's ``version`` (so it can't make a concurrent rename fail), nor the
student's or guardian's. Moving a member is two steps: remove, then add.

**Audit:** automatic only (#22): ``families`` ``create``/``update`` and the
membership rows' ``create``/``delete``. No named event, no ``details``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError, authorize_record
from app.core.models import Model, ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.families import search
from app.modules.families.ages import is_minor
from app.modules.families.errors import FamilyInactiveError, FamilyMemberConflictError
from app.modules.families.facts import load_family_facts
from app.modules.families.models import (
    FAMILY_GUARDIAN_UNIQUE,
    FAMILY_STUDENT_UNIQUE,
    Family,
    FamilyGuardian,
    FamilyStudent,
    Guardian,
    Student,
)
from app.modules.families.permissions import (
    can_view_family,
    sees_every_family,
)
from app.modules.families.schemas import (
    FamilyCreate,
    FamilyDetail,
    FamilyGuardianItem,
    FamilyListItem,
    FamilyReference,
    FamilyStatus,
    FamilyStudentItem,
    FamilyUpdate,
)

FAMILY_ORDER: Final = (search.normalized(Family.name), Family.id)
STUDENT_ORDER: Final = search.default_order(
    Student.given_names, Student.family_names, Student.id
)
GUARDIAN_ORDER: Final = search.default_order(
    Guardian.given_names, Guardian.family_names, Guardian.id
)


# --- References for the student and guardian details (#172 E1) -------------------


async def family_of_student(
    session: AsyncSession, student_id: uuid.UUID
) -> FamilyReference | None:
    """The student's family (one statement), or ``None``."""
    row = (
        await session.execute(
            select(Family.id, Family.name)
            .join(FamilyStudent, FamilyStudent.family_id == Family.id)
            .where(FamilyStudent.student_id == student_id)
        )
    ).first()
    return None if row is None else FamilyReference(id=row.id, name=row.name)


async def families_of_students(
    session: AsyncSession, student_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, FamilyReference]:
    """The families of these students, by student id; a student without a
    family (or unknown) is left out. One statement (none for an empty
    collection); no access check (#91 D4: the unbilled list)."""
    wanted = set(student_ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(FamilyStudent.student_id, Family.id, Family.name)
        .join(Family, Family.id == FamilyStudent.family_id)
        .where(FamilyStudent.student_id.in_(wanted))
    )
    return {row.student_id: FamilyReference(id=row.id, name=row.name) for row in rows}


async def family_of_guardian(
    session: AsyncSession, guardian_id: uuid.UUID
) -> FamilyReference | None:
    """The guardian profile's family (one statement), or ``None``."""
    row = (
        await session.execute(
            select(Family.id, Family.name)
            .join(FamilyGuardian, FamilyGuardian.family_id == Family.id)
            .where(FamilyGuardian.guardian_id == guardian_id)
        )
    ).first()
    return None if row is None else FamilyReference(id=row.id, name=row.name)


@dataclass(frozen=True)
class FamilyRecordReference:
    """A family's id, name and active flag with its current students' ids
    (#364 B5; for billing, which follows the family, ADR-0007 D17)."""

    id: uuid.UUID
    name: str
    is_active: bool
    student_ids: tuple[uuid.UUID, ...]


async def family_record_reference(
    session: AsyncSession, family_id: uuid.UUID
) -> FamilyRecordReference | None:
    """The family with its current students' ids (by id), or ``None`` when
    it doesn't exist. One statement; no access check (the caller's record
    policy decides)."""
    row = (
        await session.execute(
            select(
                Family.id,
                Family.name,
                Family.is_active,
                func.array_agg(FamilyStudent.student_id)
                .filter(FamilyStudent.student_id.is_not(None))
                .label("student_ids"),
            )
            .outerjoin(FamilyStudent, FamilyStudent.family_id == Family.id)
            .where(Family.id == family_id)
            .group_by(Family.id)
        )
    ).first()
    if row is None:
        return None
    return FamilyRecordReference(
        id=row.id,
        name=row.name,
        is_active=row.is_active,
        student_ids=tuple(sorted(row.student_ids or ())),
    )


async def own_family_id(
    session: AsyncSession, principal: Principal
) -> uuid.UUID | None:
    """The family of the caller's **active** guardian profile, only for a
    caller holding ``guardian`` (:func:`load_family_facts`: one statement,
    none otherwise). Loaded on every request, so a removed membership or a
    deactivated profile applies on the next one."""
    return (await load_family_facts(session, principal)).own_family_id


# --- Projections -------------------------------------------------------------------


async def _students_of(
    session: AsyncSession, family_id: uuid.UUID
) -> list[FamilyStudentItem]:
    students = await session.scalars(
        select(Student)
        .join(FamilyStudent, FamilyStudent.student_id == Student.id)
        .where(FamilyStudent.family_id == family_id)
        .order_by(*STUDENT_ORDER)
    )
    today = await administration.center_today(session)
    return [
        FamilyStudentItem(
            student_id=student.id,
            given_names=student.given_names,
            family_names=student.family_names,
            is_minor=is_minor(student.birthdate, today),
            is_active=student.is_active,
        )
        for student in students
    ]


async def _guardians_of(
    session: AsyncSession, principal: Principal, family_id: uuid.UUID
) -> list[FamilyGuardianItem]:
    """Every guardian of the family for staff; for a guardian viewer only
    their own (active) profile (#34 E1: never the co-guardians)."""
    query = (
        select(Guardian)
        .join(FamilyGuardian, FamilyGuardian.guardian_id == Guardian.id)
        .where(FamilyGuardian.family_id == family_id)
        .order_by(*GUARDIAN_ORDER)
    )
    if not sees_every_family(principal):
        query = query.where(
            Guardian.user_id == principal.user_id, Guardian.is_active.is_(True)
        )
    return [
        FamilyGuardianItem(
            guardian_id=guardian.id,
            given_names=guardian.given_names,
            family_names=guardian.family_names,
            phone=guardian.phone,
            has_account=guardian.user_id is not None,
            is_active=guardian.is_active,
        )
        for guardian in await session.scalars(query)
    ]


async def _detail(
    session: AsyncSession, principal: Principal, family: Family
) -> FamilyDetail:
    return FamilyDetail(
        id=family.id,
        name=family.name,
        is_active=family.is_active,
        students=await _students_of(session, family.id),
        guardians=await _guardians_of(session, principal, family.id),
        version=family.version,
        created_at=family.created_at,
        updated_at=family.updated_at,
    )


# --- Reads -------------------------------------------------------------------------


def _status_conditions(status: FamilyStatus) -> list[ColumnElement[bool]]:
    if status == "active":
        return [Family.is_active.is_(True)]
    if status == "inactive":
        return [Family.is_active.is_(False)]
    return []


async def list_families(
    session: AsyncSession,
    *,
    principal: Principal,
    q: str | None,
    status: FamilyStatus,
    page: OffsetParams,
) -> OffsetPage[FamilyListItem]:
    """The families the caller may see that match every filter.

    Two statements for staff (the page with each family's student count as a
    subquery, and the total). A caller without a staff role sees at most the
    family of their active profile (one more statement; an empty page when
    they have none).
    """
    conditions = _status_conditions(status)
    if not sees_every_family(principal):
        facts = await load_family_facts(session, principal)
        if facts.own_family_id is None:
            return OffsetPage[FamilyListItem](
                items=[], total=0, limit=page.limit, offset=page.offset
            )
        conditions.append(Family.id == facts.own_family_id)
    order: list[Any] = list(FAMILY_ORDER)
    if q:
        conditions.append(search.text_search_condition(q, Family.name))
        order.insert(0, search.text_relevance(q, Family.name).desc())
    student_count = (
        select(func.count(FamilyStudent.id))
        .where(FamilyStudent.family_id == Family.id)
        .correlate(Family)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Family, student_count)
            .where(*conditions)
            .order_by(*order)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    total = await session.scalar(select(func.count(Family.id)).where(*conditions))
    return OffsetPage[FamilyListItem](
        items=[
            FamilyListItem(
                id=family.id,
                name=family.name,
                is_active=family.is_active,
                student_count=count,
            )
            for family, count in rows
        ],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def _visible_family(
    session: AsyncSession, principal: Principal, family: Family | None
) -> Family:
    facts = await load_family_facts(session, principal)
    return authorize_record(
        family,
        can_view=lambda record: can_view_family(principal, record.id, facts),
    )


async def get_family(
    session: AsyncSession, *, principal: Principal, family_id: uuid.UUID
) -> FamilyDetail:
    """One family: ``404 not_found`` when missing or hidden."""
    family = await _visible_family(
        session, principal, await session.get(Family, family_id)
    )
    return await _detail(session, principal, family)


# --- Family writes -----------------------------------------------------------------


async def create_family(
    session: AsyncSession, *, principal: Principal, body: FamilyCreate
) -> FamilyDetail:
    """A new active family with no members (version 1)."""
    family = Family(name=body.name, is_active=True)
    session.add(family)
    await session.flush()
    return FamilyDetail(
        id=family.id,
        name=family.name,
        is_active=family.is_active,
        students=[],
        guardians=[],
        version=family.version,
        created_at=family.created_at,
        updated_at=family.updated_at,
    )


FAMILY_FIELDS: Final = ("name", "is_active")


async def update_family(
    session: AsyncSession,
    *,
    principal: Principal,
    family_id: uuid.UUID,
    body: FamilyUpdate,
) -> FamilyDetail:
    """Rename, deactivate or reactivate.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` → the
    update. The row is locked ``FOR UPDATE`` first. A body that changes
    nothing writes nothing. Deactivating keeps every member.
    """
    family = await _visible_family(
        session,
        principal,
        await session.scalar(
            select(Family)
            .where(Family.id == family_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )
    ensure_version(family, body.version)
    sent = body.model_fields_set
    changes = {
        name: getattr(body, name)
        for name in FAMILY_FIELDS
        if name in sent and getattr(body, name) != getattr(family, name)
    }
    if changes:
        for name, value in changes.items():
            setattr(family, name, value)
        await session.flush()
    return await _detail(session, principal, family)


# --- Membership --------------------------------------------------------------------


async def _family_for_membership(
    session: AsyncSession, family_id: uuid.UUID
) -> Family | None:
    """The family read ``FOR SHARE``: a concurrent deactivation waits for this
    transaction (and a rename only waits; membership never changes the
    family's ``version``)."""
    family: Family | None = await session.scalar(
        select(Family)
        .where(Family.id == family_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return family


async def _add_member(
    session: AsyncSession,
    *,
    family_id: uuid.UUID,
    member_model: type[Model],
    member_id: uuid.UUID,
    membership: type[FamilyStudent] | type[FamilyGuardian],
    member_column: str,
    unique: str,
) -> None:
    """The C1 order: unknown family or member (the same ``404``) → an inactive
    family (``409 family_inactive``) → already in this family (no-op) → in
    another family (``409 family_member_conflict``) → the insert."""
    family = await _family_for_membership(session, family_id)
    member = await session.get(member_model, member_id)
    if family is None or member is None:
        raise RecordNotFoundError()
    if not family.is_active:
        raise FamilyInactiveError()
    column = getattr(membership, member_column)

    async def current_family() -> uuid.UUID | None:
        found: uuid.UUID | None = await session.scalar(
            select(membership.family_id).where(column == member_id)
        )
        return found

    existing = await current_family()
    if existing == family.id:
        return
    if existing is not None:
        raise FamilyMemberConflictError()
    try:
        async with session.begin_nested():
            session.add(membership(family_id=family.id, **{member_column: member_id}))
    except IntegrityError as exc:
        if unique not in str(exc.orig):
            raise
        # A concurrent add committed first: the same family is the idempotent
        # no-op; another family is the conflict.
        if await current_family() == family.id:
            return
        raise FamilyMemberConflictError() from None


async def add_student(
    session: AsyncSession,
    *,
    principal: Principal,
    family_id: uuid.UUID,
    student_id: uuid.UUID,
) -> None:
    """Add a student (an inactive student may be added). Idempotent."""
    await _add_member(
        session,
        family_id=family_id,
        member_model=Student,
        member_id=student_id,
        membership=FamilyStudent,
        member_column="student_id",
        unique=FAMILY_STUDENT_UNIQUE,
    )


async def add_guardian(
    session: AsyncSession,
    *,
    principal: Principal,
    family_id: uuid.UUID,
    guardian_id: uuid.UUID,
) -> None:
    """Add a guardian profile (an inactive one may be added). Idempotent."""
    await _add_member(
        session,
        family_id=family_id,
        member_model=Guardian,
        member_id=guardian_id,
        membership=FamilyGuardian,
        member_column="guardian_id",
        unique=FAMILY_GUARDIAN_UNIQUE,
    )


async def _remove_member(
    session: AsyncSession,
    membership: type[FamilyStudent] | type[FamilyGuardian],
    condition: ColumnElement[bool],
) -> None:
    row = await session.scalar(select(membership).where(condition))
    if row is None:
        raise RecordNotFoundError()
    await session.delete(row)
    await session.flush()


async def remove_student(
    session: AsyncSession,
    *,
    principal: Principal,
    family_id: uuid.UUID,
    student_id: uuid.UUID,
) -> None:
    """Remove a student from **this** family; unknown ids or a student who
    isn't a member of it are ``404``."""
    await _remove_member(
        session,
        FamilyStudent,
        (FamilyStudent.family_id == family_id)
        & (FamilyStudent.student_id == student_id),
    )


async def remove_guardian(
    session: AsyncSession,
    *,
    principal: Principal,
    family_id: uuid.UUID,
    guardian_id: uuid.UUID,
) -> None:
    """Remove a guardian profile from **this** family (``404`` otherwise).
    The guardian's account stops seeing the family on its next request."""
    await _remove_member(
        session,
        FamilyGuardian,
        (FamilyGuardian.family_id == family_id)
        & (FamilyGuardian.guardian_id == guardian_id),
    )


__all__ = [
    "FamilyRecordReference",
    "add_guardian",
    "add_student",
    "create_family",
    "families_of_students",
    "family_of_guardian",
    "family_of_student",
    "family_record_reference",
    "get_family",
    "list_families",
    "own_family_id",
    "remove_guardian",
    "remove_student",
    "update_family",
]
