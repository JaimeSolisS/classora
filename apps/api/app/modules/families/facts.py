"""Loading the facts the record policies need (#33 F3, #34 F2).

Facts are loaded **on every request**, with no cache of any kind, and only
for a caller holding the matching role, so a change (an unlink, a deactivated
guardian profile, an account moved to another profile, a removed ``guardian``
role) applies on the caller's next request without a new login:

- ``tutor``: the current assignments from the
  :class:`~app.modules.families.assignments.TutorAssignmentSource`;
- ``guardian``: the students linked to the caller's **active** guardian
  profile (``linked_student_ids``, one statement) and, for the guardian
  routes, that profile's id (``own_guardian_id``, one statement);
- ``guardian`` on the family routes (#172): the family of the caller's
  active profile (``own_family_id``, one statement);
- ``student``: ``own_student_id`` stays ``None`` until #35 links a login to
  its profile.

A staff-only caller never queries links.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.families.assignments import TutorAssignmentSource
from app.modules.families.models import FamilyGuardian, Guardian, StudentGuardian
from app.modules.families.permissions import (
    GUARDIAN,
    TUTOR,
    FamilyAccessFacts,
    GuardianAccessFacts,
    StudentAccessFacts,
)


async def linked_student_ids(
    session: AsyncSession, user_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """The students linked to the active guardian profile of ``user_id``'s
    account (empty without one), in one statement."""
    rows = await session.scalars(
        select(StudentGuardian.student_id)
        .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
        .where(Guardian.user_id == user_id, Guardian.is_active.is_(True))
    )
    return frozenset(rows)


async def load_access_facts(
    session: AsyncSession, principal: Principal, source: TutorAssignmentSource
) -> StudentAccessFacts:
    """What the caller is linked to, loaded only for the roles they hold."""
    assigned: frozenset[uuid.UUID] = frozenset()
    linked: frozenset[uuid.UUID] = frozenset()
    if TUTOR in principal.roles:
        assigned = await source.student_ids_for_tutor(session, principal.user_id)
    if GUARDIAN in principal.roles:
        linked = await linked_student_ids(session, principal.user_id)
    return StudentAccessFacts(assigned_student_ids=assigned, linked_student_ids=linked)


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
