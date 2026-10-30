"""Student profiles and emergency contacts (#33; spec §2, §4, §9, §16).

**Reads** (``student.view``): :func:`list_students` and :func:`get_student`.
Who sees which student, and how much of it, is decided by the pure policies
in :mod:`app.modules.families.permissions`; :func:`load_access_facts`
(``facts.py``) loads the facts per request: a tutor's current assignments
from the :class:`~app.modules.families.assignments.TutorAssignmentSource` (a
stub until M4), a guardian's links (#34) and a student user's own profile
(#35), each only for a caller who holds that role. The detail lists the
student's guardians for staff with ``guardian.view`` (#34 E1).

**Guardians** (#34): the guardian profile and link functions live in
``guardians.py`` and are public through this module. A hidden student is ``404
not_found``, the same as a missing one; the list leaves hidden students out
and never answers ``403``/``404`` for them.

**Families** (#172): the family grouping functions live in ``groupings.py``
and are public through this module. The detail shows the student's family to
staff with ``guardian.view``. Family membership is never student access.
:func:`family_of_student` gives billing a student's family (#90 B3; no
access check). For the billing account reads (#364 B5):
:func:`family_record_reference` (the family's id, name, active flag and
current student ids; no access check) and :func:`own_family_id` (the family
of the caller's active guardian profile, the #172 rule). For discounts and
custom prices (#95): :func:`active_student_count` (a family's active
students, the sibling rule) and the unexecuted :func:`family_name_columns`
and :func:`student_name_columns` (names joined by billing reads). For due
dates and reminders (#97): the unexecuted :func:`family_status_columns`
(the overdue accounts list) and :func:`family_billing_recipients` (the
logins of a family's active guardian profiles, the reminder recipients).

**Name search** (ISSUE-D6, accepted on #33): with ``n(x) =
public.f_unaccent(lower(x))`` and ``name = given_names || ' ' ||
family_names``, a student matches ``q`` when every whitespace-separated term
of ``q`` (at most five) is a substring of ``n(name)`` (``%``, ``_`` and ``\\``
match literally, so terms match in any order), **or** ``n(q) <% n(name)``
(``pg_trgm`` word similarity at its default threshold, which tolerates
typos). Results are ordered by ``word_similarity(n(q), n(name))``, then the
default order: ``n(family_names)``, ``n(given_names)``, ``id``. Both
predicates use the GIN trigram index on ``n(name)``.

**Writes** (``student.manage``): :func:`create_student` and
:func:`update_student`. Profiles are never deleted, only deactivated
(``is_active``). An update sends the ``version`` the caller loaded; the row
is locked ``FOR UPDATE`` before the version check, so of two changes from
one version the second gets ``409 version_conflict``. Emergency contacts are
replaced as a whole list (1-5) whose order sets ``position``; any change,
a contacts-only one included, increments ``students.version`` exactly once.
A body that changes nothing changes no version and writes no event.

**Education level:** only an active level can be picked
(``catalog.service.get_level_for_reference`` with a ``FOR SHARE`` lock, so a
concurrent deactivation can't slip in); an already assigned inactive level
may be kept. ``422 education_level_unavailable`` otherwise.

**Audit:** automatic only (#22): ``create``/``update``/``delete`` events of
``students`` and ``student_emergency_contacts`` with the actor and request
ID. No named event and no ``details`` key. A contacts-only change writes only
contact events (the version bump changes no audited column).

**References** (#48): :func:`student_references` gives other modules a
student's id, display name and active flag, without any access check (the
caller's own permission decides, e.g. ``schedule.view``).

**Levels of referenced students** (#91): :func:`student_level_references`
is :func:`student_references` with each student's current
``education_level_id`` (a session charge's price and snapshot), and
:func:`families_of_students` the families of several students. No access
check.

**Ages of referenced students** (#59 S1): :func:`student_age_references`
is :func:`student_references` with each student's ``birthdate``, so another
module's record policy can apply the adult rule (``is_minor`` with the
center's today and :func:`guardian_has_academic_access`, re-exported from
``permissions``) in the same statement. No access check.

**Notification recipients** (#232 C2): :func:`student_notification_recipients`
gives other modules, per student, the login user of the student and those
of its linked guardians (no access check; the caller decides what the
family may be told).

**Academic notification recipients** (#79 B3):
:func:`academic_notification_recipients` gives the users a family
notification about the student's **academic records** may go to: the
student's own login and the accounts of the guardians whose **active**
profile is linked to the student and who have academic access (a minor on
``today``, or an adult who gave **that** guardian
``guardian_academic_access``). No other access check.

**Ages:** :func:`is_minor` (re-exported from ``ages``) with
``administration.service.center_today(session)`` (the center's time zone
setting, #37); #35 uses both.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Select,
    exists,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, authorize_record
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import EducationLevelReference
from app.modules.families import consent as _consent
from app.modules.families import groupings as _groupings
from app.modules.families import guardians as _guardians
from app.modules.families import logins, search
from app.modules.families.ages import is_minor
from app.modules.families.assignments import (
    TutorAssignmentSource,
    register_tutor_assignment_source,
)
from app.modules.families.errors import (
    BirthdateInFutureError,
    EducationLevelUnavailableError,
    EmergencyContactUnknownError,
)
from app.modules.families.facts import (
    FamilyScheduleFacts,
    family_schedule_facts,
    load_access_facts,
    own_student_id,
)
from app.modules.families.models import (
    ConsentRecord,
    Guardian,
    Student,
    StudentEmergencyContact,
    StudentGuardian,
)
from app.modules.families.permissions import (
    FAMILY_MANAGE,
    GUARDIAN_ACADEMIC_ACCESS,
    AccessLevel,
    StudentAccessFacts,
    can_view_student,
    guardian_has_academic_access,
    sees_every_student,
    sees_student_guardians,
    student_access_level,
    visible_student_ids,
)
from app.modules.families.schemas import (
    EmergencyContact,
    FamilyReference,
    StudentAccountUpdate,
    StudentCreate,
    StudentDetail,
    StudentGuardianItem,
    StudentListItem,
    StudentLogin,
    StudentPersonal,
    StudentReference,
    StudentStatus,
    StudentUpdate,
)
from app.modules.identity import service as identity

__all__ = [
    "STUDENT_LOGIN_GATE",
    "FamilyRecordReference",
    "FamilyScheduleFacts",
    "StudentAccessFacts",
    "StudentAgeReference",
    "StudentLevelReference",
    "academic_notification_recipients",
    "active_student_count",
    "add_family_guardian",
    "add_family_student",
    "create_family",
    "create_guardian",
    "create_student",
    "families_of_students",
    "family_of_student",
    "family_billing_recipients",
    "family_name_columns",
    "family_status_columns",
    "family_record_reference",
    "family_schedule_facts",
    "get_consents",
    "get_family",
    "get_guardian",
    "get_student",
    "guardian_has_academic_access",
    "is_minor",
    "link_guardian",
    "list_families",
    "list_guardians",
    "list_students",
    "load_access_facts",
    "own_family_id",
    "own_student_id",
    "record_consent",
    "register_tutor_assignment_source",
    "remove_family_guardian",
    "remove_family_student",
    "revoke_consent",
    "set_student_account",
    "student_age_references",
    "student_name_columns",
    "student_level_references",
    "student_notification_recipients",
    "student_references",
    "unlink_guardian",
    "update_family",
    "update_guardian",
    "update_student",
]

# Guardian profiles and links (#34), public through this module (ADR-0002).
create_guardian = _guardians.create_guardian
get_guardian = _guardians.get_guardian
link_guardian = _guardians.link_guardian
list_guardians = _guardians.list_guardians
unlink_guardian = _guardians.unlink_guardian
update_guardian = _guardians.update_guardian

# Consent records and the student-login gate (#35), public through this module.
get_consents = _consent.get_consents
record_consent = _consent.record_consent
revoke_consent = _consent.revoke_consent
STUDENT_LOGIN_GATE = logins.STUDENT_LOGIN_GATE
"""The ``identity.service.StudentLoginGate`` the composition roots register
(the API app factory, ``app.cli`` and ``app.worker``)."""

# Family groupings (#172), public through this module (ADR-0002; M6-05 builds
# the billing account on ``families``).
add_family_guardian = _groupings.add_guardian
add_family_student = _groupings.add_student
create_family = _groupings.create_family
get_family = _groupings.get_family
list_families = _groupings.list_families
remove_family_guardian = _groupings.remove_guardian
remove_family_student = _groupings.remove_student
update_family = _groupings.update_family
family_of_student = _groupings.family_of_student
"""The student's family, or ``None`` (one statement; no access check).
``billing.service.post_charge`` resolves the billing account with it
(#90 B3)."""
families_of_students = _groupings.families_of_students
"""The families of several students, by student id (one statement; no
access check; #91 D4, the unbilled list)."""
FamilyRecordReference = _groupings.FamilyRecordReference
family_record_reference = _groupings.family_record_reference
own_family_id = _groupings.own_family_id
active_student_count = _groupings.active_student_count
"""A family's number of active students (one statement; no access check;
#95 H3)."""
family_name_columns = _groupings.family_name_columns
family_status_columns = _groupings.family_status_columns
family_billing_recipients = _groupings.family_billing_recipients
"""The logins of each family's active guardian profiles (#97 N3: the
reminder recipients; one statement; no access check)."""

# --- References (#48) -------------------------------------------------------------


async def student_references(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, StudentReference]:
    """The students with these ids, active or not (unknown ids are left
    out). One statement (none for an empty collection)."""
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            Student.id, Student.given_names, Student.family_names, Student.is_active
        ).where(Student.id.in_(wanted))
    )
    return {
        row.id: StudentReference(
            id=row.id,
            display_name=f"{row.given_names} {row.family_names}",
            is_active=row.is_active,
        )
        for row in rows
    }


def student_name_columns() -> Select[uuid.UUID, str]:
    """``SELECT id, name FROM students`` (``name`` = given names and family
    names), **not executed** (#95: the billing reads join it as a subquery
    for names and order)."""
    return select(
        Student.id, (Student.given_names + " " + Student.family_names).label("name")
    )


@dataclass(frozen=True)
class StudentLevelReference:
    """A referenced student with the current education level id (#91: the
    level a session charge snapshots and prices with; ``None`` when the
    student has none)."""

    id: uuid.UUID
    display_name: str
    education_level_id: uuid.UUID | None


async def student_level_references(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, StudentLevelReference]:
    """The students with these ids with their current level id (unknown ids
    are left out). One statement (none for an empty collection); no access
    check."""
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            Student.id,
            Student.given_names,
            Student.family_names,
            Student.education_level_id,
        ).where(Student.id.in_(wanted))
    )
    return {
        row.id: StudentLevelReference(
            id=row.id,
            display_name=f"{row.given_names} {row.family_names}",
            education_level_id=row.education_level_id,
        )
        for row in rows
    }


@dataclass(frozen=True)
class StudentAgeReference:
    """A referenced student with the birthdate (#59 S1; never sent to a
    client by this module)."""

    id: uuid.UUID
    display_name: str
    is_active: bool
    birthdate: date


async def student_age_references(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, StudentAgeReference]:
    """:func:`student_references` with each student's birthdate (unknown ids
    are left out). One statement (none for an empty collection)."""
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            Student.id,
            Student.given_names,
            Student.family_names,
            Student.is_active,
            Student.birthdate,
        ).where(Student.id.in_(wanted))
    )
    return {
        row.id: StudentAgeReference(
            id=row.id,
            display_name=f"{row.given_names} {row.family_names}",
            is_active=row.is_active,
            birthdate=row.birthdate,
        )
        for row in rows
    }


async def student_notification_recipients(
    session: AsyncSession, student_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Per student id, the users a family notification about the student
    goes to: the student's own login (``students.user_id``) and the
    accounts of the guardians linked to the student (#34), each once, by
    id. Students without any are left out. Two statements (none for an
    empty collection), whatever the number of students."""
    wanted = set(student_ids)
    if not wanted:
        return {}
    found: dict[uuid.UUID, set[uuid.UUID]] = {}
    own = await session.execute(
        select(Student.id, Student.user_id).where(
            Student.id.in_(wanted), Student.user_id.is_not(None)
        )
    )
    for student_id, user_id in own:
        if user_id is not None:
            found.setdefault(student_id, set()).add(user_id)
    guardians = await session.execute(
        select(StudentGuardian.student_id, Guardian.user_id)
        .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
        .where(StudentGuardian.student_id.in_(wanted), Guardian.user_id.is_not(None))
    )
    for student_id, user_id in guardians:
        if user_id is not None:
            found.setdefault(student_id, set()).add(user_id)
    return {student_id: sorted(users) for student_id, users in found.items()}


async def academic_notification_recipients(
    session: AsyncSession, student_id: uuid.UUID, *, today: date
) -> list[uuid.UUID]:
    """#79 B3: the users told about the student's academic records: the
    student's own login (if any) and the accounts of guardians whose
    profile is active and linked to the student, when the student is a
    minor on ``today`` (the center's date) or gave that guardian an active
    ``guardian_academic_access`` consent. Each user once, sorted. Two
    statements (one for an unknown student)."""
    student = (
        await session.execute(
            select(Student.user_id, Student.birthdate).where(Student.id == student_id)
        )
    ).first()
    if student is None:
        return []
    found: set[uuid.UUID] = set()
    if student.user_id is not None:
        found.add(student.user_id)
    minor = is_minor(student.birthdate, today)
    consented = (
        exists()
        .where(
            ConsentRecord.student_id == StudentGuardian.student_id,
            ConsentRecord.guardian_id == StudentGuardian.guardian_id,
            ConsentRecord.scope == GUARDIAN_ACADEMIC_ACCESS,
            ConsentRecord.revoked_at.is_(None),
        )
        .correlate(StudentGuardian)
    )
    guardians = await session.execute(
        select(Guardian.user_id, consented)
        .join(StudentGuardian, StudentGuardian.guardian_id == Guardian.id)
        .where(
            StudentGuardian.student_id == student_id,
            Guardian.is_active.is_(True),
            Guardian.user_id.is_not(None),
        )
    )
    for user_id, has_consent in guardians:
        if user_id is not None and (minor or has_consent):
            found.add(user_id)
    return sorted(found)


# --- Search expressions ----------------------------------------------------------


def search_condition(q: str) -> ColumnElement[bool]:
    """The E2 predicate of ``q`` on students (``search.name_search_condition``)."""
    return search.name_search_condition(q, Student.given_names, Student.family_names)


def _relevance(q: str) -> ColumnElement[float]:
    return search.relevance(q, Student.given_names, Student.family_names)


DEFAULT_ORDER: Final = search.default_order(
    Student.given_names, Student.family_names, Student.id
)


# --- Projections -----------------------------------------------------------------


def _status_conditions(status: StudentStatus) -> list[ColumnElement[bool]]:
    if status == "active":
        return [Student.is_active.is_(True)]
    if status == "inactive":
        return [Student.is_active.is_(False)]
    return []


def _list_item(
    student: Student,
    levels: dict[uuid.UUID, EducationLevelReference],
    today: date,
) -> StudentListItem:
    level_id = student.education_level_id
    return StudentListItem(
        id=student.id,
        given_names=student.given_names,
        family_names=student.family_names,
        is_active=student.is_active,
        is_minor=is_minor(student.birthdate, today),
        school_grade=student.school_grade,
        education_level=levels.get(level_id) if level_id is not None else None,
    )


async def _contacts_of(
    session: AsyncSession, student_id: uuid.UUID
) -> list[StudentEmergencyContact]:
    return list(
        await session.scalars(
            select(StudentEmergencyContact)
            .where(StudentEmergencyContact.student_id == student_id)
            .order_by(StudentEmergencyContact.position)
        )
    )


def _contact(row: StudentEmergencyContact) -> EmergencyContact:
    return EmergencyContact(
        id=row.id,
        name=row.name,
        relationship=row.relationship,
        phone=row.phone,
        position=row.position,
    )


async def _level_of(
    session: AsyncSession, student: Student
) -> EducationLevelReference | None:
    if student.education_level_id is None:
        return None
    return await catalog.get_level_for_reference(
        session, student.education_level_id, lock=False
    )


def _detail(
    student: Student,
    *,
    level: EducationLevelReference | None,
    access: AccessLevel,
    contacts: Iterable[StudentEmergencyContact],
    guardians: list[StudentGuardianItem] | None,
    family: FamilyReference | None = None,
    login: StudentLogin | None = None,
    today: date,
) -> StudentDetail:
    personal = None
    if access == "full":
        personal = StudentPersonal(
            birthdate=student.birthdate,
            email=student.email,
            phone=student.phone,
            emergency_contacts=[
                _contact(row) for row in sorted(contacts, key=lambda c: c.position)
            ],
        )
    return StudentDetail(
        id=student.id,
        given_names=student.given_names,
        family_names=student.family_names,
        is_active=student.is_active,
        is_minor=is_minor(student.birthdate, today),
        school_name=student.school_name,
        school_grade=student.school_grade,
        education_level=level,
        personal=personal,
        guardians=guardians,
        family=family,
        login=login,
        version=student.version,
        created_at=student.created_at,
        updated_at=student.updated_at,
    )


async def _detail_for(
    session: AsyncSession,
    student: Student,
    *,
    principal: Principal,
    access: AccessLevel,
    level: EducationLevelReference | None = None,
    contacts: Sequence[StudentEmergencyContact] | None = None,
) -> StudentDetail:
    if level is None:
        level = await _level_of(session, student)
    if access == "full" and contacts is None:
        contacts = await _contacts_of(session, student.id)
    guardians = None
    family = None
    if sees_student_guardians(principal):
        guardians = await _guardians.student_guardian_items(session, student.id)
        family = await _groupings.family_of_student(session, student.id)
    return _detail(
        student,
        level=level,
        access=access,
        contacts=contacts or (),
        guardians=guardians,
        family=family,
        login=await _login_of(session, principal, student),
        today=await administration.center_today(session),
    )


async def _login_of(
    session: AsyncSession, principal: Principal, student: Student
) -> StudentLogin | None:
    """The linked login, only for ``family.manage`` holders (#35 C3)."""
    if student.user_id is None or not principal.has(FAMILY_MANAGE):
        return None
    reference = await identity.get_account_reference(session, student.user_id)
    if reference is None:  # pragma: no cover - the FK guarantees it
        return None
    return StudentLogin(
        user_id=reference.id,
        username=reference.username,
        display_name=reference.display_name,
        state=reference.state,
    )


def _access(
    principal: Principal, student: Student, facts: StudentAccessFacts, today: date
) -> AccessLevel | None:
    return student_access_level(
        principal,
        student.id,
        facts,
        is_minor=is_minor(student.birthdate, today),
    )


# --- Reads -----------------------------------------------------------------------


async def list_students(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    q: str | None,
    status: StudentStatus,
    education_level_id: uuid.UUID | None,
    page: OffsetParams,
) -> OffsetPage[StudentListItem]:
    """The students the caller may see that match every filter.

    At most four statements for a caller who sees every student (the page,
    the total, the page's level names through ``catalog.service``, ADR-0002
    forbids a cross-module join, and the center time zone for the ages, #37,
    once per Unit of Work). Any other caller sees only
    ``assigned ∪ linked ∪ own``; when that is empty, the page is empty and no
    statement is sent.
    """
    conditions = _status_conditions(status)
    if not sees_every_student(principal):
        facts = await load_access_facts(session, principal, source)
        visible = visible_student_ids(principal, facts)
        if not visible:
            return OffsetPage[StudentListItem](
                items=[], total=0, limit=page.limit, offset=page.offset
            )
        conditions.append(Student.id.in_(visible))
    if education_level_id is not None:
        conditions.append(Student.education_level_id == education_level_id)
    order: list[Any] = list(DEFAULT_ORDER)
    if q:
        conditions.append(search_condition(q))
        order.insert(0, _relevance(q).desc())
    students = list(
        await session.scalars(
            select(Student)
            .where(*conditions)
            .order_by(*order)
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    total = await session.scalar(select(func.count(Student.id)).where(*conditions))
    levels = await catalog.education_level_references(
        session,
        {s.education_level_id for s in students if s.education_level_id is not None},
    )
    today = await administration.center_today(session)
    return OffsetPage[StudentListItem](
        items=[_list_item(student, levels, today) for student in students],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_student(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
) -> StudentDetail:
    """One student: ``404 not_found`` when missing or hidden; ``personal``
    only for a full-access viewer."""
    facts = await load_access_facts(session, principal, source)
    student = authorize_record(
        await session.get(Student, student_id),
        can_view=lambda record: can_view_student(principal, record.id, facts),
    )
    access = _access(
        principal, student, facts, await administration.center_today(session)
    )
    if access is None:  # pragma: no cover - authorize_record already checked
        raise AssertionError("visible student without an access level")
    return await _detail_for(session, student, principal=principal, access=access)


# --- Writes ----------------------------------------------------------------------

LEVEL_PATH: Final[list[str | int]] = ["body", "education_level_id"]


async def _available_level(
    session: AsyncSession, level_id: uuid.UUID
) -> EducationLevelReference:
    """An active level, locked ``FOR SHARE``; else ``422
    education_level_unavailable``."""
    level = await catalog.get_level_for_reference(session, level_id, lock=True)
    if level is None or not level.is_active:
        raise EducationLevelUnavailableError(
            errors=[
                {
                    "path": LEVEL_PATH,
                    "code": "education_level_unavailable",
                    "message": "Choose an active education level.",
                }
            ]
        )
    return level


def _ensure_not_in_future(birthdate: date, today: date) -> None:
    if birthdate > today:
        raise BirthdateInFutureError()


async def create_student(
    session: AsyncSession, *, principal: Principal, body: StudentCreate
) -> StudentDetail:
    """A new active profile with its contacts in the given order.

    After the body's ``422``: a future birthdate (``422 validation_error``)
    → an unknown or inactive level (``422 education_level_unavailable``) →
    the inserts.
    """
    today = await administration.center_today(session)
    _ensure_not_in_future(body.birthdate, today)
    level = None
    if body.education_level_id is not None:
        level = await _available_level(session, body.education_level_id)
    student = Student(
        given_names=body.given_names,
        family_names=body.family_names,
        birthdate=body.birthdate,
        email=body.email,
        phone=body.phone,
        school_name=body.school_name,
        school_grade=body.school_grade,
        education_level_id=body.education_level_id,
        is_active=True,
    )
    session.add(student)
    await session.flush()
    contacts = [
        StudentEmergencyContact(
            student_id=student.id,
            name=item.name,
            relationship=item.relationship,
            phone=item.phone,
            position=position,
        )
        for position, item in enumerate(body.emergency_contacts, start=1)
    ]
    session.add_all(contacts)
    await session.flush()
    access = _access(principal, student, StudentAccessFacts(), today)
    return _detail(
        student,
        level=level,
        access=access or "limited",
        contacts=contacts,
        guardians=[] if sees_student_guardians(principal) else None,
        today=today,
    )


PROFILE_FIELDS: Final = (
    "given_names",
    "family_names",
    "birthdate",
    "email",
    "phone",
    "school_name",
    "school_grade",
    "is_active",
)
CONTACT_FIELDS: Final = ("name", "relationship", "phone")


@dataclass
class _ContactPlan:
    """The changes a whole-list replace makes to a student's contacts."""

    updates: list[tuple[StudentEmergencyContact, dict[str, Any]]]
    inserts: list[StudentEmergencyContact]
    deletes: list[StudentEmergencyContact]

    @property
    def changes_anything(self) -> bool:
        return bool(self.updates or self.inserts or self.deletes)


def _unknown_contact(index: int) -> FieldError:
    return {
        "path": ["body", "emergency_contacts", index, "id"],
        "code": "emergency_contact_unknown",
        "message": "Not one of this student's emergency contacts.",
    }


def _plan_contacts(
    student: Student,
    current: Sequence[StudentEmergencyContact],
    body: StudentUpdate,
) -> _ContactPlan:
    """Compare the sent list with the current contacts (``422
    emergency_contact_unknown`` for an id that isn't this student's or is
    sent twice; nothing has changed yet)."""
    items = body.emergency_contacts or []
    by_id = {row.id: row for row in current}
    seen: set[uuid.UUID] = set()
    errors = []
    for index, item in enumerate(items):
        if item.id is None:
            continue
        if item.id not in by_id or item.id in seen:
            errors.append(_unknown_contact(index))
        seen.add(item.id)
    if errors:
        raise EmergencyContactUnknownError(errors=errors)
    plan = _ContactPlan(updates=[], inserts=[], deletes=[])
    for position, item in enumerate(items, start=1):
        wanted = {
            "name": item.name,
            "relationship": item.relationship,
            "phone": item.phone,
            "position": position,
        }
        if item.id is None:
            plan.inserts.append(
                StudentEmergencyContact(student_id=student.id, **wanted)
            )
            continue
        row = by_id[item.id]
        changed = {
            key: value for key, value in wanted.items() if getattr(row, key) != value
        }
        if changed:
            plan.updates.append((row, changed))
    plan.deletes = [row for row in current if row.id not in seen]
    return plan


async def _student_for_update(
    session: AsyncSession, student_id: uuid.UUID
) -> Student | None:
    student: Student | None = await session.scalar(
        select(Student)
        .where(Student.id == student_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return student


async def update_student(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    body: StudentUpdate,
) -> StudentDetail:
    """Change the sent fields and, when sent, replace the contact list.

    Order (after the body's ``422``): ``404`` (missing or hidden) → ``409
    version_conflict`` → a future birthdate (``422 validation_error``) → a
    birthdate that makes an active student login a minor's without a valid
    consent (``409 guardian_consent_required``, #35 E5) →
    ``422 emergency_contact_unknown`` → ``422 education_level_unavailable``
    (only when the level changes; keeping an inactive one is allowed) → the
    update. The student row is locked ``FOR UPDATE`` first.
    """
    facts = await load_access_facts(session, principal, source)
    student = authorize_record(
        await _student_for_update(session, student_id),
        can_view=lambda record: can_view_student(principal, record.id, facts),
    )
    ensure_version(student, body.version)
    today = await administration.center_today(session)
    sent = body.model_fields_set
    changes: dict[str, Any] = {
        name: getattr(body, name)
        for name in PROFILE_FIELDS
        if name in sent and getattr(body, name) != getattr(student, name)
    }
    if "birthdate" in changes:
        _ensure_not_in_future(changes["birthdate"], today)
        # #35 E5: never turn an active student login into a minor's login
        # without a guardian's consent.
        await logins.ensure_birthdate_allowed(session, student, changes["birthdate"])
    current = await _contacts_of(session, student.id)
    plan = _ContactPlan(updates=[], inserts=[], deletes=[])
    if "emergency_contacts" in sent:
        plan = _plan_contacts(student, current, body)
    level: EducationLevelReference | None = None
    if (
        "education_level_id" in sent
        and body.education_level_id != student.education_level_id
    ):
        if body.education_level_id is not None:
            level = await _available_level(session, body.education_level_id)
        changes["education_level_id"] = body.education_level_id
    access = _access(principal, student, facts, today) or "limited"
    if not changes and not plan.changes_anything:
        return await _detail_for(
            session, student, principal=principal, access=access, contacts=current
        )

    # One versioned UPDATE of the student row: the changed columns, or, for
    # a contacts-only change, only updated_at (left out of the audit, so no
    # students event is written).
    if changes:
        for name, value in changes.items():
            setattr(student, name, value)
    else:
        student.updated_at = func.now()
    await session.flush()
    await session.refresh(student, ["updated_at"])

    for row, changed in plan.updates:
        for name, value in changed.items():
            setattr(row, name, value)
    session.add_all(plan.inserts)
    for row in plan.deletes:
        await session.delete(row)
    await session.flush()
    deleted = {row.id for row in plan.deletes}
    contacts = [row for row in current if row.id not in deleted] + plan.inserts
    return await _detail_for(
        session,
        student,
        principal=principal,
        access=access,
        level=level,
        contacts=contacts,
    )


async def set_student_account(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    body: StudentAccountUpdate,
) -> StudentDetail:
    """Link a login account to the profile, or unlink it (#35 C).

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``422 student_account_unavailable`` → ``409 student_account_in_use`` →
    the gate for this student when the account is active and holds
    ``student`` (``409 guardian_consent_required``) → the update (a
    ``students`` ``update`` event of ``user_id``). The row is locked ``FOR
    UPDATE`` first.
    """
    facts = await load_access_facts(session, principal, source)
    student = authorize_record(
        await _student_for_update(session, student_id),
        can_view=lambda record: can_view_student(principal, record.id, facts),
    )
    await logins.link_account(
        session, student, user_id=body.user_id, version=body.version
    )
    access = (
        _access(principal, student, facts, await administration.center_today(session))
        or "limited"
    )
    return await _detail_for(session, student, principal=principal, access=access)
