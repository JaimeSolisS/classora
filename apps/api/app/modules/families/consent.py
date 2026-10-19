"""Consent records: list, record, revoke (#35 D; ADR-0007 D17, D18).

Who may do what is the pure record policy in
:mod:`app.modules.families.permissions` (:func:`consent_rules` and
friends; #35 D1):

- **staff** with ``consent.record`` (Owner, Administrator): any student, any
  linked active guardian, method ``paper`` or ``in_person``; they see and
  revoke every record;
- a **guardian**: ``student_login`` for their linked students (minors), as
  their own active profile, method ``in_app``; they see and revoke only the
  records naming their own profile;
- a **student**: ``guardian_academic_access`` for themselves (adults), naming
  one of their linked active guardians, method ``in_app``; they see every
  record of their own student and revoke its ``guardian_academic_access``
  records.

A student the caller can't reach this way, and a record they can't see, are
``404`` (the same as a missing one). Each new record stamps the current
privacy-notice version (#174) and ``clock.now()``; the student row is locked
``FOR UPDATE``, so two identical consents serialize (the second is ``409
consent_already_given``; the partial unique index ``uq_consent_records_active``
backs it). Revoking runs the loss-of-basis check (``logins.reevaluate_login``)
in the same transaction.

Audit: automatic only (#22): a ``create`` per record and an ``update`` per
revocation, plus ``families.student_login_suspended`` from ``logins``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import (
    PermissionDeniedError,
    Principal,
    RecordNotFoundError,
    authorize_record,
)
from app.core.clock import center_today
from app.modules.families import logins, search
from app.modules.families.ages import is_minor
from app.modules.families.assignments import TutorAssignmentSource
from app.modules.families.errors import (
    ConsentAlreadyGivenError,
    ConsentAlreadyRevokedError,
    ConsentGuardianInvalidError,
    ConsentMethodInvalidError,
    ConsentNotApplicableError,
)
from app.modules.families.facts import load_access_facts, load_guardian_facts
from app.modules.families.models import (
    CONSENT_ACTIVE_UNIQUE,
    ConsentRecord,
    Guardian,
    Student,
    StudentGuardian,
)
from app.modules.families.permissions import (
    GUARDIAN,
    STUDENT_LOGIN,
    ConsentContext,
    applies_at_age,
    can_revoke_consent,
    can_see_consent,
    can_see_consents_of,
    consent_rules,
    recordable_scopes,
    rule_for,
)
from app.modules.families.schemas import (
    ConsentCreate,
    ConsentGuardianOption,
    ConsentMethod,
    ConsentOverview,
    ConsentRecordItem,
    ConsentRevocation,
    ConsentScope,
    GuardianRelationship,
    RevocationCause,
)
from app.modules.identity import service as identity

GUARDIAN_ORDER = search.default_order(
    Guardian.given_names, Guardian.family_names, Guardian.id
)

# --- Context ----------------------------------------------------------------------


async def _context(
    session: AsyncSession,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
) -> ConsentContext:
    """The caller's relation to this student (facts loaded per request, never
    cached)."""
    facts = await load_access_facts(session, principal, source)
    guardian_id = None
    if GUARDIAN in principal.roles and student_id in facts.linked_student_ids:
        guardian_id = (await load_guardian_facts(session, principal)).own_guardian_id
    return ConsentContext(
        guardian_id=guardian_id, own_student=facts.own_student_id == student_id
    )


def _visible(
    principal: Principal, context: ConsentContext, student: Student | None
) -> Student:
    return authorize_record(
        student, can_view=lambda _record: can_see_consents_of(principal, context)
    )


# --- Projections ------------------------------------------------------------------


async def _links(
    session: AsyncSession, student_id: uuid.UUID
) -> list[tuple[str, Guardian]]:
    rows = await session.execute(
        select(StudentGuardian.relationship, Guardian)
        .join(Guardian, Guardian.id == StudentGuardian.guardian_id)
        .where(StudentGuardian.student_id == student_id)
        .order_by(*GUARDIAN_ORDER)
    )
    return [(relationship, guardian) for relationship, guardian in rows]


def _guardian_options(
    principal: Principal,
    context: ConsentContext,
    links: Sequence[tuple[str, Guardian]],
) -> list[ConsentGuardianOption]:
    """Staff and the student: every linked guardian; a guardian: themselves
    (a guardian never sees co-guardians, #34 E1)."""
    clauses = {rule.clause for rule in consent_rules(principal, context)}
    every = bool(clauses & {"staff", "student"})
    return [
        ConsentGuardianOption(
            guardian_id=guardian.id,
            given_names=guardian.given_names,
            family_names=guardian.family_names,
            relationship=cast(GuardianRelationship, relationship),
            is_active=guardian.is_active,
        )
        for relationship, guardian in links
        if every or guardian.id == context.guardian_id
    ]


async def _records(
    session: AsyncSession, student_id: uuid.UUID
) -> list[tuple[ConsentRecord, Guardian]]:
    rows = await session.execute(
        select(ConsentRecord, Guardian)
        .join(Guardian, Guardian.id == ConsentRecord.guardian_id)
        .where(ConsentRecord.student_id == student_id)
    )
    return [(record, guardian) for record, guardian in rows]


class _LoginFacts:
    """What ``suspends_login_on_revoke`` needs, loaded once per response."""

    def __init__(self, *, active_minor_login: bool, valid: Sequence[uuid.UUID]):
        self.active_minor_login = active_minor_login
        self.valid = list(valid)

    def suspends(self, record: ConsentRecord) -> bool:
        return (
            self.active_minor_login
            and record.scope == STUDENT_LOGIN
            and self.valid == [record.id]
        )


async def _login_facts(session: AsyncSession, student: Student) -> _LoginFacts:
    if not is_minor(student.birthdate, center_today()) or not (
        await logins.login_is_active_student(session, student.user_id)
    ):
        return _LoginFacts(active_minor_login=False, valid=())
    return _LoginFacts(
        active_minor_login=True,
        valid=await logins.valid_login_consent_ids(session, student.id),
    )


async def _items(
    session: AsyncSession,
    principal: Principal,
    context: ConsentContext,
    student: Student,
    rows: Sequence[tuple[ConsentRecord, Guardian]],
) -> list[ConsentRecordItem]:
    versions = await identity.privacy_notice_versions(
        session, {record.notice_version_id for record, _ in rows}
    )
    login = await _login_facts(session, student)
    items = [
        ConsentRecordItem(
            id=record.id,
            scope=cast(ConsentScope, record.scope),
            guardian_id=record.guardian_id,
            guardian_given_names=guardian.given_names,
            guardian_family_names=guardian.family_names,
            method=cast(ConsentMethod, record.method),
            notice_version=versions[record.notice_version_id],
            granted_at=record.granted_at,
            revoked_at=record.revoked_at,
            revocation_cause=cast(RevocationCause | None, record.revocation_cause),
            can_revoke=record.revoked_at is None
            and can_revoke_consent(
                principal, context, scope=record.scope, guardian_id=record.guardian_id
            ),
            suspends_login_on_revoke=record.revoked_at is None
            and login.suspends(record),
        )
        for record, guardian in rows
    ]
    # Active first, then the newest grant first (the id breaks ties).
    items.sort(key=lambda item: str(item.id), reverse=True)
    items.sort(key=lambda item: item.granted_at, reverse=True)
    items.sort(key=lambda item: item.revoked_at is not None)
    return items


# --- Reads ------------------------------------------------------------------------


async def get_consents(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
) -> ConsentOverview:
    """The consent overview of a student (D2); ``404`` when hidden."""
    context = await _context(session, principal, source, student_id)
    student = _visible(principal, context, await session.get(Student, student_id))
    minor = is_minor(student.birthdate, center_today())
    links = await _links(session, student.id)
    rows = [
        (record, guardian)
        for record, guardian in await _records(session, student.id)
        if can_see_consent(principal, context, guardian_id=record.guardian_id)
    ]
    login_state = None
    if student.user_id is not None:
        reference = await identity.get_account_reference(session, student.user_id)
        login_state = reference.state if reference is not None else None
    notice = await identity.current_privacy_notice(session)
    return ConsentOverview(
        student_id=student.id,
        is_minor=minor,
        login_state=login_state,
        current_notice_version=notice.version,
        guardians=_guardian_options(principal, context, links),
        consents=await _items(session, principal, context, student, rows),
        can_record=sorted(
            cast(ConsentScope, scope)
            for scope in recordable_scopes(principal, context)
            if applies_at_age(scope, is_minor=minor)
        ),
    )


# --- Writes -----------------------------------------------------------------------


async def record_consent(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    body: ConsentCreate,
) -> ConsentRecordItem:
    """Record a consent (D3). Order after the body's ``422``: ``404`` →
    ``403`` (scope) → ``422 consent_method_invalid`` → ``403`` (a guardian
    naming another guardian) / ``422 consent_guardian_invalid`` → ``422
    consent_not_applicable`` → ``409 consent_already_given`` → the insert.
    The student row is locked ``FOR UPDATE`` first."""
    context = await _context(session, principal, source, student_id)
    student = _visible(
        principal,
        context,
        await session.scalar(
            select(Student)
            .where(Student.id == student_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )
    if body.scope not in recordable_scopes(principal, context):
        raise PermissionDeniedError()
    rule = rule_for(principal, context, scope=body.scope, method=body.method)
    if rule is None:
        raise ConsentMethodInvalidError()
    if rule.clause == "guardian" and body.guardian_id != context.guardian_id:
        raise PermissionDeniedError()
    guardian = await session.scalar(
        select(Guardian)
        .join(StudentGuardian, StudentGuardian.guardian_id == Guardian.id)
        .where(
            StudentGuardian.student_id == student.id,
            Guardian.id == body.guardian_id,
        )
    )
    if guardian is None or not guardian.is_active:
        raise ConsentGuardianInvalidError()
    if not applies_at_age(
        body.scope, is_minor=is_minor(student.birthdate, center_today())
    ):
        raise ConsentNotApplicableError()
    existing = await session.scalar(
        select(ConsentRecord.id).where(
            ConsentRecord.student_id == student.id,
            ConsentRecord.guardian_id == guardian.id,
            ConsentRecord.scope == body.scope,
            ConsentRecord.revoked_at.is_(None),
        )
    )
    if existing is not None:
        raise ConsentAlreadyGivenError()
    notice = await identity.current_privacy_notice(session)
    record = ConsentRecord(
        student_id=student.id,
        guardian_id=guardian.id,
        scope=body.scope,
        method=body.method,
        notice_version_id=notice.id,
        granted_at=clock.now(),
        recorded_by_user_id=principal.user_id,
    )
    try:
        async with session.begin_nested():
            session.add(record)
    except IntegrityError as exc:
        if CONSENT_ACTIVE_UNIQUE in str(exc.orig):
            raise ConsentAlreadyGivenError() from None
        raise
    [item] = await _items(session, principal, context, student, [(record, guardian)])
    return item


async def revoke_consent(
    session: AsyncSession,
    *,
    principal: Principal,
    source: TutorAssignmentSource,
    student_id: uuid.UUID,
    consent_id: uuid.UUID,
) -> ConsentRevocation:
    """Revoke a record (D4), then run the loss-of-basis check (E1, E2).

    ``404`` for a hidden student or a record that isn't this student's or
    isn't visible → ``403`` when the caller may not revoke it → ``409
    consent_already_revoked``. Locks the student's login, then the student
    row (``logins.lock_student_with_login``), then the record."""
    context = await _context(session, principal, source, student_id)
    student = _visible(
        principal, context, await logins.lock_student_with_login(session, student_id)
    )
    row = (
        await session.execute(
            select(ConsentRecord, Guardian)
            .join(Guardian, Guardian.id == ConsentRecord.guardian_id)
            .where(
                ConsentRecord.id == consent_id, ConsentRecord.student_id == student.id
            )
            .with_for_update(of=ConsentRecord)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None or not can_see_consent(
        principal, context, guardian_id=row[0].guardian_id
    ):
        raise RecordNotFoundError()
    record, guardian = row
    if not can_revoke_consent(
        principal, context, scope=record.scope, guardian_id=record.guardian_id
    ):
        raise PermissionDeniedError()
    if record.revoked_at is not None:
        raise ConsentAlreadyRevokedError()
    record.revoked_at = clock.now()
    record.revoked_by_user_id = principal.user_id
    record.revocation_cause = "revoked"
    await session.flush()
    suspended = await logins.reevaluate_login(
        session, student, cause="consent_revoked", consent_id=record.id
    )
    [item] = await _items(session, principal, context, student, [(record, guardian)])
    return ConsentRevocation(consent=item, student_login_suspended=suspended)


async def revoke_on_unlink(
    session: AsyncSession,
    *,
    principal: Principal,
    student: Student,
    guardian_id: uuid.UUID,
) -> uuid.UUID | None:
    """E3: revoke the guardian's active records for the student (both scopes)
    with cause ``guardian_unlinked``. Returns the revoked ``student_login``
    record's id, or ``None``."""
    records = list(
        await session.scalars(
            select(ConsentRecord)
            .where(
                ConsentRecord.student_id == student.id,
                ConsentRecord.guardian_id == guardian_id,
                ConsentRecord.revoked_at.is_(None),
            )
            .order_by(ConsentRecord.id)
            .with_for_update()
        )
    )
    login_record = None
    now = clock.now()
    for record in records:
        record.revoked_at = now
        record.revoked_by_user_id = principal.user_id
        record.revocation_cause = "guardian_unlinked"
        if record.scope == STUDENT_LOGIN:
            login_record = record.id
    if records:
        await session.flush()
    return login_record


__all__ = ["get_consents", "record_consent", "revoke_consent", "revoke_on_unlink"]
