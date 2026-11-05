"""The approval inbox core (#40; spec §9; architecture §4.1).

Every approval workflow plugs into one table, ``approval_requests``
(:mod:`app.modules.administration.models`), through a **handler** that its
module registers at app build (:func:`register_approval_handler`). Other
modules reach all of this through ``administration.service`` (ADR-0002).

**Handlers** (:class:`ApprovalHandler`): one per ``request_type``. The
handler's ``approve``, ``reject`` and ``request_changes`` run in the caller's
Unit of Work, after the request row is locked and before its status changes,
so a handler error (any ``AppError``) rolls the whole decision back and the
request stays as it was. ``review_permission`` decides who sees and decides
the type's requests; ``summaries`` returns the display data of the list.

**State machine** (:func:`transition_allowed`): ``pending → approved |
rejected | changes_requested | cancelled``; ``changes_requested → pending
(resubmit) | rejected | cancelled``; ``approved``, ``rejected`` and
``cancelled`` are final. Any other move is ``409
approval_transition_invalid``.

**Decision order** (:func:`decide_approval_request`): the row ``FOR UPDATE``
→ ``404 not_found`` when it's missing, its type is unregistered or the caller
lacks the type's review permission (hidden looks like missing, as the #21
record policies) → only through the generic inbox routes (``via_inbox``), for
a type decided on its own page (``inbox_decisions = False``) whose handler
defines ``inbox_route_error``: the error it returns, whatever the request's
status (#312) → ``409 approval_action_unsupported`` (``request-changes`` on
a type without it) → ``409 approval_transition_invalid`` → ``422`` on
``reason`` → the handler → status, ``resolved_*``, ``reason`` and ``version``
→ flush.

**Names** (requester, assignee, resolver) come from the account-reference
resolver that the composition roots register
(:func:`register_account_reference_resolver`; ``identity.service.
get_account_references``): ``administration`` imports no other module.
Without one, building a list item fails closed (:class:`AccountResolverMissingError`).

**Assignment** (#118; :func:`assign_approval_request`): anyone who may review
a request's type may assign it, reassign it or unassign it, to any **active**
user who holds the type's review permission now (the review-candidate
resolver the composition roots register,
:func:`register_review_candidate_resolver`; without one it fails closed with
:class:`ReviewCandidateResolverMissingError`). Order: the row ``FOR UPDATE``
→ ``404 not_found`` (as a decision) → ``409 version_conflict`` → ``409
approval_request_closed`` (only ``pending`` and ``changes_requested`` can be
assigned) → ``422 approval_assignee_ineligible`` → the write. Assigning the
current assignee changes nothing. The new assignee gets an
``administration.approval_assigned`` notification when someone else assigned
them. Decisions, cancels, resubmits and reopens keep the assignee.

Every function runs in the caller's Unit of Work and never commits.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Protocol

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PERMISSION_CODE_PATTERN, Principal, RecordNotFoundError
from app.core.errors import AppError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration.errors import (
    ApprovalActionUnsupportedError,
    ApprovalAssigneeIneligibleError,
    ApprovalReasonInvalidError,
    ApprovalRequestClosedError,
    ApprovalTransitionInvalidError,
)
from app.modules.administration.models import (
    APPROVAL_PRIORITIES,
    APPROVED,
    CANCELLED,
    CHANGES_REQUESTED,
    FINAL_STATUSES,
    HIGH,
    LOW,
    NORMAL,
    PENDING,
    REASON_MAX_LENGTH,
    REJECTED,
    REQUEST_TYPE_MAX_LENGTH,
    REQUEST_TYPE_PATTERN,
    ApprovalRequest,
)
from app.modules.administration.notifications import (
    create_notification,
    register_notification_type,
)
from app.modules.administration.schemas import (
    AccountName,
    ApprovalAction,
    ApprovalAssigneeFilter,
    ApprovalPriority,
    ApprovalRequestItem,
    ApprovalSort,
    ApprovalStatusFilter,
)

# --- The state machine (B2) ------------------------------------------------------

TRANSITIONS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        PENDING: frozenset({APPROVED, REJECTED, CHANGES_REQUESTED, CANCELLED}),
        CHANGES_REQUESTED: frozenset({PENDING, REJECTED, CANCELLED}),
        APPROVED: frozenset(),
        REJECTED: frozenset(),
        CANCELLED: frozenset(),
    }
)
"""The allowed moves, by current status."""

DECISION_TARGETS: Final[Mapping[ApprovalAction, str]] = MappingProxyType(
    {
        "approve": APPROVED,
        "reject": REJECTED,
        "request_changes": CHANGES_REQUESTED,
    }
)
"""The status each reviewer decision moves a request to."""

OPEN_STATUSES: Final = (PENDING, CHANGES_REQUESTED)


def transition_allowed(current: str, target: str) -> bool:
    """Whether a request in ``current`` may move to ``target`` (pure)."""
    return target in TRANSITIONS.get(current, frozenset())


def ensure_transition(current: str, target: str) -> None:
    """``409 approval_transition_invalid`` unless the move is allowed."""
    if not transition_allowed(current, target):
        raise ApprovalTransitionInvalidError()


# --- Handlers (B1) ---------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalRequestView:
    """What a handler knows about the request it decides (read-only: the core
    alone changes the request's status)."""

    id: uuid.UUID
    request_type: str
    subject_id: uuid.UUID
    requester_user_id: uuid.UUID | None
    status: str


class ApprovalHandler(Protocol):
    """One approval type's behaviour, registered with
    :func:`register_approval_handler`.

    - ``request_type``: the dotted code (``<module>.<thing>``).
    - ``review_permission``: the permission code that lets a user see and
      decide this type's requests.
    - ``supports_request_changes``: whether reviewers may send a request back
      to its requester; ``request_changes`` is called only when it does.
    - ``reason_required_on_reject``: whether a rejection needs a reason.

    The actions run in the caller's Unit of Work after the request row is
    locked (lock the subject inside the action), raise an ``AppError`` to
    refuse, and never commit. ``summaries`` returns display data by subject
    id (JSON values, no secrets) in a fixed number of statements; a missing
    subject is left out.

    Optional attributes, read with ``getattr`` (absent = the default):

    - ``inbox_decisions`` (``True``): ``False`` when the type's items are
      decided on their own page (see :func:`_inbox_decisions`).
    - ``inbox_route_error`` (#312): ``async (session, request:
      ApprovalRequestView) -> AppError``, meaningful only with
      ``inbox_decisions = False``. A generic inbox decision
      (``via_inbox=True``) raises what it returns right after the ``404``
      check, before the action and status checks, so the inbox answers the
      type's "decide it on its page" error whatever the request's status. It
      must not change anything. Without it the generic routes follow the
      usual order (the handler's own ``409`` reaches only pending items).
    """

    @property
    def request_type(self) -> str: ...

    @property
    def review_permission(self) -> str: ...

    @property
    def supports_request_changes(self) -> bool: ...

    @property
    def reason_required_on_reject(self) -> bool: ...

    async def approve(
        self, session: AsyncSession, request: ApprovalRequestView, principal: Principal
    ) -> None: ...

    async def reject(
        self,
        session: AsyncSession,
        request: ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None: ...

    async def request_changes(
        self,
        session: AsyncSession,
        request: ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None: ...

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]: ...


_handlers: dict[str, ApprovalHandler] = {}
_TYPE_FORMAT: Final = re.compile(REQUEST_TYPE_PATTERN)


def register_approval_handler(handler: ApprovalHandler) -> None:
    """Register ``handler`` for its ``request_type``.

    Raises ``ValueError`` when the type's format or the review permission's
    format is wrong, or another handler holds the type. Registering the same
    handler again changes nothing, so building the app more than once in a
    process (tests, the OpenAPI export) is safe.
    """
    request_type = handler.request_type
    if (
        len(request_type) > REQUEST_TYPE_MAX_LENGTH
        or _TYPE_FORMAT.fullmatch(request_type) is None
    ):
        raise ValueError(f"not an approval request type: {request_type!r}")
    if PERMISSION_CODE_PATTERN.fullmatch(handler.review_permission) is None:
        raise ValueError(f"not a permission code: {handler.review_permission!r}")
    registered = _handlers.get(request_type)
    if registered is not None and registered is not handler:
        raise ValueError(f"approval type already registered: {request_type!r}")
    _handlers[request_type] = handler


def unregister_approval_handler(request_type: str) -> None:
    """Remove a registered handler (tests only); an unknown type is ignored."""
    _handlers.pop(request_type, None)


def registered_approval_types() -> frozenset[str]:
    """The registered request types."""
    return frozenset(_handlers)


def _reviewable_types(principal: Principal) -> list[str]:
    return sorted(
        request_type
        for request_type, handler in _handlers.items()
        if principal.has(handler.review_permission)
    )


# --- Account names (E4) ----------------------------------------------------------


class AccountReferenceLike(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def display_name(self) -> str: ...


type AccountReferenceResolver = Callable[
    [AsyncSession, Iterable[uuid.UUID]],
    Awaitable[Mapping[uuid.UUID, AccountReferenceLike]],
]
"""Resolves user ids to references (one statement; unknown ids left out)."""


class AccountResolverMissingError(RuntimeError):
    """No account-reference resolver is registered: building an approval item
    fails closed (a ``500``) instead of showing ids without names."""


_account_resolver: AccountReferenceResolver | None = None


def register_account_reference_resolver(
    resolver: AccountReferenceResolver | None,
) -> None:
    """Register the resolver for this process (``None`` unregisters it; tests
    only). The API app factory, ``app.cli`` and ``app.worker`` register
    ``identity.service.get_account_references``."""
    global _account_resolver
    _account_resolver = resolver


def registered_account_reference_resolver() -> AccountReferenceResolver | None:
    """The registered resolver, or ``None``."""
    return _account_resolver


async def _account_names(
    session: AsyncSession, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, AccountName]:
    resolver = _account_resolver
    if resolver is None:
        raise AccountResolverMissingError(
            "No account-reference resolver is registered."
        )
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    references = await resolver(session, ids)
    return {
        user_id: AccountName(id=reference.id, display_name=reference.display_name)
        for user_id, reference in references.items()
    }


# --- Review candidates (#118 B2) -------------------------------------------------

type ReviewCandidateResolver = Callable[
    [AsyncSession, str], Awaitable[Sequence[AccountReferenceLike]]
]
"""The **active** users who hold a permission now (through a role or an
active delegation), as account references, in a fixed number of
statements."""


class ReviewCandidateResolverMissingError(RuntimeError):
    """No review-candidate resolver is registered: the assignee options and
    the assignment's eligibility check fail closed (a ``500``)."""


_candidate_resolver: ReviewCandidateResolver | None = None


def register_review_candidate_resolver(
    resolver: ReviewCandidateResolver | None,
) -> None:
    """Register the resolver for this process (``None`` unregisters it; tests
    only). ``app.wiring.register_approval_wiring`` registers one composed from
    ``identity.service.active_user_ids_with_permission`` and
    ``get_account_references``."""
    global _candidate_resolver
    _candidate_resolver = resolver


def registered_review_candidate_resolver() -> ReviewCandidateResolver | None:
    """The registered resolver, or ``None``."""
    return _candidate_resolver


async def _review_candidates(
    session: AsyncSession, permission: str
) -> list[AccountName]:
    """The assignable users for ``permission``, by display name then id. The
    assignee options and the eligibility check both read this (B3)."""
    resolver = _candidate_resolver
    if resolver is None:
        raise ReviewCandidateResolverMissingError(
            "No review-candidate resolver is registered."
        )
    references = await resolver(session, permission)
    return sorted(
        (
            AccountName(id=reference.id, display_name=reference.display_name)
            for reference in references
        ),
        key=lambda name: (name.display_name, name.id),
    )


# --- The assignment notification (#118 E5) ---------------------------------------

APPROVAL_ASSIGNED: Final = "administration.approval_assigned"


class ApprovalAssignedParams(BaseModel):
    """The params of ``administration.approval_assigned``: ids and the type
    code only (no names, no subject data)."""

    request_id: uuid.UUID
    request_type: str


def register_approval_notifications() -> None:
    """Register ``administration.approval_assigned`` (again: a no-op)."""
    register_notification_type(APPROVAL_ASSIGNED, ApprovalAssignedParams)


# --- Creating and requester actions (B1) -----------------------------------------


async def create_approval_request(
    session: AsyncSession,
    *,
    request_type: str,
    subject_id: uuid.UUID,
    requester_user_id: uuid.UUID | None,
    priority: str = NORMAL,
    assignee_user_id: uuid.UUID | None = None,
) -> ApprovalRequest:
    """A new ``pending`` request for ``subject_id``, flushed (not committed).

    ``request_type`` must be registered and ``priority`` one of ``low``,
    ``normal`` and ``high`` (``ValueError`` otherwise: a programming error).
    A second request for the same subject violates
    ``uq_approval_requests_request_type`` (one request per subject).
    """
    if request_type not in _handlers:
        raise ValueError(f"approval type not registered: {request_type!r}")
    if priority not in APPROVAL_PRIORITIES:
        raise ValueError(f"not an approval priority: {priority!r}")
    request = ApprovalRequest(
        id=uuid.uuid7(),
        request_type=request_type,
        subject_id=subject_id,
        requester_user_id=requester_user_id,
        assignee_user_id=assignee_user_id,
        status=PENDING,
        priority=priority,
    )
    session.add(request)
    await session.flush()
    return request


async def open_approval_request(
    session: AsyncSession,
    *,
    request_type: str,
    subject_id: uuid.UUID,
    requester_user_id: uuid.UUID | None,
    priority: str = NORMAL,
    assignee_user_id: uuid.UUID | None = None,
) -> ApprovalRequest:
    """The subject's request of ``request_type``, ``pending``, flushed (not
    committed), for a subject that can need a decision more than once (#232
    QA D1: an occurrence that returns to ``conflict``).

    There is at most one request per subject
    (``uq_approval_requests_request_type``), so:

    - none yet → :func:`create_approval_request`;
    - one already ``pending`` → kept, with ``requester_user_id`` and
      ``priority`` refreshed;
    - one in any other status (decided, cancelled or ``changes_requested``)
      → **reopened**: back to ``pending`` with those fields refreshed and
      ``reason``, ``resolved_at`` and ``resolved_by_user_id`` cleared. This
      is a system move outside :data:`TRANSITIONS` (which governs the
      reviewer and requester actions); the earlier decision stays in the
      audit log (the flush writes the ``update`` event and bumps
      ``version``).

    An existing row keeps its ``assignee_user_id`` (#118 E4): a returned or
    resubmitted item goes back to whoever was handling it.
    ``assignee_user_id`` is used only when the request is created.

    The row is locked ``FOR UPDATE`` first. ``ValueError`` as
    :func:`create_approval_request`. Creating a request sends no
    notification, so neither does reopening one: the inbox lists it again
    because it is ``pending``.
    """
    if request_type not in _handlers:
        raise ValueError(f"approval type not registered: {request_type!r}")
    if priority not in APPROVAL_PRIORITIES:
        raise ValueError(f"not an approval priority: {priority!r}")
    request: ApprovalRequest | None = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.request_type == request_type)
        .where(ApprovalRequest.subject_id == subject_id)
        .with_for_update()
    )
    if request is None:
        return await create_approval_request(
            session,
            request_type=request_type,
            subject_id=subject_id,
            requester_user_id=requester_user_id,
            priority=priority,
            assignee_user_id=assignee_user_id,
        )
    request.requester_user_id = requester_user_id
    request.priority = priority
    if request.status != PENDING:
        request.status = PENDING
        request.reason = None
        request.resolved_at = None
        request.resolved_by_user_id = None
    await session.flush()
    return request


async def approval_request_id_for_subject(
    session: AsyncSession, *, request_type: str, subject_id: uuid.UUID
) -> uuid.UUID | None:
    """The id of the request of ``request_type`` about ``subject_id`` (there
    is at most one), or ``None``. For an owning module that acts on the
    request through its own record (#44: a tutor cancels a qualification).
    One statement, no lock."""
    found: uuid.UUID | None = await session.scalar(
        select(ApprovalRequest.id)
        .where(ApprovalRequest.request_type == request_type)
        .where(ApprovalRequest.subject_id == subject_id)
    )
    return found


async def _locked(session: AsyncSession, request_id: uuid.UUID) -> ApprovalRequest:
    # Locked: two decisions on one request run one after the other, and the
    # second sees the first one's status.
    request: ApprovalRequest | None = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise RecordNotFoundError()
    return request


def _resolve(request: ApprovalRequest, by_user_id: uuid.UUID | None) -> None:
    request.resolved_at = clock.now()
    request.resolved_by_user_id = by_user_id


async def cancel_approval_request(
    session: AsyncSession, *, request_id: uuid.UUID, by_user_id: uuid.UUID | None
) -> ApprovalRequest:
    """Cancel an open request (the requester withdraws it, or the system:
    ``by_user_id=None``). ``404 not_found`` when unknown, ``409
    approval_transition_invalid`` unless ``pending`` or ``changes_requested``.
    The owning module checks that the caller may cancel it."""
    request = await _locked(session, request_id)
    ensure_transition(request.status, CANCELLED)
    request.status = CANCELLED
    _resolve(request, by_user_id)
    await session.flush()
    return request


async def resubmit_approval_request(
    session: AsyncSession, *, request_id: uuid.UUID, by_user_id: uuid.UUID | None
) -> ApprovalRequest:
    """Send a ``changes_requested`` request back to ``pending`` (the requester
    made the changes). ``404`` and ``409`` as :func:`cancel_approval_request`.
    The reviewer's earlier reason is cleared (the audit log keeps it).
    ``by_user_id`` is not stored: the Unit of Work's actor is the audit
    row's actor, and an open request has no resolver."""
    del by_user_id
    request = await _locked(session, request_id)
    ensure_transition(request.status, PENDING)
    request.status = PENDING
    request.reason = None
    await session.flush()
    return request


# --- Reviewer decisions (B3) -----------------------------------------------------


def _clean_reason(reason: str | None, *, required: bool) -> str | None:
    cleaned = reason.strip() if reason is not None else ""
    if not cleaned:
        if required:
            raise ApprovalReasonInvalidError("missing", "Give a reason.")
        return None
    if len(cleaned) > REASON_MAX_LENGTH:
        raise ApprovalReasonInvalidError(
            "string_too_long",
            f"The reason can be at most {REASON_MAX_LENGTH} characters long.",
        )
    return cleaned


def _view(request: ApprovalRequest) -> ApprovalRequestView:
    return ApprovalRequestView(
        id=request.id,
        request_type=request.request_type,
        subject_id=request.subject_id,
        requester_user_id=request.requester_user_id,
        status=request.status,
    )


async def decide_approval_request(
    session: AsyncSession,
    *,
    principal: Principal,
    request_id: uuid.UUID,
    action: ApprovalAction,
    reason: str | None = None,
    via_inbox: bool = False,
) -> ApprovalRequestItem:
    """Approve, reject or request changes, in the order of the module
    docstring; returns the updated item. ``via_inbox`` is set only by the
    generic inbox routes (#312): a type's own pages, which resolve the item
    through this function, keep the default."""
    request = await _locked(session, request_id)
    handler = _handlers.get(request.request_type)
    if handler is None or not principal.has(handler.review_permission):
        raise RecordNotFoundError()
    if via_inbox:
        route_error = _inbox_route_error(handler)
        if route_error is not None:
            raise await route_error(session, _view(request))
    if action == "request_changes" and not handler.supports_request_changes:
        raise ApprovalActionUnsupportedError()
    target = DECISION_TARGETS[action]
    ensure_transition(request.status, target)
    view = _view(request)
    if action == "approve":
        await handler.approve(session, view, principal)
        cleaned = None
    elif action == "reject":
        cleaned = _clean_reason(reason, required=handler.reason_required_on_reject)
        await handler.reject(session, view, principal, cleaned)
    else:
        cleaned = _clean_reason(reason, required=True)
        assert cleaned is not None  # noqa: S101  (required above)
        await handler.request_changes(session, view, principal, cleaned)
    request.status = target
    request.reason = cleaned
    if target in FINAL_STATUSES:
        _resolve(request, principal.user_id)
    await session.flush()
    [item] = await _items(session, [request])
    return item


# --- Assignment (#118 A, B) ------------------------------------------------------


def _reviewing_handler(
    request: ApprovalRequest, principal: Principal
) -> ApprovalHandler:
    """The request's handler; ``404`` when its type is unregistered or the
    caller may not review it (hidden looks like missing)."""
    handler = _handlers.get(request.request_type)
    if handler is None or not principal.has(handler.review_permission):
        raise RecordNotFoundError()
    return handler


async def assign_approval_request(
    session: AsyncSession,
    *,
    principal: Principal,
    request_id: uuid.UUID,
    assignee_user_id: uuid.UUID | None,
    version: int,
) -> ApprovalRequestItem:
    """Assign, reassign or unassign (``None``) an open request, in the order
    of the module docstring; returns the item. Never calls the handler, and
    changes nothing but ``assignee_user_id`` (and so ``version``)."""
    request = await _locked(session, request_id)
    handler = _reviewing_handler(request, principal)
    ensure_version(request, version)
    if request.status not in OPEN_STATUSES:
        raise ApprovalRequestClosedError()
    if assignee_user_id is not None:
        candidates = await _review_candidates(session, handler.review_permission)
        if all(candidate.id != assignee_user_id for candidate in candidates):
            raise ApprovalAssigneeIneligibleError()
    if request.assignee_user_id != assignee_user_id:
        request.assignee_user_id = assignee_user_id
        await session.flush()
        if assignee_user_id is not None and assignee_user_id != principal.user_id:
            await create_notification(
                session,
                recipient_user_id=assignee_user_id,
                notification_type=APPROVAL_ASSIGNED,
                params=ApprovalAssignedParams(
                    request_id=request.id, request_type=request.request_type
                ),
            )
    [item] = await _items(session, [request])
    return item


async def list_assignee_options(
    session: AsyncSession, *, principal: Principal, request_id: uuid.UUID
) -> list[AccountName]:
    """The users the request can be assigned to now (the caller included
    when eligible), by display name then id; ``404`` as
    :func:`get_approval_request`. Any status (it only reads); three
    statements."""
    request: ApprovalRequest | None = await session.get(ApprovalRequest, request_id)
    if request is None:
        raise RecordNotFoundError()
    handler = _reviewing_handler(request, principal)
    return await _review_candidates(session, handler.review_permission)


# --- Reading (C1-C3) -------------------------------------------------------------


def _inbox_decisions(handler: ApprovalHandler) -> bool:
    """Whether the inbox offers the type's decisions (#60 E1). A handler may
    set ``inbox_decisions = False`` when its items are decided on their own
    page (the generic routes then answer the handler's ``409``, or its
    ``inbox_route_error`` for any status, #312); the items then list no
    ``allowed_actions``. Optional: absent means ``True``."""
    return bool(getattr(handler, "inbox_decisions", True))


def _inbox_route_error(
    handler: ApprovalHandler,
) -> Callable[[AsyncSession, ApprovalRequestView], Awaitable[AppError]] | None:
    """The handler's optional ``inbox_route_error`` hook (#312), only for a
    type decided on its own page; ``None`` otherwise."""
    if _inbox_decisions(handler):
        return None
    hook: Callable[[AsyncSession, ApprovalRequestView], Awaitable[AppError]] | None = (
        getattr(handler, "inbox_route_error", None)
    )
    return hook


def allowed_actions(
    status: str, *, supports_request_changes: bool
) -> list[ApprovalAction]:
    """The reviewer decisions a request in ``status`` takes now (pure)."""
    return [
        action
        for action, target in DECISION_TARGETS.items()
        if transition_allowed(status, target)
        and (action != "request_changes" or supports_request_changes)
    ]


async def _items(
    session: AsyncSession, requests: list[ApprovalRequest]
) -> list[ApprovalRequestItem]:
    """List items: one statement for the names and one per type present for
    the summaries."""
    names = await _account_names(
        session,
        (
            user_id
            for request in requests
            for user_id in (
                request.requester_user_id,
                request.assignee_user_id,
                request.resolved_by_user_id,
            )
        ),
    )
    by_type: dict[str, list[uuid.UUID]] = {}
    for request in requests:
        by_type.setdefault(request.request_type, []).append(request.subject_id)
    summaries: dict[tuple[str, uuid.UUID], Mapping[str, Any]] = {}
    for request_type, subject_ids in sorted(by_type.items()):
        found = await _handlers[request_type].summaries(session, subject_ids)
        for subject_id, summary in found.items():
            summaries[(request_type, subject_id)] = summary

    def name(user_id: uuid.UUID | None) -> AccountName | None:
        return names.get(user_id) if user_id is not None else None

    return [
        ApprovalRequestItem(
            id=request.id,
            request_type=request.request_type,
            status=request.status,  # type: ignore[arg-type]
            priority=request.priority,  # type: ignore[arg-type]
            requester=name(request.requester_user_id),
            assignee=name(request.assignee_user_id),
            created_at=request.created_at,
            resolved_at=request.resolved_at,
            resolved_by=name(request.resolved_by_user_id),
            reason=request.reason,
            subject=dict(summaries.get((request.request_type, request.subject_id), {})),
            allowed_actions=(
                allowed_actions(
                    request.status,
                    supports_request_changes=_handlers[
                        request.request_type
                    ].supports_request_changes,
                )
                if _inbox_decisions(_handlers[request.request_type])
                else []
            ),
            version=request.version,
        )
        for request in requests
    ]


@dataclass(frozen=True)
class ApprovalRequestFilters:
    """Validated list filters (``request_type`` is a registered type)."""

    status: ApprovalStatusFilter = "open"
    request_type: str | None = None
    assignee: ApprovalAssigneeFilter = "any"
    priority: ApprovalPriority | None = None
    sort: ApprovalSort = "priority"


_PRIORITY_ORDER: Final = case(
    {HIGH: 0, NORMAL: 1, LOW: 2}, value=ApprovalRequest.priority, else_=3
)
_ORDERS: Final[Mapping[str, tuple[Any, ...]]] = MappingProxyType(
    {
        "priority": (_PRIORITY_ORDER, ApprovalRequest.created_at, ApprovalRequest.id),
        "oldest": (ApprovalRequest.created_at, ApprovalRequest.id),
        "newest": (ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()),
    }
)
"""The list orders (#118 E7); each ends on ``id``, so paging is stable."""


async def list_approval_requests(
    session: AsyncSession,
    *,
    principal: Principal,
    filters: ApprovalRequestFilters,
    page: OffsetParams,
) -> OffsetPage[ApprovalRequestItem]:
    """The requests of the types the caller may review, in ``filters.sort``
    order (by default by priority, high first, then oldest first). At most
    ``3 + types on the page`` statements (the count, the page, the names, the
    summaries); none without any reviewable type."""
    types = _reviewable_types(principal)
    if filters.request_type is not None:
        types = [t for t in types if t == filters.request_type]
    if not types:
        return OffsetPage[ApprovalRequestItem](
            items=[], total=0, limit=page.limit, offset=page.offset
        )
    query = select(ApprovalRequest).where(ApprovalRequest.request_type.in_(types))
    if filters.status == "open":
        query = query.where(ApprovalRequest.status.in_(OPEN_STATUSES))
    elif filters.status != "all":
        query = query.where(ApprovalRequest.status == filters.status)
    if filters.assignee == "me":
        query = query.where(ApprovalRequest.assignee_user_id == principal.user_id)
    elif filters.assignee == "unassigned":
        query = query.where(ApprovalRequest.assignee_user_id.is_(None))
    if filters.priority is not None:
        query = query.where(ApprovalRequest.priority == filters.priority)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    requests = list(
        (
            await session.scalars(
                query.order_by(*_ORDERS[filters.sort])
                .limit(page.limit)
                .offset(page.offset)
            )
        ).all()
    )
    return OffsetPage[ApprovalRequestItem](
        items=await _items(session, requests),
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_approval_request(
    session: AsyncSession, *, principal: Principal, request_id: uuid.UUID
) -> ApprovalRequestItem:
    """One request; ``404 not_found`` when missing or hidden (B3)."""
    request: ApprovalRequest | None = await session.get(ApprovalRequest, request_id)
    if request is None:
        raise RecordNotFoundError()
    _reviewing_handler(request, principal)
    [item] = await _items(session, [request])
    return item


__all__ = [
    "APPROVAL_ASSIGNED",
    "OPEN_STATUSES",
    "TRANSITIONS",
    "AccountReferenceResolver",
    "AccountResolverMissingError",
    "ApprovalAssignedParams",
    "ApprovalHandler",
    "ApprovalRequestFilters",
    "ApprovalRequestView",
    "ReviewCandidateResolver",
    "ReviewCandidateResolverMissingError",
    "allowed_actions",
    "approval_request_id_for_subject",
    "assign_approval_request",
    "cancel_approval_request",
    "create_approval_request",
    "decide_approval_request",
    "ensure_transition",
    "get_approval_request",
    "list_approval_requests",
    "list_assignee_options",
    "open_approval_request",
    "register_account_reference_resolver",
    "register_approval_handler",
    "register_approval_notifications",
    "register_review_candidate_resolver",
    "registered_account_reference_resolver",
    "registered_approval_types",
    "registered_review_candidate_resolver",
    "resubmit_approval_request",
    "transition_allowed",
    "unregister_approval_handler",
]
