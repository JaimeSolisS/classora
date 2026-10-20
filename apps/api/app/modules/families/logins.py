"""Student logins: the consent gate, account links and loss of the consent
basis (#35 B, C, E; ADR-0007 D17, D18).

**The gate** (:class:`FamiliesStudentLoginGate`, registered with
``identity.service.register_student_login_gate`` by the composition roots):
an account holding ``student`` may be ``active`` only when

1. a student profile is linked to it (``students.user_id``), else ``409
   student_profile_required``;
2. that student is an adult today in the center's time zone, or
3. the minor has at least one **valid** ``student_login`` consent: not
   revoked, from a guardian still linked to the student and whose profile is
   active (owner decision #35 E3 (a));

otherwise ``409 guardian_consent_required``. ``identity`` runs it inside
``change_user_state(→ active)`` and ``add_user_role("student")``, so #29
redemption, #169 approval, #31 reactivation and #170 role changes all pass
it; the caller's Unit of Work rolls back on a refusal.

**Lock order** (the #170 lesson): the last-active-Owner advisory lock (only
when the login holds ``owner``), then the ``users`` row, then the
``students`` row. ``identity`` locks the user before calling the gate, and
the gate locks the student row ``FOR UPDATE``; :func:`lock_student_with_login`
takes the same locks in the same order for the paths that may suspend a
login (revocation, unlinking, guardian deactivation). A gate check and a
loss of basis on the same student therefore serialize: the end state is
never "an active minor login without a valid consent".

**Loss of basis** (:func:`reevaluate_login`, E1): after a revocation, an
unlink or a guardian deactivation, a minor's ``active`` student login with
no valid consent left is suspended through
``identity.service.change_user_state(active → suspended)`` (its sessions
end, #25) and ``families.student_login_suspended`` is written.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.families.ages import is_minor
from app.modules.families.errors import (
    GuardianConsentRequiredError,
    StudentAccountInUseError,
    StudentAccountUnavailableError,
    StudentProfileRequiredError,
)
from app.modules.families.models import (
    STUDENT_USER_UNIQUE,
    ConsentRecord,
    Guardian,
    Student,
    StudentGuardian,
)
from app.modules.families.permissions import OWNER, STUDENT, STUDENT_LOGIN
from app.modules.identity import service as identity

ACTIVE: Final = "active"
SUSPENDED: Final = "suspended"
ARCHIVED: Final = "archived"

STUDENT_LOGIN_SUSPENDED_EVENT: Final = "families.student_login_suspended"
"""The explicit audit event of a suspension by loss of consent (#35 G1):
``record_type="users"``, ``record_id`` = the student's account, ``details`` =
``{"student_id", "consent_id", "cause"}``."""

type SuspensionCause = Literal[
    "consent_revoked", "guardian_unlinked", "guardian_deactivated"
]


# --- Validity -----------------------------------------------------------------------


async def valid_login_consent_ids(
    session: AsyncSession, student_id: uuid.UUID
) -> list[uuid.UUID]:
    """The student's **valid** ``student_login`` consents (one statement):
    not revoked, the guardian still linked to the student and active."""
    rows = await session.scalars(
        select(ConsentRecord.id)
        .join(
            StudentGuardian,
            (StudentGuardian.student_id == ConsentRecord.student_id)
            & (StudentGuardian.guardian_id == ConsentRecord.guardian_id),
        )
        .join(Guardian, Guardian.id == ConsentRecord.guardian_id)
        .where(
            ConsentRecord.student_id == student_id,
            ConsentRecord.scope == STUDENT_LOGIN,
            ConsentRecord.revoked_at.is_(None),
            Guardian.is_active.is_(True),
        )
        .order_by(ConsentRecord.id)
    )
    return list(rows)


async def has_login_basis(
    session: AsyncSession, student: Student, *, birthdate: date | None = None
) -> bool:
    """B3 steps 2-3 for this profile: an adult, or a minor with a valid
    consent. ``birthdate`` overrides the stored one (#35 E5)."""
    if not is_minor(
        birthdate or student.birthdate, await administration.center_today(session)
    ):
        return True
    return bool(await valid_login_consent_ids(session, student.id))


async def ensure_login_basis(session: AsyncSession, student: Student) -> None:
    """``409 guardian_consent_required`` unless :func:`has_login_basis`."""
    if not await has_login_basis(session, student):
        raise GuardianConsentRequiredError()


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


class FamiliesStudentLoginGate:
    """The families implementation of ``identity.service.StudentLoginGate``
    (#35 B1, B3). ``identity`` has already locked the user's row."""

    async def ensure_login_allowed(
        self, session: AsyncSession, *, user_id: uuid.UUID
    ) -> None:
        student: Student | None = await session.scalar(
            select(Student)
            .where(Student.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if student is None:
            raise StudentProfileRequiredError()
        await ensure_login_basis(session, student)


STUDENT_LOGIN_GATE: Final = FamiliesStudentLoginGate()
"""The instance the composition roots register."""


# --- Locks and the loss of basis (E1) ------------------------------------------------


async def _lock_account(session: AsyncSession, user_id: uuid.UUID) -> None:
    reference = await identity.get_account_reference(session, user_id)
    if reference is not None and OWNER in reference.roles:
        # A suspension of this login would take the Owner guard's lock; take
        # it first so the order stays advisory lock -> user -> student.
        await identity.lock_owner_guard(session)
    await identity.lock_user(session, user_id)


async def lock_student_with_login(
    session: AsyncSession, student_id: uuid.UUID
) -> Student | None:
    """Lock the student's login account (when one is linked), then the
    student row ``FOR UPDATE``; ``None`` for an unknown student. The lock
    order of the module docstring."""
    user_id = await session.scalar(
        select(Student.user_id).where(Student.id == student_id)
    )
    if user_id is not None:
        await _lock_account(session, user_id)
    student = await _student_for_update(session, student_id)
    if student is not None and student.user_id not in (None, user_id):
        # Relinked between the two reads (rare): lock that account too.
        await _lock_account(session, student.user_id)  # pragma: no cover
    return student


async def login_is_active_student(
    session: AsyncSession, user_id: uuid.UUID | None
) -> bool:
    """Whether ``user_id`` is an ``active`` account holding ``student``."""
    if user_id is None:
        return False
    reference = await identity.get_account_reference(session, user_id)
    return (
        reference is not None
        and reference.state == ACTIVE
        and STUDENT in reference.roles
    )


async def reevaluate_login(
    session: AsyncSession,
    student: Student,
    *,
    cause: SuspensionCause,
    consent_id: uuid.UUID | None = None,
) -> bool:
    """E1: suspend the student's login when it is ``active``, holds
    ``student``, the student is a minor and no valid consent is left.

    Returns whether it suspended. The caller has taken
    :func:`lock_student_with_login`."""
    user_id = student.user_id
    if user_id is None or not await login_is_active_student(session, user_id):
        return False
    if await has_login_basis(session, student):
        return False
    await identity.change_user_state(session, user_id=user_id, new_state=SUSPENDED)
    record_audit_event(
        session,
        action=STUDENT_LOGIN_SUSPENDED_EVENT,
        record_type="users",
        record_id=user_id,
        details={"student_id": student.id, "consent_id": consent_id, "cause": cause},
    )
    return True


async def ensure_birthdate_allowed(
    session: AsyncSession, student: Student, new_birthdate: date
) -> None:
    """E5: a birthdate that makes the student a minor while their login is
    ``active`` without a valid consent is ``409 guardian_consent_required``
    on ``["body", "birthdate"]`` (nothing is written)."""
    if not await login_is_active_student(session, student.user_id):
        return
    if await has_login_basis(session, student, birthdate=new_birthdate):
        return
    raise GuardianConsentRequiredError(
        errors=[
            {
                "path": ["body", "birthdate"],
                "code": "guardian_consent_required",
                "message": (
                    "The student would be a minor without a guardian's consent"
                    " for their login."
                ),
            }
        ]
    )


# --- Linking a login account (C) ------------------------------------------------------


async def link_account(
    session: AsyncSession,
    student: Student,
    *,
    user_id: uuid.UUID | None,
    version: int,
) -> None:
    """Set or clear ``student.user_id`` (the row is locked by the caller).

    Order: ``409 version_conflict`` → ``422 student_account_unavailable`` (an
    unknown or archived account) → ``409 student_account_in_use`` (a
    pre-check; a violation of exactly ``uq_students_user_id`` is the same
    code) → the gate for **this** student when the account is ``active`` and
    holds ``student`` → the update. Unlinking (``None``) is always allowed;
    the same account is a no-op.
    """
    ensure_version(student, version)
    if user_id == student.user_id:
        return
    if user_id is not None:
        reference = await identity.get_account_reference(session, user_id)
        if reference is None or reference.state == ARCHIVED:
            raise StudentAccountUnavailableError()
        other = await session.scalar(
            select(Student.id).where(
                Student.user_id == user_id, Student.id != student.id
            )
        )
        if other is not None:
            raise StudentAccountInUseError()
        if reference.state == ACTIVE and STUDENT in reference.roles:
            await ensure_login_basis(session, student)
    student.user_id = user_id
    try:
        async with session.begin_nested():
            session.add(student)
    except IntegrityError as exc:
        if STUDENT_USER_UNIQUE in str(exc.orig):
            raise StudentAccountInUseError() from None
        raise


__all__ = [
    "STUDENT_LOGIN_GATE",
    "STUDENT_LOGIN_SUSPENDED_EVENT",
    "FamiliesStudentLoginGate",
    "ensure_birthdate_allowed",
    "ensure_login_basis",
    "has_login_basis",
    "link_account",
    "lock_student_with_login",
    "login_is_active_student",
    "reevaluate_login",
    "valid_login_consent_ids",
]
