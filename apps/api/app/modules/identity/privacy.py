"""Privacy-notice versions and acceptances (#174; spec §9, §16).

A version is published by an Alembic migration (owner decision #174 E2 (a))
and never changes. The **current** version is the highest ``version`` whose
``published_at`` is not after :func:`app.core.clock.now`, so a migration can
ship a version that takes effect later. With no published version the
functions here raise :class:`~app.modules.identity.errors.PrivacyNoticeUnavailableError`
(fail closed); migration 0017 publishes version 1.

Every signed-in user must accept the current version: ``load_principal``
reads :func:`accepted_current_notice` in its own user statement (no extra
query, never cached), and ``app.core.authz`` refuses everything else with
``403 privacy_notice_required`` until they do.

Public through ``identity.service``. Every function runs in the caller's Unit
of Work and never commits.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import clock
from app.modules.identity.errors import (
    PrivacyNoticeOutdatedError,
    PrivacyNoticeUnavailableError,
)
from app.modules.identity.models import PrivacyNoticeAcceptance, PrivacyNoticeVersion
from app.modules.identity.schemas import PrivacyNotice

ACCEPTANCE_UNIQUE_CONSTRAINT = "uq_privacy_notice_acceptances_user_id"


def _current_version_id(now: datetime) -> ColumnElement[uuid.UUID]:
    """A scalar subquery: the id of the current version at ``now``, or
    ``NULL`` when none is published."""
    return (
        select(PrivacyNoticeVersion.id)
        .where(PrivacyNoticeVersion.published_at <= now)
        .order_by(PrivacyNoticeVersion.version.desc())
        .limit(1)
        .scalar_subquery()
    )


def accepted_current_notice(
    user_id: uuid.UUID | ColumnElement[uuid.UUID] | InstrumentedAttribute[uuid.UUID],
) -> ColumnElement[bool]:
    """A boolean SQL expression: whether ``user_id`` accepted the version that
    is current now. False when no version is published (fail closed). Meant
    for a column of an existing statement (``load_principal``)."""
    return exists().where(
        PrivacyNoticeAcceptance.user_id == user_id,
        PrivacyNoticeAcceptance.notice_version_id == _current_version_id(clock.now()),
    )


async def current_privacy_notice(session: AsyncSession) -> PrivacyNotice:
    """The current privacy-notice version (one statement).

    Raises :class:`PrivacyNoticeUnavailableError` when none is published.
    """
    row = await session.scalar(
        select(PrivacyNoticeVersion)
        .where(PrivacyNoticeVersion.published_at <= clock.now())
        .order_by(PrivacyNoticeVersion.version.desc())
        .limit(1)
    )
    if row is None:
        raise PrivacyNoticeUnavailableError()
    return PrivacyNotice.model_validate(row)


async def find_privacy_notice_acceptance(
    session: AsyncSession, *, user_id: uuid.UUID, notice_version_id: uuid.UUID
) -> PrivacyNoticeAcceptance | None:
    """The user's acceptance of one version, or ``None``."""
    acceptance: PrivacyNoticeAcceptance | None = await session.scalar(
        select(PrivacyNoticeAcceptance).where(
            PrivacyNoticeAcceptance.user_id == user_id,
            PrivacyNoticeAcceptance.notice_version_id == notice_version_id,
        )
    )
    return acceptance


async def accept_privacy_notice(
    session: AsyncSession, *, user_id: uuid.UUID, version: int
) -> PrivacyNoticeAcceptance:
    """Record that ``user_id`` accepts the current version ``version``.

    - ``version`` is not the current version (older, unknown or not yet
      published): :class:`PrivacyNoticeOutdatedError`, nothing written;
    - already accepted: the existing acceptance, nothing written;
    - otherwise a new acceptance at ``clock.now()`` (audited as a ``create``
      event of ``privacy_notice_acceptances``). A concurrent acceptance of the
      same version is caught by the unique constraint and returns the row
      that won.
    """
    current = await current_privacy_notice(session)
    if version != current.version:
        raise PrivacyNoticeOutdatedError()
    existing = await find_privacy_notice_acceptance(
        session, user_id=user_id, notice_version_id=current.id
    )
    if existing is not None:
        return existing
    acceptance = PrivacyNoticeAcceptance(
        user_id=user_id, notice_version_id=current.id, accepted_at=clock.now()
    )
    try:
        async with session.begin_nested():
            session.add(acceptance)
            await session.flush()
    except IntegrityError as error:
        if ACCEPTANCE_UNIQUE_CONSTRAINT not in str(error.orig):
            raise
        winner = await find_privacy_notice_acceptance(
            session, user_id=user_id, notice_version_id=current.id
        )
        if winner is None:  # pragma: no cover - the constraint names it
            raise
        return winner
    return acceptance


async def privacy_notice_versions(
    session: AsyncSession, version_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """The version number of each id in ``version_ids`` (one statement; none
    for an empty set). For records that stamp a version (#35 consents)."""
    ids = set(version_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(PrivacyNoticeVersion.id, PrivacyNoticeVersion.version).where(
            PrivacyNoticeVersion.id.in_(ids)
        )
    )
    return {row.id: row.version for row in rows}


async def has_accepted_current_privacy_notice(
    session: AsyncSession, *, user_id: uuid.UUID
) -> bool:
    """Whether ``user_id`` accepted the current version (one statement; false
    when none is published)."""
    accepted = await session.scalar(select(accepted_current_notice(user_id)))
    return bool(accepted)
