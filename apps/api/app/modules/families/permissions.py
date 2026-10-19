"""Record policies of student profiles (#33 F; #21 C2 convention).

Pure functions, no I/O: the service loads the facts about the caller
(:class:`StudentAccessFacts`) and passes them in.

**Who sees a student** (:func:`can_view_student`), any matching role allows
(the union of a multi-role user's roles):

- a **staff** role (:data:`STAFF_ROLES`) together with ``student.view``:
  every student;
- ``tutor``: the students in ``assigned_student_ids`` (current assignments;
  empty until M4 replaces the stub source);
- ``guardian``: the students in ``linked_student_ids`` (the links of the
  caller's **active** guardian profile, #34);
- ``student``: the profile ``own_student_id`` (the profile linked to the
  caller's login, #35).

A student that is not visible is ``404 not_found``, the same as a missing
one (``authorize_record``); a list leaves it out.

**How much of it** (:func:`student_access_level`, owner decision #33 E2 (a)):
``full`` (the ``personal`` part: birthdate, contact details, emergency
contacts) for holders of ``student.manage``, a linked guardian of a **minor**
(owner decision #34 E3 (a)), a linked guardian of an adult who gave that
guardian a ``guardian_academic_access`` consent (#35 E4 (a)) and the student
themselves; ``limited`` for every other viewer (Finance Staff, assigned
tutors, a linked guardian of an adult without that consent, ADR-0007 D17).

**Guardian profiles** (:func:`can_view_guardian`, #34 F1): a staff role
together with ``guardian.view`` sees every profile; a ``guardian`` sees only
their own (``GuardianAccessFacts.own_guardian_id``, the active profile linked
to their account). The co-guardians of a student are shown only to staff
(:func:`sees_student_guardians`).

**Families** (:func:`can_view_family`, #172 D1): a staff role together with
``guardian.view`` sees every family; a ``guardian`` sees only the family of
their active profile (``FamilyAccessFacts.own_family_id``). Family membership
grants no student access.

The role and permission codes are the #20 catalog's, repeated here because
this module imports no other module's internals (ADR-0002);
``tests/unit/test_student_policy.py`` checks they match
``app.modules.identity.permissions``.
"""

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from app.core.authz import Principal

OWNER: Final = "owner"
ADMINISTRATOR: Final = "administrator"
ACADEMIC_COORDINATOR: Final = "academic_coordinator"
FINANCE_STAFF: Final = "finance_staff"
TUTOR: Final = "tutor"
STUDENT: Final = "student"
GUARDIAN: Final = "guardian"

STAFF_ROLES: Final = frozenset(
    {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF}
)

STUDENT_VIEW: Final = "student.view"
STUDENT_MANAGE: Final = "student.manage"
GUARDIAN_VIEW: Final = "guardian.view"
FAMILY_MANAGE: Final = "family.manage"
CONSENT_RECORD: Final = "consent.record"

type AccessLevel = Literal["full", "limited"]


@dataclass(frozen=True)
class StudentAccessFacts:
    """What the caller is linked to. Each set is loaded only when the caller
    holds the matching role; otherwise it stays empty (or ``None``)."""

    assigned_student_ids: frozenset[uuid.UUID] = frozenset()
    """Tutor: the students of their current assignments (M4)."""
    linked_student_ids: frozenset[uuid.UUID] = frozenset()
    """Guardian: the students linked to their active guardian profile (#34)."""
    own_student_id: uuid.UUID | None = None
    """Student user: their own profile (#35; ``students.user_id``, whatever
    the profile's ``is_active``)."""
    academic_consent_student_ids: frozenset[uuid.UUID] = frozenset()
    """Guardian: the linked students with an active
    ``guardian_academic_access`` consent for the caller's own active profile
    (#35 E4 (a), per guardian)."""


NO_FACTS: Final = StudentAccessFacts()


def sees_every_student(principal: Principal) -> bool:
    """A staff role together with ``student.view``: every student is visible."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(STUDENT_VIEW)


def visible_student_ids(
    principal: Principal, facts: StudentAccessFacts
) -> frozenset[uuid.UUID]:
    """For a caller who doesn't see every student: the ids they may see
    (``assigned ∪ linked ∪ own``, each only through its role)."""
    ids: set[uuid.UUID] = set()
    if TUTOR in principal.roles:
        ids |= facts.assigned_student_ids
    if GUARDIAN in principal.roles:
        ids |= facts.linked_student_ids
    if STUDENT in principal.roles and facts.own_student_id is not None:
        ids.add(facts.own_student_id)
    return frozenset(ids)


def can_view_student(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """Whether the caller may see this student at all."""
    return sees_every_student(principal) or student_id in visible_student_ids(
        principal, facts
    )


def student_access_level(
    principal: Principal,
    student_id: uuid.UUID,
    facts: StudentAccessFacts,
    *,
    is_minor: bool,
) -> AccessLevel | None:
    """``"full"``, ``"limited"``, or ``None`` when the student is not visible.

    ``is_minor`` is the student's status today in the center's time zone
    (``ages.is_minor(birthdate, center_today())``), passed in so the function
    stays pure. With several roles, the highest level wins (``full`` >
    ``limited``).

    A linked guardian gets ``full`` for a minor and, for an adult, only when
    the student gave **that** guardian a ``guardian_academic_access`` consent
    (:func:`guardian_has_academic_access`; owner decisions #34 E3 (a), #35 E4
    (a); ADR-0007 D17); otherwise ``limited``.
    """
    if not can_view_student(principal, student_id, facts):
        return None
    if principal.has(STUDENT_MANAGE):
        return "full"
    if guardian_has_academic_access(
        principal, student_id, is_minor=is_minor, facts=facts
    ):
        return "full"
    if STUDENT in principal.roles and facts.own_student_id == student_id:
        return "full"
    return "limited"


def guardian_has_academic_access(
    principal: Principal,
    student_id: uuid.UUID,
    *,
    is_minor: bool,
    facts: StudentAccessFacts,
) -> bool:
    """Whether the caller, **as a linked guardian**, may see the student's
    academic records (ADR-0007 D17; #35 F2).

    A guardian linked to the student through their active profile: always
    for a minor; for an adult only with an active ``guardian_academic_access``
    consent naming their own profile (owner decision #35 E4 (a), per
    guardian). The fact M4-M5 academic policies must use for guardian reads;
    never re-query consents.
    """
    if GUARDIAN not in principal.roles or student_id not in facts.linked_student_ids:
        return False
    return is_minor or student_id in facts.academic_consent_student_ids


def sees_student_guardians(principal: Principal) -> bool:
    """Whether ``StudentDetail.guardians`` is shown (#34 E1): a staff role
    together with ``guardian.view``. Tutors, student users and linked
    guardians never see a student's guardians (a guardian never sees the
    co-guardians, which protects separated families). The same rule as
    :func:`sees_every_guardian`."""
    return sees_every_guardian(principal)


@dataclass(frozen=True)
class GuardianAccessFacts:
    """The caller's own guardian profile: loaded only for a caller holding
    ``guardian``; ``None`` without an **active** profile linked to their
    account."""

    own_guardian_id: uuid.UUID | None = None


NO_GUARDIAN_FACTS: Final = GuardianAccessFacts()


def sees_every_guardian(principal: Principal) -> bool:
    """A staff role together with ``guardian.view``: every profile is visible."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(GUARDIAN_VIEW)


def can_view_guardian(
    principal: Principal, guardian_id: uuid.UUID, facts: GuardianAccessFacts
) -> bool:
    """Whether the caller may see this guardian profile (#34 F1)."""
    if sees_every_guardian(principal):
        return True
    return GUARDIAN in principal.roles and (
        facts.own_guardian_id is not None and guardian_id == facts.own_guardian_id
    )


# --- Family groupings (#172 D) ----------------------------------------------------


@dataclass(frozen=True)
class FamilyAccessFacts:
    """The caller's own family: loaded only for a caller holding ``guardian``;
    the family of their **active** guardian profile, or ``None``."""

    own_family_id: uuid.UUID | None = None


NO_FAMILY_FACTS: Final = FamilyAccessFacts()


def sees_every_family(principal: Principal) -> bool:
    """A staff role together with ``guardian.view`` (#172: there is no
    ``family.view``; reads use ``guardian.view``). The same rule as
    :func:`sees_every_guardian`."""
    return sees_every_guardian(principal)


def can_view_family(
    principal: Principal, family_id: uuid.UUID, facts: FamilyAccessFacts
) -> bool:
    """Whether the caller may see this family (#172 D1): every family for a
    staff role with ``guardian.view``; for a ``guardian``, only the family of
    their active profile. Membership is never student access: what a
    guardian sees of a student follows the links only (#34)."""
    if sees_every_family(principal):
        return True
    return GUARDIAN in principal.roles and (
        facts.own_family_id is not None and family_id == facts.own_family_id
    )


# --- Consent records (#35 D1) -------------------------------------------------------

STUDENT_LOGIN: Final = "student_login"
GUARDIAN_ACADEMIC_ACCESS: Final = "guardian_academic_access"
IN_APP: Final = "in_app"
PAPER: Final = "paper"
IN_PERSON: Final = "in_person"

CONSENT_STAFF_ROLES: Final = frozenset({OWNER, ADMINISTRATOR})
"""The staff roles that hold ``consent.record`` (#20 catalog; a unit test
keeps it equal to the seeded role permissions)."""

type ConsentClause = Literal["staff", "guardian", "student"]


@dataclass(frozen=True)
class ConsentContext:
    """The caller's relation to one student, loaded by the service.

    ``guardian_id`` is the caller's own **active** guardian profile when it is
    linked to this student (else ``None``); ``own_student`` says the student
    is the caller's own profile (``StudentAccessFacts.own_student_id``)."""

    guardian_id: uuid.UUID | None = None
    own_student: bool = False


@dataclass(frozen=True)
class ConsentRule:
    """What one clause of the record policy allows (D1)."""

    clause: ConsentClause
    scopes: frozenset[str]
    methods: frozenset[str]


STAFF_RULE: Final = ConsentRule(
    "staff",
    frozenset({STUDENT_LOGIN, GUARDIAN_ACADEMIC_ACCESS}),
    frozenset({PAPER, IN_PERSON}),
)
GUARDIAN_RULE: Final = ConsentRule(
    "guardian", frozenset({STUDENT_LOGIN}), frozenset({IN_APP})
)
STUDENT_RULE: Final = ConsentRule(
    "student", frozenset({GUARDIAN_ACADEMIC_ACCESS}), frozenset({IN_APP})
)


def is_consent_staff(principal: Principal) -> bool:
    """A staff role that holds ``consent.record`` (Owner, Administrator)
    together with the permission: every student and every record."""
    return bool(CONSENT_STAFF_ROLES & principal.roles) and principal.has(CONSENT_RECORD)


def consent_rules(
    principal: Principal, context: ConsentContext
) -> tuple[ConsentRule, ...]:
    """The clauses that apply to the caller for this student, staff first
    (the union of a multi-role caller's roles). Empty: the student is hidden
    on the consent routes (``404``)."""
    if not principal.has(CONSENT_RECORD):
        return ()
    rules: list[ConsentRule] = []
    if is_consent_staff(principal):
        rules.append(STAFF_RULE)
    if GUARDIAN in principal.roles and context.guardian_id is not None:
        rules.append(GUARDIAN_RULE)
    if STUDENT in principal.roles and context.own_student:
        rules.append(STUDENT_RULE)
    return tuple(rules)


def can_see_consents_of(principal: Principal, context: ConsentContext) -> bool:
    """Whether the student's consent routes are visible to the caller."""
    return bool(consent_rules(principal, context))


def can_see_consent(
    principal: Principal, context: ConsentContext, *, guardian_id: uuid.UUID
) -> bool:
    """Whether one record of a visible student is visible: staff and the
    student themselves see every record; a guardian only the records naming
    their own profile."""
    for rule in consent_rules(principal, context):
        if rule.clause in ("staff", "student"):
            return True
        if guardian_id == context.guardian_id:
            return True
    return False


def recordable_scopes(principal: Principal, context: ConsentContext) -> frozenset[str]:
    """The scopes the caller may record for this student (before the age
    rule)."""
    scopes: set[str] = set()
    for rule in consent_rules(principal, context):
        scopes |= rule.scopes
    return frozenset(scopes)


def rule_for(
    principal: Principal, context: ConsentContext, *, scope: str, method: str
) -> ConsentRule | None:
    """The first clause allowing both ``scope`` and ``method``, or ``None``."""
    for rule in consent_rules(principal, context):
        if scope in rule.scopes and method in rule.methods:
            return rule
    return None


def can_revoke_consent(
    principal: Principal,
    context: ConsentContext,
    *,
    scope: str,
    guardian_id: uuid.UUID,
) -> bool:
    """Staff revoke any record; a guardian their own ``student_login``
    records; a student the ``guardian_academic_access`` records of their own
    student (D1)."""
    for rule in consent_rules(principal, context):
        if rule.clause == "staff":
            return True
        if (
            rule.clause == "guardian"
            and scope == STUDENT_LOGIN
            and guardian_id == context.guardian_id
        ):
            return True
        if rule.clause == "student" and scope == GUARDIAN_ACADEMIC_ACCESS:
            return True
    return False


def applies_at_age(scope: str, *, is_minor: bool) -> bool:
    """``student_login`` only for a minor; ``guardian_academic_access`` only
    for an adult (#35 D3 step 6)."""
    return is_minor if scope == STUDENT_LOGIN else not is_minor
