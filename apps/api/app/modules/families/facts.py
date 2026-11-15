"""Loading the facts the record policies need (#33 F3, #34 F2).

Facts are loaded **on every request**, with no cache of any kind, and only
for a caller holding the matching role, so a change (an unlink, a deactivated
guardian profile, an account moved to another profile, a removed ``guardian``
role) applies on the caller's next request without a new login:

- ``tutor``: the current assignments from the
  :class:`~app.modules.families.assignments.TutorAssignmentSource`;
- ``guardian``: the students linked to the caller's **active** guardian
  profile (``linked_student_ids``) and, in the **same** statement, those of
  them with an active ``guardian_academic_access`` consent for that profile
  (``academic_consent_student_ids``, #35 E4 (a)); for the guardian routes,
  that profile's id (``own_guardian_id``, one statement);
- ``guardian`` on the family routes (#172): the family of the caller's
  active profile (``own_family_id``, one statement);
- ``student``: the profile linked to the caller's login (``own_student_id``,
  #35; one statement, whatever the profile's ``is_active``: suspending the
  login is the access control);
- the group and cohort reads (#66 G1, #246 A1): :func:`family_schedule_facts`,
  the own profile and the active profile's linked students with their
  birthdates and consents, in one statement.

A staff-only caller never queries links.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, false, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.families.assignments import TutorAssignmentSource
from app.modules.families.models import (
    ConsentRecord,
    FamilyGuardian,
    Guardian,
    Student,
    StudentGuardian,
)
from app.modules.families.permissions import (
    GUARDIAN,
    GUARDIAN_ACADEMIC_ACCESS,
    STUDENT,
    TUTOR,
    FamilyAccessFacts,
    GuardianAccessFacts,
    StudentAccessFacts,
)


async def guardian_student_facts(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID]]:
    """``(linked, academic_consent)`` for ``user_id``'s active guardian
    profile, in one statement: the linked students, and those of them with an
    active ``guardian_academic_access`` consent naming that profile."""
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
    rows = await session.execute(
        select(StudentGuardian.student_id, consented)
        .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
        .where(Guardian.user_id == user_id, Guardian.is_active.is_(True))
    )
    linked: set[uuid.UUID] = set()
    academic: set[uuid.UUID] = set()
    for student_id, has_consent in rows:
        linked.add(student_id)
        if has_consent:
            academic.add(student_id)
    return frozenset(linked), frozenset(academic)


async def own_student_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """The student profile linked to ``user_id``'s login, or ``None`` (one
    statement)."""
    found: uuid.UUID | None = await session.scalar(
        select(Student.id).where(Student.user_id == user_id)
    )
    return found


async def load_access_facts(
    session: AsyncSession, principal: Principal, source: TutorAssignmentSource
) -> StudentAccessFacts:
    """What the caller is linked to, loaded only for the roles they hold."""
    assigned: frozenset[uuid.UUID] = frozenset()
    linked: frozenset[uuid.UUID] = frozenset()
    academic: frozenset[uuid.UUID] = frozenset()
    own: uuid.UUID | None = None
    if TUTOR in principal.roles:
        assigned = await source.student_ids_for_tutor(session, principal.user_id)
    if GUARDIAN in principal.roles:
        linked, academic = await guardian_student_facts(session, principal.user_id)
    if STUDENT in principal.roles:
        own = await own_student_id(session, principal.user_id)
    return StudentAccessFacts(
        assigned_student_ids=assigned,
        linked_student_ids=linked,
        own_student_id=own,
        academic_consent_student_ids=academic,
    )


async def load_guardian_facts(
    session: AsyncSession, principal: Principal
) -> GuardianAccessFacts:
    """The caller's own active guardian profile, only for a ``guardian``."""
    if GUARDIAN not in principal.roles:
        return GuardianAccessFacts()
    own: uuid.UUID | None = await session.scalar(
        select(Guardian.id).where(
            Guardian.user_id == principal.user_id, Guardian.is_active.is_(True)
        )
    )
    return GuardianAccessFacts(own_guardian_id=own)


async def load_family_facts(
    session: AsyncSession, principal: Principal
) -> FamilyAccessFacts:
    """The family of the caller's active guardian profile, only for a
    ``guardian`` (one statement). Membership never adds to
    ``linked_student_ids``: it is not student access."""
    if GUARDIAN not in principal.roles:
        return FamilyAccessFacts()
    own: uuid.UUID | None = await session.scalar(
        select(FamilyGuardian.family_id)
        .join(Guardian, Guardian.id == FamilyGuardian.guardian_id)
        .where(Guardian.user_id == principal.user_id, Guardian.is_active.is_(True))
    )
    return FamilyAccessFacts(own_family_id=own)


@dataclass(frozen=True)
class FamilyScheduleFacts:
    """The schedule-shaped record facts of a family caller (#246 A1): who
    they are linked to (:class:`StudentAccessFacts` with ``own_student_id``,
    ``linked_student_ids`` and ``academic_consent_student_ids``) and the
    birthdates of their linked students (for the adult rule)."""

    facts: StudentAccessFacts
    birthdates: dict[uuid.UUID, date]


async def family_schedule_facts(
    session: AsyncSession, principal: Principal
) -> FamilyScheduleFacts:
    """The facts of the group and cohort reads (#66 G1, #246 A1): the
    caller's own profile (``student`` role) and the students linked to
    their **active** guardian profile (``guardian`` role) with each one's
    birthdate and whether it gave that profile a ``guardian_academic_access``
    consent. Loaded on every request like the other facts, in **one**
    statement whatever the roles; none without either role."""
    if not ({STUDENT, GUARDIAN} & principal.roles):
        return FamilyScheduleFacts(facts=StudentAccessFacts(), birthdates={})
    parts = []
    if STUDENT in principal.roles:
        parts.append(
            select(
                Student.id.label("student_id"),
                true().label("own"),
                Student.birthdate.label("birthdate"),
                false().label("consented"),
            ).where(Student.user_id == principal.user_id)
        )
    if GUARDIAN in principal.roles:
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
        parts.append(
            select(
                StudentGuardian.student_id.label("student_id"),
                false().label("own"),
                Student.birthdate.label("birthdate"),
                consented.label("consented"),
            )
            .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
            .join(Student, Student.id == StudentGuardian.student_id)
            .where(Guardian.user_id == principal.user_id, Guardian.is_active.is_(True))
        )
    query = parts[0] if len(parts) == 1 else union_all(*parts)
    own: uuid.UUID | None = None
    linked: set[uuid.UUID] = set()
    academic: set[uuid.UUID] = set()
    birthdates: dict[uuid.UUID, date] = {}
    for student_id, is_own, birthdate, has_consent in await session.execute(query):
        if is_own:
            own = student_id
            continue
        linked.add(student_id)
        birthdates[student_id] = birthdate
        if has_consent:
            academic.add(student_id)
    return FamilyScheduleFacts(
        facts=StudentAccessFacts(
            linked_student_ids=frozenset(linked),
            own_student_id=own,
            academic_consent_student_ids=frozenset(academic),
        ),
        birthdates=birthdates,
    )
