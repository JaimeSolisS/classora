"""Availability change requests (M4-18 #63 A1, C, D6, G; spec §3: "Changes
affecting existing assignments require administrator approval").

**Submitting** (:func:`submit_change_request`, called by the three
availability writes of :mod:`app.modules.staff.tutor_availability` when the
impact source answers one or more affected sessions): nothing in the
availability tables changes; a ``tutor_availability_change_requests`` row
holds the change (``payload``), the snapshot of the affected sessions and
the inbox item, created first with
``administration.service.create_approval_request`` (type
``staff.availability_change``, subject the request's id, requester the
caller, priority ``high`` when the earliest affected session starts within
24 hours: :func:`~app.modules.staff.availability_impact.change_priority`).
The write answers ``202`` with :class:`AvailabilityChangeRequestItem`.
Several pending requests of one tutor may wait together.

**Withdrawing** (:func:`cancel_change_request`, ``POST
…/availability/change-requests/{id}/cancel``): ``404`` (the tutor is not
visible, or the id is not one of this tutor's) → ``403`` (neither the tutor
themselves nor a holder of ``tutor.manage``) → ``409 version_conflict`` →
``409 availability_change_not_pending`` (``details.status``) →
``cancelled`` and the inbox item cancelled (``by_user_id`` = the caller).
**Lock order:** the inbox item first (the order of an inbox decision, which
locks the approval request and then, in the handler, this row), then this
row; both checks run again under the lock, so a withdrawal racing a
decision is one ``409``.

**Deciding** is the ``staff.availability_change`` handler's
(``app/workflows/availability_change.py``); this module gives it the row
(:func:`lock_change_request`), :func:`finish_change_request`, the
notifications (:func:`notify_change_decided`, F1) and the inbox summaries
(:func:`change_request_summaries`, two statements).

**Reads** (:func:`change_requests_of`): the tutor's pending requests
(oldest first) and the last five resolved ones (newest first), in two
statements (the rows, then the requesters' names). The affected sessions
are the snapshot stored at submission (its first 20 in display form), so
``staff`` never reads ``scheduling``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.identity import service as identity
from app.modules.identity.schemas import AccountReference
from app.modules.staff.assignment_impact import AffectedSession
from app.modules.staff.availability_impact import change_priority
from app.modules.staff.errors import AvailabilityChangeNotPendingError
from app.modules.staff.models import (
    CHANGE_APPROVED,
    CHANGE_CANCELLED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    CHANGE_WEEKLY,
    TutorAvailabilityChangeRequest,
)
from app.modules.staff.permissions import can_edit_availability
from app.modules.staff.qualifications import visible_tutor
from app.modules.staff.schemas import (
    AFFECTED_SESSIONS_SHOWN,
    AffectedSessionItem,
    AvailabilityChange,
    AvailabilityChangeException,
    AvailabilityChangeRequester,
    AvailabilityChangeRequestItem,
)

AVAILABILITY_CHANGE_TYPE: Final = "staff.availability_change"
"""The approval type (its handler lives in ``app/workflows``)."""

AVAILABILITY_CHANGE_APPROVED: Final = "staff.availability_change_approved"
AVAILABILITY_CHANGE_REJECTED: Final = "staff.availability_change_rejected"

RECENT_CHANGES: Final = 5


class AvailabilityChangeDecisionParams(BaseModel):
    """The params of the two decision notifications (F1; never the reason:
    the tutor reads it in the availability section)."""

    change_request_id: uuid.UUID
    kind: str
    affected_count: int


def register_availability_notifications() -> None:
    """Register the two decision notification types (again: a no-op)."""
    for code in (AVAILABILITY_CHANGE_APPROVED, AVAILABILITY_CHANGE_REJECTED):
        administration.register_notification_type(
            code, AvailabilityChangeDecisionParams
        )


# --- Display -------------------------------------------------------------------------


def _snapshot(affected: Sequence[AffectedSession]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.id),
            "starts_at": item.starts_at.astimezone(UTC).isoformat(),
            "ends_at": item.ends_at.astimezone(UTC).isoformat(),
            "subject_name": item.subject_name,
            "state": item.state,
        }
        for item in affected[:AFFECTED_SESSIONS_SHOWN]
    ]


def change_display(row: TutorAvailabilityChangeRequest) -> AvailabilityChange:
    """The payload in display form (G1, D2)."""
    payload = row.payload
    if row.kind == CHANGE_WEEKLY:
        return AvailabilityChange(
            windows=[AvailabilityWindow(**item) for item in payload["windows"]],
            exception=None,
        )
    if "exception_id" in payload:
        return AvailabilityChange(
            windows=None,
            exception=AvailabilityChangeException(
                id=payload["exception_id"], **payload["exception"]
            ),
        )
    return AvailabilityChange(
        windows=None, exception=AvailabilityChangeException(id=None, **payload)
    )


def _requester(
    user_id: uuid.UUID, accounts: Mapping[uuid.UUID, AccountReference]
) -> AvailabilityChangeRequester:
    account = accounts.get(user_id)
    return AvailabilityChangeRequester(
        id=user_id, display_name=account.display_name if account else ""
    )


def change_request_item(
    row: TutorAvailabilityChangeRequest, accounts: Mapping[uuid.UUID, AccountReference]
) -> AvailabilityChangeRequestItem:
    return AvailabilityChangeRequestItem(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]  # CHECK-constrained
        status=row.status,  # type: ignore[arg-type]  # CHECK-constrained
        change=change_display(row),
        affected_count=len(row.affected_session_ids),
        affected_sessions=[
            AffectedSessionItem.model_validate(item) for item in row.affected_sessions
        ],
        requested_by=_requester(row.requested_by_user_id, accounts),
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        rejection_reason=row.rejection_reason,
        approval_request_id=row.approval_request_id,
        version=row.version,
    )


# --- Submit (C1, C2) -----------------------------------------------------------------


async def submit_change_request(
    session: AsyncSession,
    *,
    tutor_user_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    affected: Sequence[AffectedSession],
) -> AvailabilityChangeRequestItem:
    """Store a change that affects ``affected`` (non-empty, by start) as a
    pending request with its inbox item (see the module docstring)."""
    if not affected:
        raise ValueError("a change request needs at least one affected session")
    request_id = uuid.uuid7()
    approval = await administration.create_approval_request(
        session,
        request_type=AVAILABILITY_CHANGE_TYPE,
        subject_id=request_id,
        requester_user_id=requested_by_user_id,
        priority=change_priority((item.starts_at for item in affected), clock.now()),
    )
    row = TutorAvailabilityChangeRequest(
        id=request_id,
        tutor_user_id=tutor_user_id,
        requested_by_user_id=requested_by_user_id,
        kind=kind,
        status=CHANGE_PENDING,
        payload=payload,
        affected_session_ids=[item.id for item in affected],
        affected_sessions=_snapshot(affected),
        approval_request_id=approval.id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, ["created_at", "version"])
    accounts = await identity.get_account_references(session, [requested_by_user_id])
    return change_request_item(row, accounts)


# --- Reads (G2) ----------------------------------------------------------------------


async def change_requests_of(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> tuple[list[AvailabilityChangeRequestItem], list[AvailabilityChangeRequestItem]]:
    """The tutor's pending requests (oldest first) and the last
    :data:`RECENT_CHANGES` resolved ones (newest first); two statements."""
    model = TutorAvailabilityChangeRequest
    recent = (
        select(model.id)
        .where(model.tutor_user_id == tutor_user_id)
        .where(model.status != CHANGE_PENDING)
        .order_by(model.resolved_at.desc(), model.id.desc())
        .limit(RECENT_CHANGES)
    )
    rows = list(
        await session.scalars(
            select(model)
            .where(model.tutor_user_id == tutor_user_id)
            .where(or_(model.status == CHANGE_PENDING, model.id.in_(recent)))
        )
    )
    accounts = await identity.get_account_references(
        session, {row.requested_by_user_id for row in rows}
    )
    pending = sorted(
        (row for row in rows if row.status == CHANGE_PENDING),
        key=lambda row: (row.created_at, row.id),
    )
    resolved = sorted(
        (row for row in rows if row.status != CHANGE_PENDING),
        key=lambda row: (row.resolved_at, row.id),
        reverse=True,
    )
    return (
        [change_request_item(row, accounts) for row in pending],
        [change_request_item(row, accounts) for row in resolved],
    )


# --- Withdraw (D6) -------------------------------------------------------------------


async def _locked(
    session: AsyncSession, change_request_id: uuid.UUID
) -> TutorAvailabilityChangeRequest | None:
    row: TutorAvailabilityChangeRequest | None = await session.scalar(
        select(TutorAvailabilityChangeRequest)
        .where(TutorAvailabilityChangeRequest.id == change_request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def cancel_change_request(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    change_request_id: uuid.UUID,
    version: int,
) -> AvailabilityChangeRequestItem:
    """Withdraw a pending request (see the module docstring for the order)."""
    member = await visible_tutor(session, principal, user_id)
    row = await session.scalar(
        select(TutorAvailabilityChangeRequest)
        .where(TutorAvailabilityChangeRequest.id == change_request_id)
        .where(TutorAvailabilityChangeRequest.tutor_user_id == member.id)
    )
    if row is None:
        raise RecordNotFoundError()
    if not can_edit_availability(principal, member.id):
        raise PermissionDeniedError()
    ensure_version(row, version)
    if row.status != CHANGE_PENDING:
        raise AvailabilityChangeNotPendingError(row.status)
    try:
        await administration.cancel_approval_request(
            session, request_id=row.approval_request_id, by_user_id=principal.user_id
        )
    except administration.ApprovalTransitionInvalidError:
        # A decision got there first: report the request's own status.
        decided = await _locked(session, change_request_id)
        raise AvailabilityChangeNotPendingError(
            decided.status if decided is not None else CHANGE_CANCELLED
        ) from None
    locked = await _locked(session, change_request_id)
    if locked is None:  # pragma: no cover - rows are never deleted
        raise RecordNotFoundError()
    ensure_version(locked, version)
    if locked.status != CHANGE_PENDING:  # pragma: no cover - the item was pending
        raise AvailabilityChangeNotPendingError(locked.status)
    finish_change_request(locked, CHANGE_CANCELLED)
    await session.flush()
    await session.refresh(locked, ["version", "updated_at"])
    accounts = await identity.get_account_references(
        session, [locked.requested_by_user_id]
    )
    return change_request_item(locked, accounts)


# --- For the decision handler (D3, D4) ---------------------------------------------


async def lock_change_request(
    session: AsyncSession, change_request_id: uuid.UUID
) -> TutorAvailabilityChangeRequest | None:
    """The request ``FOR UPDATE`` (re-read), or ``None``."""
    return await _locked(session, change_request_id)


def finish_change_request(
    row: TutorAvailabilityChangeRequest, status: str, *, reason: str | None = None
) -> None:
    """Move a pending request to ``approved`` | ``rejected`` (with
    ``reason``) | ``cancelled`` (the caller flushes)."""
    if row.status != CHANGE_PENDING:
        raise ValueError("only a pending request can be resolved")
    if (status == CHANGE_REJECTED) != (reason is not None):
        raise ValueError("a rejection, and only a rejection, takes a reason")
    if status not in (CHANGE_APPROVED, CHANGE_REJECTED, CHANGE_CANCELLED):
        raise ValueError(f"not a final status: {status!r}")
    row.status = status
    row.rejection_reason = reason
    row.resolved_at = clock.now()


async def notify_change_decided(
    session: AsyncSession,
    row: TutorAvailabilityChangeRequest,
    *,
    affected_count: int,
) -> None:
    """F1: the decision to the tutor (whoever submitted), without the
    reason."""
    await administration.create_notification(
        session,
        recipient_user_id=row.tutor_user_id,
        notification_type=(
            AVAILABILITY_CHANGE_APPROVED
            if row.status == CHANGE_APPROVED
            else AVAILABILITY_CHANGE_REJECTED
        ),
        params=AvailabilityChangeDecisionParams(
            change_request_id=row.id, kind=row.kind, affected_count=affected_count
        ),
    )


async def change_request_summaries(
    session: AsyncSession, change_request_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Mapping[str, Any]]:
    """The inbox display data by request id (D2), in two statements:
    ``{tutor {id, display_name}, kind, change, affected_count,
    affected_sessions, change_request_status}``."""
    ids = set(change_request_ids)
    if not ids:
        return {}
    rows = list(
        await session.scalars(
            select(TutorAvailabilityChangeRequest).where(
                TutorAvailabilityChangeRequest.id.in_(ids)
            )
        )
    )
    accounts = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows}
    )
    return {
        row.id: {
            "tutor": {
                "id": str(row.tutor_user_id),
                "display_name": _requester(row.tutor_user_id, accounts).display_name,
            },
            "kind": row.kind,
            "change": change_display(row).model_dump(mode="json"),
            "affected_count": len(row.affected_session_ids),
            "affected_sessions": row.affected_sessions,
            "change_request_status": row.status,
        }
        for row in rows
    }
