"""Guardian profiles and student-guardian links (#34; spec §2, §12, §16).

**Profiles** (``guardians``): a parent or guardian, with or without a login.
Holders of ``family.manage`` (Owners, Administrators) create and edit them;
there is no delete (deactivate with ``is_active``; deletion belongs to
M10-07). A profile may be linked to **one** account (``user_id``); that
account then sees the profile's linked students while it holds the
``guardian`` role and the profile is active.

**Account rule** (B2), checked only when ``user_id`` changes: the account
exists, isn't ``archived`` and holds ``guardian`` (``pending`` and
``suspended`` accounts may be linked: neither can sign in). Otherwise ``422
guardian_account_unavailable``. An account already linked to another profile
is ``409 guardian_account_in_use`` (a pre-check, and a violation of exactly
``uq_guardians_user_id`` maps to the same code). The account is read through
``identity.service`` (ADR-0002).

**Links** (``student_guardians``): ``family.manage`` links and unlinks; a link
carries a relationship code and is never edited (unlink and link again). A
link never changes either profile's ``version``. Unlinking hard-deletes the
row; its audit ``delete`` event keeps the values.

**Reads** (``guardian.view``): staff with ``guardian.view`` see every profile;
a ``guardian`` sees only their own active profile (``can_view_guardian``); a
hidden profile is ``404``, the same as a missing one. ``account`` (username,
display name, state) is shown only to ``family.manage`` holders. ``family``
(#172 E1) is shown to every viewer of the profile: staff with
``guardian.view`` and the guardian viewing their own profile.

**Audit:** automatic only (#22): ``guardians`` and ``student_guardians``
``create``/``update``/``delete`` events with the actor and request ID; an
account link is an ``update`` of ``user_id``. No named event, no ``details``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError, authorize_record
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.families import consent, logins, search
from app.modules.families.ages import is_minor
from app.modules.families.assignments import TutorAssignmentSource
from app.modules.families.errors import (
    GuardianAccountInUseError,
    GuardianAccountUnavailableError,
    GuardianAlreadyLinkedError,
    GuardianUnavailableError,
)
from app.modules.families.facts import load_access_facts, load_guardian_facts
from app.modules.families.groupings import family_of_guardian
from app.modules.families.models import (
    GUARDIAN_USER_UNIQUE,
    LINK_UNIQUE,
    Guardian,
    Student,
    StudentGuardian,
)
from app.modules.families.permissions import (
    FAMILY_MANAGE,
    GUARDIAN,
    can_view_guardian,
    can_view_student,
    sees_every_guardian,
)
from app.modules.families.schemas import (
    GuardianAccount,
    GuardianCreate,
    GuardianDetail,
    GuardianListItem,
    GuardianRelationship,
    GuardianStatus,
    GuardianStudentItem,
    GuardianUpdate,
    StudentGuardianCreate,
    StudentGuardianItem,
)
from app.modules.identity import service as identity

ARCHIVED: Final = "archived"
USER_ID_PATH: Final[list[str | int]] = ["body", "user_id"]
GUARDIAN_ID_PATH: Final[list[str | int]] = ["body", "guardian_id"]

GUARDIAN_ORDER: Final = search.default_order(
    Guardian.given_names, Guardian.family_names, Guardian.id
)
STUDENT_ORDER: Final = search.default_order(
    Student.given_names, Student.family_names, Student.id
)


# --- Projections -----------------------------------------------------------------


async def student_guardian_items(
    session: AsyncSession, student_id: uuid.UUID
) -> list[StudentGuardianItem]:
    """The guardians linked to a student, in the default name order (one
    statement)."""
    rows = await session.execute(
        select(StudentGuardian.relationship, Guardian)
        .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
        .where(StudentGuardian.student_id == student_id)
        .order_by(*GUARDIAN_ORDER)
    )
    return [
        _student_guardian_item(guardian, relationship)
        for relationship, guardian in rows
    ]


def _student_guardian_item(
    guardian: Guardian, relationship: str
) -> StudentGuardianItem:
    return StudentGuardianItem(
        guardian_id=guardian.id,
        given_names=guardian.given_names,
        family_names=guardian.family_names,
        relationship=cast(GuardianRelationship, relationship),
        phone=guardian.phone,
        is_active=guardian.is_active,
        has_account=guardian.user_id is not None,
    )


async def _students_of(
    session: AsyncSession, guardian_id: uuid.UUID
) -> list[GuardianStudentItem]:
    rows = await session.execute(
        select(StudentGuardian.relationship, Student)
        .join(Student, Student.id == StudentGuardian.student_id)
        .where(StudentGuardian.guardian_id == guardian_id)
        .order_by(*STUDENT_ORDER)
    )
    today = await administration.center_today(session)
    return [
        GuardianStudentItem(
            student_id=student.id,
            given_names=student.given_names,
            family_names=student.family_names,
            relationship=cast(GuardianRelationship, relationship),
            is_minor=is_minor(student.birthdate, today),
            is_active=student.is_active,
        )
        for relationship, student in rows
    ]


async def _detail(
    session: AsyncSession, principal: Principal, guardian: Guardian
) -> GuardianDetail:
    account = None
    if guardian.user_id is not None and principal.has(FAMILY_MANAGE):
        reference = await identity.get_account_reference(session, guardian.user_id)
        if reference is not None:  # pragma: no branch - the FK guarantees it
            account = GuardianAccount(
                id=reference.id,
                username=reference.username,
                display_name=reference.display_name,
                state=reference.state,
            )
    return GuardianDetail(
        id=guardian.id,
        given_names=guardian.given_names,
        family_names=guardian.family_names,
        email=guardian.email,
        phone=guardian.phone,
        is_active=guardian.is_active,
        has_account=guardian.user_id is not None,
        account=account,
        students=await _students_of(session, guardian.id),
        family=await family_of_guardian(session, guardian.id),
        version=guardian.version,
        created_at=guardian.created_at,
        updated_at=guardian.updated_at,
    )


# --- Reads -----------------------------------------------------------------------


def _status_conditions(status: GuardianStatus) -> list[ColumnElement[bool]]:
    if status == "active":
        return [Guardian.is_active.is_(True)]
    if status == "inactive":
        return [Guardian.is_active.is_(False)]
    return []


async def list_guardians(
    session: AsyncSession,
    *,
    principal: Principal,
    q: str | None,
    status: GuardianStatus,
    page: OffsetParams,
) -> OffsetPage[GuardianListItem]:
    """The guardian profiles the caller may see that match every filter.

    Two statements for staff (the page with each profile's link count as a
    subquery, and the total). A caller without a staff role sees at most their
    own active profile (one more statement; none at all when they have none).
    """
    conditions = _status_conditions(status)
    if not sees_every_guardian(principal):
        facts = await load_guardian_facts(session, principal)
        if facts.own_guardian_id is None:
            return OffsetPage[GuardianListItem](
                items=[], total=0, limit=page.limit, offset=page.offset
            )
        conditions.append(Guardian.id == facts.own_guardian_id)
    order: list[Any] = list(GUARDIAN_ORDER)
    if q:
        conditions.append(
            search.name_search_condition(q, Guardian.given_names, Guardian.family_names)
        )
        order.insert(
            0, search.relevance(q, Guardian.given_names, Guardian.family_names).desc()
        )
    student_count = (
        select(func.count(StudentGuardian.id))
        .where(StudentGuardian.guardian_id == Guardian.id)
        .correlate(Guardian)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Guardian, student_count)
            .where(*conditions)
            .order_by(*order)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    total = await session.scalar(select(func.count(Guardian.id)).where(*conditions))
    return OffsetPage[GuardianListItem](
        items=[
            GuardianListItem(
                id=guardian.id,
                given_names=guardian.given_names,
                family_names=guardian.family_names,
                is_active=guardian.is_active,
                has_account=guardian.user_id is not None,
                student_count=count,
            )
            for guardian, count in rows
        ],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def _visible_guardian(
    session: AsyncSession,
    principal: Principal,
    guardian: Guardian | None,
) -> Guardian:
    facts = await load_guardian_facts(session, principal)
    return authorize_record(
        guardian,
        can_view=lambda record: can_view_guardian(principal, record.id, facts),
    )


async def get_guardian(
    session: AsyncSession, *, principal: Principal, guardian_id: uuid.UUID
) -> GuardianDetail:
    """One guardian profile: ``404 not_found`` when missing or hidden."""
    guardian = await _visible_guardian(
        session, principal, await session.get(Guardian, guardian_id)
    )
    return await _detail(session, principal, guardian)


# --- Profile writes --------------------------------------------------------------


def _account_unavailable() -> GuardianAccountUnavailableError:
    return GuardianAccountUnavailableError(
        errors=[
            {
                "path": USER_ID_PATH,
                "code": "guardian_account_unavailable",
                "message": (
                    "Choose an active or pending account with the guardian role."
                ),
            }
        ]
    )


async def _ensure_account_available(
    session: AsyncSession, user_id: uuid.UUID, *, guardian_id: uuid.UUID | None
) -> None:
    """B2 (the account exists, isn't archived, holds ``guardian``), then the
    in-use pre-check (no other profile has it)."""
    reference = await identity.get_account_reference(session, user_id)
    if (
        reference is None
        or reference.state == ARCHIVED
        or GUARDIAN not in reference.roles
    ):
        raise _account_unavailable()
    other = select(Guardian.id).where(Guardian.user_id == user_id)
    if guardian_id is not None:
        other = other.where(Guardian.id != guardian_id)
    if await session.scalar(other) is not None:
        raise GuardianAccountInUseError()


async def _flush_profile(session: AsyncSession, guardian: Guardian) -> None:
    """Flush the profile in a savepoint; a concurrent link of the same account
    (``uq_guardians_user_id``) is ``409 guardian_account_in_use``. Any other
    integrity error propagates."""
    try:
        async with session.begin_nested():
            session.add(guardian)
    except IntegrityError as exc:
        if GUARDIAN_USER_UNIQUE in str(exc.orig):
            raise GuardianAccountInUseError() from None
        raise


async def create_guardian(
    session: AsyncSession, *, principal: Principal, body: GuardianCreate
) -> GuardianDetail:
    """A new active profile. After the body's ``422``: the account rule (``422
    guardian_account_unavailable``, ``409 guardian_account_in_use``) → the
    insert."""
    if body.user_id is not None:
        await _ensure_account_available(session, body.user_id, guardian_id=None)
    guardian = Guardian(
        given_names=body.given_names,
        family_names=body.family_names,
        email=body.email,
        phone=body.phone,
        user_id=body.user_id,
        is_active=True,
    )
    await _flush_profile(session, guardian)
    return await _detail(session, principal, guardian)


PROFILE_FIELDS: Final = (
    "given_names",
    "family_names",
    "email",
    "phone",
    "user_id",
    "is_active",
)


async def update_guardian(
    session: AsyncSession,
    *,
    principal: Principal,
    guardian_id: uuid.UUID,
    body: GuardianUpdate,
) -> GuardianDetail:
    """Change the sent fields.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    the account rule, only when ``user_id`` changes → the update. The row is
    locked ``FOR UPDATE`` first. A body that changes nothing writes nothing.
    """
    guardian = await _visible_guardian(
        session,
        principal,
        await session.scalar(
            select(Guardian)
            .where(Guardian.id == guardian_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )
    ensure_version(guardian, body.version)
    sent = body.model_fields_set
    changes = {
        name: getattr(body, name)
        for name in PROFILE_FIELDS
        if name in sent and getattr(body, name) != getattr(guardian, name)
    }
    if changes.get("user_id") is not None:
        await _ensure_account_available(
            session, changes["user_id"], guardian_id=guardian.id
        )
    if changes:
        for name, value in changes.items():
            setattr(guardian, name, value)
        await _flush_profile(session, guardian)
    if changes.get("is_active") is False:
        await _reevaluate_students_of(session, guardian.id)
    return await _detail(session, principal, guardian)


async def _reevaluate_students_of(
    session: AsyncSession, guardian_id: uuid.UUID
) -> None:
    """#35 E4: a deactivated profile's consents stop counting; check the
    login of each linked student (in id order, each locked login → student)."""
    student_ids = await session.scalars(
        select(StudentGuardian.student_id)
        .where(StudentGuardian.guardian_id == guardian_id)
        .order_by(StudentGuardian.student_id)
    )
    for student_id in list(student_ids):
        student = await logins.lock_student_with_login(session, student_id)
        if student is not None:  # pragma: no branch - the FK guarantees it
            await logins.reevaluate_login(
                session, student, cause="guardian_deactivated"
            )


# --- Links -----------------------------------------------------------------------


async def _visible_student(
    session: AsyncSession,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
) -> Student:
    facts = await load_access_facts(session, principal, source)
    return authorize_record(
        await session.get(Student, student_id),
        can_view=lambda record: can_view_student(principal, record.id, facts),
    )


def _guardian_unavailable() -> GuardianUnavailableError:
    return GuardianUnavailableError(
        errors=[
            {
                "path": GUARDIAN_ID_PATH,
                "code": "guardian_unavailable",
                "message": "Choose an active guardian.",
            }
        ]
    )


async def link_guardian(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    body: StudentGuardianCreate,
) -> StudentGuardianItem:
    """Link an active guardian to a student (an inactive student may be
    linked).

    Order (after the body's ``422``): an unknown student (``404``) → an
    unknown or inactive guardian (``422 guardian_unavailable``; the guardian
    is read ``FOR SHARE``, so a concurrent deactivation waits for this
    transaction) → an existing link (``409 guardian_already_linked``; a
    concurrent duplicate hits ``uq_student_guardians_student_guardian`` and
    gets the same code) → the insert.
    """
    student = await _visible_student(session, principal, source, student_id)
    guardian: Guardian | None = await session.scalar(
        select(Guardian)
        .where(Guardian.id == body.guardian_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if guardian is None or not guardian.is_active:
        raise _guardian_unavailable()
    existing = await session.scalar(
        select(StudentGuardian.id).where(
            StudentGuardian.student_id == student.id,
            StudentGuardian.guardian_id == guardian.id,
        )
    )
    if existing is not None:
        raise GuardianAlreadyLinkedError()
    link = StudentGuardian(
        student_id=student.id, guardian_id=guardian.id, relationship=body.relationship
    )
    try:
        async with session.begin_nested():
            session.add(link)
    except IntegrityError as exc:
        if LINK_UNIQUE in str(exc.orig):
            raise GuardianAlreadyLinkedError() from None
        raise
    return _student_guardian_item(guardian, link.relationship)


async def unlink_guardian(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
) -> None:
    """Delete the link. An unknown student, an unknown guardian and no link
    between them are the same ``404 not_found``.

    #35 E3: the guardian's active consent records for the student are revoked
    (cause ``guardian_unlinked``) and the student's login is checked again
    (``logins.reevaluate_login``); the login, then the student row, are
    locked first."""
    facts = await load_access_facts(session, principal, source)
    student = authorize_record(
        await logins.lock_student_with_login(session, student_id),
        can_view=lambda record: can_view_student(principal, record.id, facts),
    )
    link = await session.scalar(
        select(StudentGuardian).where(
            StudentGuardian.student_id == student.id,
            StudentGuardian.guardian_id == guardian_id,
        )
    )
    if link is None:
        raise RecordNotFoundError()
    consent_id = await consent.revoke_on_unlink(
        session, principal=principal, student=student, guardian_id=guardian_id
    )
    await session.delete(link)
    await session.flush()
    await logins.reevaluate_login(
        session, student, cause="guardian_unlinked", consent_id=consent_id
    )


__all__ = [
    "create_guardian",
    "get_guardian",
    "link_guardian",
    "list_guardians",
    "student_guardian_items",
    "unlink_guardian",
    "update_guardian",
]
