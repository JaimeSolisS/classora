"""The staff module's public API (ADR-0002): tutor profiles (#43).

Tutors are the users holding the ``tutor`` role; ``staff`` reads them only
through ``identity.service`` (:func:`identity.list_role_members`,
:func:`identity.get_role_member`) and never queries ``users`` itself.

- :func:`list_tutors`, :func:`get_tutor`: the record policy of
  ``staff/permissions.py`` (a tutor-only caller sees only themselves; a
  hidden tutor is ``404``, like a missing one or a user without the role).
- :func:`update_tutor`: order ``404`` → ``403 permission_denied`` (a sent
  field outside :func:`~app.modules.staff.permissions.editable_fields`) →
  ``409 version_conflict`` → the update. The profile row is created by the
  first change (lazy, version 0 before), otherwise its version is
  incremented by any change, account fields included, so the profile's
  ``version`` guards the whole page. Account fields go through
  ``identity.service.update_account_contact`` (``users`` events). A body
  that changes nothing changes no version and writes no event.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import StaleVersionError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.identity import service as identity
from app.modules.identity.schemas import RoleMember
from app.modules.staff.models import USER_ID_UNIQUE, TutorProfile
from app.modules.staff.permissions import (
    ACCOUNT_FIELDS,
    TUTOR,
    TutorField,
    can_view_tutor,
    editable_fields,
    sees_every_tutor,
)
from app.modules.staff.schemas import TutorDetail, TutorListItem, TutorUpdate

__all__ = ["get_tutor", "list_tutors", "update_tutor"]


def _list_item(member: RoleMember) -> TutorListItem:
    return TutorListItem(
        user_id=member.id,
        username=member.username,
        display_name=member.display_name,
        state=member.state,
        email=member.email,
        phone=member.phone,
    )


async def list_tutors(
    session: AsyncSession,
    *,
    principal: Principal,
    q: str | None,
    state: str | None,
    page: OffsetParams,
) -> OffsetPage[TutorListItem]:
    """The tutors the caller may see matching ``q`` and ``state``, by display
    name, then username (the #31 order). A tutor-only caller's page holds
    only themselves. Three statements (``identity.list_role_members``)."""
    only = None if sees_every_tutor(principal) else [principal.user_id]
    members = await identity.list_role_members(
        session, TUTOR, q=q, state=state, page=page, user_ids=only
    )
    return OffsetPage[TutorListItem](
        items=[_list_item(member) for member in members.items],
        total=members.total,
        limit=members.limit,
        offset=members.offset,
    )


async def _visible_member(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> RoleMember:
    """The tutor ``user_id``: ``404`` when unknown, without the ``tutor``
    role now, or not visible to the caller (one indistinguishable error)."""
    if not can_view_tutor(principal, user_id):
        raise RecordNotFoundError()
    member = await identity.get_role_member(session, TUTOR, user_id)
    if member is None:
        raise RecordNotFoundError()
    return member


async def _profile(session: AsyncSession, user_id: uuid.UUID) -> TutorProfile | None:
    profile: TutorProfile | None = await session.scalar(
        select(TutorProfile).where(TutorProfile.user_id == user_id)
    )
    return profile


def _editable(principal: Principal, member: RoleMember) -> frozenset[TutorField]:
    return editable_fields(
        principal,
        member.id,
        may_manage_account=identity.may_manage_user(
            principal.permissions,
            actor_id=principal.user_id,
            target_id=member.id,
            target_roles=member.roles,
        ),
    )


def _detail(
    principal: Principal, member: RoleMember, profile: TutorProfile | None
) -> TutorDetail:
    return TutorDetail(
        **_list_item(member).model_dump(),
        professional_experience=(
            profile.professional_experience if profile is not None else None
        ),
        version=profile.version if profile is not None else 0,
        editable_fields=sorted(_editable(principal, member)),
    )


async def get_tutor(
    session: AsyncSession, *, principal: Principal, user_id: uuid.UUID
) -> TutorDetail:
    """One tutor profile with the caller's ``editable_fields``."""
    member = await _visible_member(session, principal, user_id)
    return _detail(principal, member, await _profile(session, user_id))


async def _flush_profile(session: AsyncSession) -> None:
    """Flush the profile; a concurrent first edit that created the row first
    (``uq_tutor_profiles_user_id``) is ``409 version_conflict``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if USER_ID_UNIQUE in str(exc.orig):
            raise StaleVersionError() from None
        raise


async def update_tutor(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    body: TutorUpdate,
) -> TutorDetail:
    """Change the sent fields (see the module docstring for the order)."""
    member = await _visible_member(session, principal, user_id)
    sent = body.sent_fields()
    if sent - _editable(principal, member):
        raise PermissionDeniedError()
    profile = await _profile(session, user_id)
    current_version = profile.version if profile is not None else 0
    if body.version != current_version:
        raise StaleVersionError()

    account_changes = {
        field: getattr(body, field)
        for field in sent & ACCOUNT_FIELDS
        if getattr(body, field) != getattr(member, field)
    }
    experience_changed = "professional_experience" in sent and (
        body.professional_experience
        != (profile.professional_experience if profile is not None else None)
    )
    if not account_changes and not experience_changed:
        return _detail(principal, member, profile)

    # The profile first: its versioned UPDATE (or the unique user_id of a
    # first INSERT) locks out a concurrent edit before the account changes.
    if profile is None:
        profile = TutorProfile(
            user_id=user_id,
            professional_experience=body.professional_experience,
        )
        session.add(profile)
    elif experience_changed:
        profile.professional_experience = body.professional_experience
    else:
        # Only account fields changed: bump the profile's version anyway
        # (version/updated_at alone write no audit event, #22).
        profile.updated_at = func.now()
    await _flush_profile(session)
    await session.refresh(profile, ["updated_at", "version"])

    if account_changes:
        await identity.update_account_contact(session, user_id, **account_changes)
        member = await _visible_member(session, principal, user_id)
    return _detail(principal, member, profile)
