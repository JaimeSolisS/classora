"""``people`` (#543): the simulation's accounts, families, guardians and
students, through the identity and families services.

At :data:`~.anchor.SIM_SETUP`: the Owner (the CLI bootstrap's rows, the one
documented direct insert: ``bootstrap-owner`` runs its own Unit of Work), the
three other staff accounts and the tutors who start then, each invited by
the Owner and redeemed with its role's development password; then every
family that starts then, created by the Administrator (family, guardian
profiles, students with emergency contacts, links, memberships; guardian
and adult-student logins invited by the Administrator).

Dated steps (``on_day``, each at its planned instant): the approved
self-registration (7 Sep) and its family (enrolled later); the families
that enroll later; tutor 12's account (1 Oct); the Owner's delegations
(one granted 12 Oct and revoked 16 Oct, one granted 18 Nov; #543 D3); on 19
Oct, the day after privacy-notice version 1 is published, every login
account's acceptance, the minors' guardian consents (in-app by guardians
with a login, paper or in person by the Administrator) and the teen
students' logins (#543 D2); the pending self-registration (18 Nov). At
:data:`~.anchor.SIM_NOW`: the pending tutor invitation ("joining in
December"; an unredeemed ``account_tokens`` row).

Every login account stores the run's language and a theme (light, dark or
unset) through the preferences service.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.cli import OWNER_BOOTSTRAPPED_EVENT
from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import Principal
from app.core.reauth import RecentAuthGuard
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import EducationLevel
from app.modules.families import consent as families_consent
from app.modules.families import groupings, guardians
from app.modules.families import service as families_service
from app.modules.families.assignments import get_tutor_assignment_source
from app.modules.families.models import (
    ConsentRecord,
    Family,
    FamilyGuardian,
    FamilyStudent,
    Guardian,
    Student,
    StudentEmergencyContact,
    StudentGuardian,
)
from app.modules.families.schemas import (
    ConsentCreate,
    EmergencyContactCreate,
    FamilyCreate,
    GuardianCreate,
    StudentAccountUpdate,
    StudentCreate,
    StudentGuardianCreate,
)
from app.modules.identity import invitations, registrations
from app.modules.identity import service as identity
from app.modules.identity.models import (
    AccountToken,
    PrivacyNoticeAcceptance,
    RegistrationRequest,
    Role,
    User,
    UserDelegation,
    UserPreference,
    UserRole,
)
from app.modules.identity.passwords import hash_password
from app.modules.identity.permissions import ACTIVE, GUARDIAN, OWNER
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_SETUP, ZONE, sim_clock
from tests.factories.simulation.plan import (
    REGISTRATION_APPROVED,
    REGISTRATION_SUBMITTED,
    AccountPlan,
    FamilyPlan,
    GuardianPlan,
    StudentPlan,
    TutorPlan,
)
from tests.factories.simulation.timeline import SimulationContext

INVITATION_ORIGIN: Final = "http://localhost:5173"
"""Only the token is read back from the (never shown) invitation link."""
INVITE_ACTION: Final = "user.invitation.staff_role"

PEOPLE_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    User,
    UserRole,
    AccountToken,
    UserPreference,
    PrivacyNoticeAcceptance,
    UserDelegation,
    RegistrationRequest,
    ApprovalRequest,
    Notification,
    Family,
    FamilyGuardian,
    FamilyStudent,
    Guardian,
    Student,
    StudentEmergencyContact,
    StudentGuardian,
    ConsentRecord,
)

type Step = Callable[[], Awaitable[None]]


def user_key(username: str) -> str:
    return f"user:{username}"


async def principal(
    session: AsyncSession, ctx: SimulationContext, account: AccountPlan
) -> Principal:
    """The (cached) principal of a simulation account; it must be active."""
    user_id = ctx.id(user_key(account.username))
    cached = ctx.principals.get(user_id)
    if cached is not None:
        return cached
    loaded = await identity.load_principal(session, user_id)
    if loaded is None:
        raise RuntimeError(f"{account.username} must be active")
    ctx.principals[user_id] = loaded
    return loaded


class PeopleModule:
    name = "people"
    tables = PEOPLE_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        owner = ctx.plan.owner.username
        return (
            await session.scalar(select(User.id).where(User.username == owner))
        ) is not None

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        plan = ctx.plan
        await _bootstrap_owner(session, ctx, plan.owner)
        owner = await principal(session, ctx, plan.owner)
        for account in plan.staff[1:]:
            await create_login(session, ctx, owner, account)
        for tutor in plan.tutors:
            if tutor.account.created_at == SIM_SETUP:
                await create_login(session, ctx, owner, tutor.account)
        for family in plan.families:
            if family.enrolls_at == SIM_SETUP:
                await enroll_family(session, ctx, family)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {_local_day(at) for at, _ in self._events(None, ctx)}

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for at, step in self._events(session, ctx):
            if _local_day(at) == day:
                with sim_clock(at):
                    await step()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        tutor = ctx.plan.pending_tutor
        owner = await principal(session, ctx, ctx.plan.owner)
        with seed_actor(session, owner.user_id):
            await invitations.create_invitation(
                session,
                principal=owner,
                guard=RecentAuthGuard(INVITE_ACTION, clock.now()),
                username=tutor.username,
                display_name=tutor.display_name,
                role="tutor",
                app_origin=INVITATION_ORIGIN,
            )

    def _events(
        self, session: AsyncSession | None, ctx: SimulationContext
    ) -> list[tuple[datetime, Step]]:
        """Every dated step, sorted by instant (stable: creations before the
        acceptances at the same instant). ``session`` is ``None`` when only
        the days are wanted."""
        plan = ctx.plan
        s: AsyncSession = session  # type: ignore[assignment]
        events: list[tuple[datetime, Step]] = []

        def add(at: datetime, step: Step) -> None:
            events.append((at, step))

        registered = plan.registered_family.guardians[0]
        add(REGISTRATION_SUBMITTED, lambda: _submit_registration(s, ctx, registered))
        add(REGISTRATION_APPROVED, lambda: _approve_registration(s, ctx, registered))
        for family in plan.families:
            if family.enrolls_at != SIM_SETUP:
                add(family.enrolls_at, lambda f=family: enroll_family(s, ctx, f))
        for tutor in plan.tutors:
            if tutor.account.created_at != SIM_SETUP:
                add(tutor.account.created_at, lambda t=tutor: _owner_invites(s, ctx, t))
        for delegation in plan.delegations:
            add(
                delegation.granted_at,
                lambda p=delegation.permission: _delegate(s, ctx, p, grant=True),
            )
            if delegation.revoked_at is not None:
                add(
                    delegation.revoked_at,
                    lambda p=delegation.permission: _delegate(s, ctx, p, grant=False),
                )
        for family in plan.families:
            for student in family.students:
                if student.consent is not None:
                    add(
                        student.consent.at,
                        lambda f=family, st=student: _consent(s, ctx, f, st),
                    )
                if (
                    student.account is not None
                    and student.account.created_at > family.enrolls_at
                ):
                    add(
                        student.account.created_at,
                        lambda st=student: _student_login(s, ctx, st),
                    )
        add(plan.pending_registration.at, lambda: _submit_pending(s, ctx))
        for account in plan.accounts:
            add(
                account.privacy_accepted_at,
                lambda a=account: _accept_privacy_notice(s, ctx, a),
            )
        return sorted(events, key=lambda event: event[0])


def _local_day(at: datetime) -> date:
    return at.astimezone(ZONE).date()


# --- Accounts -----------------------------------------------------------------------


async def _bootstrap_owner(
    session: AsyncSession, ctx: SimulationContext, account: AccountPlan
) -> None:
    """What ``make bootstrap-owner`` writes (``app.cli._create_owner``),
    in this Unit of Work: the CLI opens its own, so it can't be called here."""
    role_id = await session.scalar(select(Role.id).where(Role.code == OWNER))
    user = User(
        username=account.username,
        display_name=account.display_name,
        password_hash=hash_password(account.password),
        state=ACTIVE,
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role_id))
    await session.flush()
    record_audit_event(
        session,
        action=OWNER_BOOTSTRAPPED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"source": "cli"},
    )
    ctx.ids[user_key(account.username)] = user.id
    await _preferences(session, user.id, ctx, account)


async def invite(
    session: AsyncSession,
    ctx: SimulationContext,
    inviter: Principal,
    account: AccountPlan,
) -> tuple[uuid.UUID, str]:
    """A pending user with the account's role; returns its id and the raw
    invitation token."""
    with seed_actor(session, inviter.user_id):
        issued = await invitations.create_invitation(
            session,
            principal=inviter,
            guard=RecentAuthGuard(INVITE_ACTION, clock.now()),
            username=account.username,
            display_name=account.display_name,
            role=account.role,
            app_origin=INVITATION_ORIGIN,
        )
    ctx.ids[user_key(account.username)] = issued.user_id
    return issued.user_id, issued.invitation_url.split("#token=", 1)[1]


async def redeem(
    session: AsyncSession,
    ctx: SimulationContext,
    account: AccountPlan,
    user_id: uuid.UUID,
    raw_token: str,
) -> None:
    """The person sets the role's development password (no actor, as the
    public redeem route), then stores their preferences."""
    await invitations.redeem_invitation(
        session, raw_token=raw_token, password=account.password
    )
    await _preferences(session, user_id, ctx, account)


async def create_login(
    session: AsyncSession,
    ctx: SimulationContext,
    inviter: Principal,
    account: AccountPlan,
) -> uuid.UUID:
    user_id, token = await invite(session, ctx, inviter, account)
    await redeem(session, ctx, account, user_id, token)
    return user_id


async def _preferences(
    session: AsyncSession,
    user_id: uuid.UUID,
    ctx: SimulationContext,
    account: AccountPlan,
) -> None:
    with seed_actor(session, user_id):
        if account.theme is None:
            await identity.update_user_preferences(
                session, user_id, language=ctx.language
            )
        else:
            await identity.update_user_preferences(
                session,
                user_id,
                language=ctx.language,
                theme=account.theme,  # type: ignore[arg-type]
            )


async def _owner_invites(
    session: AsyncSession, ctx: SimulationContext, tutor: TutorPlan
) -> None:
    owner = await principal(session, ctx, ctx.plan.owner)
    await create_login(session, ctx, owner, tutor.account)


async def _accept_privacy_notice(
    session: AsyncSession, ctx: SimulationContext, account: AccountPlan
) -> None:
    user_id = ctx.id(user_key(account.username))
    current = await identity.current_privacy_notice(session)
    with seed_actor(session, user_id):
        await identity.accept_privacy_notice(
            session, user_id=user_id, version=current.version
        )
    ctx.principals.pop(user_id, None)  # privacy_notice_accepted changed


async def _delegate(
    session: AsyncSession, ctx: SimulationContext, permission: str, *, grant: bool
) -> None:
    owner_id = ctx.id(user_key(ctx.plan.owner.username))
    admin_id = ctx.id(user_key(ctx.plan.administrator.username))
    with seed_actor(session, owner_id):
        change = identity.grant_delegation if grant else identity.revoke_delegation
        await change(
            session, actor_id=owner_id, user_id=admin_id, permission=permission
        )
    ctx.principals.pop(admin_id, None)


# --- Self-registration --------------------------------------------------------------


async def _submit_registration(
    session: AsyncSession, ctx: SimulationContext, guardian: GuardianPlan
) -> None:
    account = guardian.account
    assert account is not None
    await registrations.submit_registration(
        session,
        username=account.username,
        display_name=account.display_name,
        password=account.password,
        role=GUARDIAN,
    )
    ctx.ids[user_key(account.username)] = await _user_id(session, account.username)


async def _approve_registration(
    session: AsyncSession, ctx: SimulationContext, guardian: GuardianPlan
) -> None:
    account = guardian.account
    assert account is not None
    user_id = ctx.id(user_key(account.username))
    admin = await principal(session, ctx, ctx.plan.administrator)
    request_id = await session.scalar(
        select(ApprovalRequest.id)
        .join(RegistrationRequest, RegistrationRequest.id == ApprovalRequest.subject_id)
        .where(RegistrationRequest.user_id == user_id)
    )
    assert request_id is not None
    with seed_actor(session, admin.user_id):
        await administration.decide_approval_request(
            session, principal=admin, request_id=request_id, action="approve"
        )
    await _preferences(session, user_id, ctx, account)


async def _submit_pending(session: AsyncSession, ctx: SimulationContext) -> None:
    pending = ctx.plan.pending_registration
    await registrations.submit_registration(
        session,
        username=pending.username,
        display_name=pending.display_name,
        password=ctx.plan.registered_family.guardians[0].account.password,  # type: ignore[union-attr]
        role=GUARDIAN,
    )


async def _user_id(session: AsyncSession, username: str) -> uuid.UUID:
    user_id = await session.scalar(select(User.id).where(User.username == username))
    assert user_id is not None
    return user_id


# --- Families -----------------------------------------------------------------------


async def enroll_family(
    session: AsyncSession, ctx: SimulationContext, family: FamilyPlan
) -> None:
    """The Administrator creates the family, its guardian profiles (with
    their logins), its students (with the adults' logins) and every link."""
    admin = await principal(session, ctx, ctx.plan.administrator)
    source = get_tutor_assignment_source()
    with seed_actor(session, admin.user_id):
        created = await groupings.create_family(
            session, principal=admin, body=FamilyCreate(name=family.name)
        )
    ctx.ids[f"family:{family.key}"] = created.id

    for guardian in family.guardians:
        user_id = None
        if guardian.account is not None:
            if guardian.registered:
                user_id = ctx.id(user_key(guardian.account.username))
            else:
                user_id = await create_login(session, ctx, admin, guardian.account)
        with seed_actor(session, admin.user_id):
            profile = await guardians.create_guardian(
                session,
                principal=admin,
                body=GuardianCreate(
                    given_names=guardian.given_names,
                    family_names=guardian.family_names,
                    email=guardian.email,
                    user_id=user_id,
                ),
            )
            await groupings.add_guardian(
                session, principal=admin, family_id=created.id, guardian_id=profile.id
            )
        ctx.ids[f"guardian:{guardian.key}"] = profile.id

    levels = dict(
        (
            await session.execute(select(EducationLevel.seed_key, EducationLevel.id))
        ).all()
    )
    for student in family.students:
        with seed_actor(session, admin.user_id):
            detail = await families_service.create_student(
                session,
                principal=admin,
                body=StudentCreate(
                    given_names=student.given_names,
                    family_names=student.family_names,
                    birthdate=student.birthdate,
                    email=student.email,
                    education_level_id=levels[student.level],
                    emergency_contacts=[
                        EmergencyContactCreate(
                            name=c.name, relationship=c.relationship, phone=c.phone
                        )
                        for c in student.contacts
                    ],
                ),
            )
            await groupings.add_student(
                session, principal=admin, family_id=created.id, student_id=detail.id
            )
            for guardian in family.guardians:
                await guardians.link_guardian(
                    session,
                    principal=admin,
                    source=source,
                    student_id=detail.id,
                    body=StudentGuardianCreate(
                        guardian_id=ctx.id(f"guardian:{guardian.key}"),
                        relationship=guardian.relationship,  # type: ignore[arg-type]
                    ),
                )
        ctx.ids[f"student:{student.key}"] = detail.id
        if student.account is not None and student.account.created_at <= (
            family.enrolls_at
        ):
            await _student_login(session, ctx, student)


async def _student_login(
    session: AsyncSession, ctx: SimulationContext, student: StudentPlan
) -> None:
    """The Administrator invites the student, links the account to the
    profile, then the student redeems (the student-login gate checks the
    adult age or the guardian consent)."""
    account = student.account
    assert account is not None
    admin = await principal(session, ctx, ctx.plan.administrator)
    user_id, token = await invite(session, ctx, admin, account)
    student_id = ctx.id(f"student:{student.key}")
    version = await session.scalar(
        select(Student.version).where(Student.id == student_id)
    )
    assert version is not None
    with seed_actor(session, admin.user_id):
        await families_service.set_student_account(
            session,
            principal=admin,
            source=get_tutor_assignment_source(),
            student_id=student_id,
            body=StudentAccountUpdate(user_id=user_id, version=version),
        )
    await redeem(session, ctx, account, user_id, token)


async def _consent(
    session: AsyncSession,
    ctx: SimulationContext,
    family: FamilyPlan,
    student: StudentPlan,
) -> None:
    consent = student.consent
    assert consent is not None
    guardian = next(g for g in family.guardians if g.key == consent.guardian)
    if consent.method == "in_app":
        assert guardian.account is not None
        actor = await principal(session, ctx, guardian.account)
    else:
        actor = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, actor.user_id):
        await families_consent.record_consent(
            session,
            principal=actor,
            source=get_tutor_assignment_source(),
            student_id=ctx.id(f"student:{student.key}"),
            body=ConsentCreate(
                scope="student_login",
                guardian_id=ctx.id(f"guardian:{guardian.key}"),
                method=consent.method,
            ),
        )
