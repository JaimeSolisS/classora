"""Saved export setups (#125; spec §8 reports; architecture ISSUE-A2).

A setup is a name and one validated #124 ``ExportParameters``, stored as the
model dumped as JSON with omitted optional fields kept omitted, so posting
the stored ``parameters`` to ``POST /api/v1/exports`` validates to the same
model as configuring it by hand (#125 E1, E2). There is no run route (E4):
running is that ``POST``, under every #124 check for the runner.

**Visibility** (B2, E3): the caller's own setups, always; another user's
only when its ``shared_role`` is one of the caller's roles **and** the caller
may export its dataset. Nothing else, for any role, the Owner included.
Anything else is ``404 not_found``; a visible setup of another user is
``403 permission_denied`` on a change (only the creator edits or deletes).

**Saving** (B3, B4, E5): ``422 export_range_invalid`` (the #124 range
check, its field at ``["body", "parameters", "filters", <field>]``) → ``403
permission_denied`` unless :func:`~..exports.registry.may_export` → ``409
report_setup_limit_reached`` at :data:`~.errors.MAX_SETUPS_PER_USER`
(under a per-user advisory lock) → ``409 report_setup_name_taken``. No
reauthentication (a setup holds no data). Every function runs in the
caller's Unit of Work and never commits.
"""

import uuid
from typing import Any

from sqlalchemy import Select, and_, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.administration.schemas import AccountName
from app.modules.identity import service as identity
from app.modules.identity.service import STAFF_ROLES
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.exports.errors import ExportRangeInvalidError
from app.modules.reporting.exports.registry import (
    ExportParametersBase,
    export_dataset,
    exportable_datasets,
    may_export,
)
from app.modules.reporting.setups import errors
from app.modules.reporting.setups.errors import (
    ReportSetupLimitReachedError,
    ReportSetupNameTakenError,
)
from app.modules.reporting.setups.models import NAME_UNIQUE, ReportSetup
from app.modules.reporting.setups.schemas import (
    ReportSetupCreate,
    ReportSetupItem,
    ReportSetupList,
    ReportSetupUpdate,
)


def stored_parameters(parameters: ExportParametersBase) -> dict[str, Any]:
    """What a setup stores (#125 B3): the validated model as JSON, omitted
    optional fields kept omitted (so the default columns stay implicit)."""
    return parameters.model_dump(mode="json", by_alias=True, exclude_unset=True)


def _check_parameters(principal: Principal, parameters: ExportParametersBase) -> None:
    """``422 export_range_invalid``, then ``403`` unless the caller may
    export the dataset (the #124 functions, unchanged)."""
    dataset = export_dataset(getattr(parameters, "dataset"))  # noqa: B009
    if dataset is None:  # pragma: no cover - the body names a built-in dataset
        raise PermissionDeniedError()
    if dataset.check_filters is not None:
        try:
            dataset.check_filters(parameters.filter_values())
        except ExportRangeInvalidError as error:
            # The same check; its field is under the body's `parameters`.
            for field_error in error.errors or ():
                field_error["path"] = ["body", "parameters", *field_error["path"][1:]]
            raise
    if not may_export(principal, dataset):
        raise PermissionDeniedError()


def _may_run(principal: Principal, code: str) -> bool:
    dataset = export_dataset(code)
    return dataset is not None and may_export(principal, dataset)


def _visible(principal: Principal) -> Any:
    """The B2 rule as a ``WHERE`` clause."""
    codes = [dataset.code for dataset in exportable_datasets(principal)]
    roles = sorted(STAFF_ROLES & principal.roles)
    shared = (
        and_(ReportSetup.shared_role.in_(roles), ReportSetup.dataset.in_(codes))
        if roles and codes
        else false()
    )
    return or_(ReportSetup.created_by_user_id == principal.user_id, shared)


def _with_names() -> Select[Any]:
    names = identity.display_name_columns().subquery()
    return select(ReportSetup, names.c.display_name).join(
        names, names.c.id == ReportSetup.created_by_user_id
    )


def _item(
    principal: Principal, setup: ReportSetup, display_name: str
) -> ReportSetupItem:
    return ReportSetupItem.model_validate(
        {
            "id": setup.id,
            "name": setup.name,
            "dataset": setup.dataset,
            "parameters": setup.parameters,
            "shared_role": setup.shared_role,
            "created_by": AccountName(
                id=setup.created_by_user_id, display_name=display_name
            ),
            "mine": setup.created_by_user_id == principal.user_id,
            "can_run": _may_run(principal, setup.dataset),
            "version": setup.version,
            "created_at": setup.created_at,
            "updated_at": setup.updated_at,
        }
    )


async def _item_of(
    session: AsyncSession, principal: Principal, setup: ReportSetup
) -> ReportSetupItem:
    names = identity.display_name_columns().subquery()
    display_name = await session.scalar(
        select(names.c.display_name).where(names.c.id == setup.created_by_user_id)
    )
    return _item(principal, setup, display_name or "")


# --- List (B2) ------------------------------------------------------------------------


async def list_setups(
    session: AsyncSession, *, principal: Principal
) -> ReportSetupList:
    """The setups the caller may see, by name (case-insensitive), then id."""
    rows = await session.execute(
        _with_names()
        .where(_visible(principal))
        .order_by(func.lower(ReportSetup.name), ReportSetup.id)
    )
    return ReportSetupList(
        items=[_item(principal, setup, name) for setup, name in rows]
    )


# --- Create (B3) ----------------------------------------------------------------------


async def _lock_creator(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialize one user's saves, so two can't both pass the limit."""
    key = func.hashtextextended(f"reporting.setups:{user_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def _ensure_name_free(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    except_id: uuid.UUID | None = None,
) -> None:
    query = select(ReportSetup.id).where(
        ReportSetup.created_by_user_id == user_id,
        func.lower(ReportSetup.name) == func.lower(name),
    )
    if except_id is not None:
        query = query.where(ReportSetup.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise ReportSetupNameTakenError()


async def _flush(session: AsyncSession) -> None:
    """Flush; a concurrent save that took the name after the pre-check is
    ``409 report_setup_name_taken``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if NAME_UNIQUE in str(exc.orig):
            raise ReportSetupNameTakenError() from None
        raise


async def create_setup(
    session: AsyncSession, *, principal: Principal, body: ReportSetupCreate
) -> ReportSetupItem:
    """Save a setup for the caller (#125 B3, steps 6-10)."""
    parameters: ExportParameters = body.parameters
    _check_parameters(principal, parameters)
    await _lock_creator(session, principal.user_id)
    owned = await session.scalar(
        select(func.count())
        .select_from(ReportSetup)
        .where(ReportSetup.created_by_user_id == principal.user_id)
    )
    if (owned or 0) >= errors.MAX_SETUPS_PER_USER:
        raise ReportSetupLimitReachedError(errors.MAX_SETUPS_PER_USER)
    await _ensure_name_free(session, principal.user_id, body.name)
    setup = ReportSetup(
        created_by_user_id=principal.user_id,
        name=body.name,
        dataset=parameters.dataset,
        parameters=stored_parameters(parameters),
        shared_role=body.shared_role,
    )
    session.add(setup)
    await _flush(session)
    return await _item_of(session, principal, setup)


# --- Update and delete (B4, B5) -------------------------------------------------------


async def _own_setup(
    session: AsyncSession, principal: Principal, setup_id: uuid.UUID
) -> ReportSetup:
    """The setup ``FOR UPDATE`` when the caller created it: ``404`` unless
    the caller may see it (B2), ``403`` when it is another user's."""
    setup = await session.scalar(
        select(ReportSetup)
        .where(ReportSetup.id == setup_id, _visible(principal))
        .with_for_update()
    )
    if setup is None:
        raise RecordNotFoundError()
    if setup.created_by_user_id != principal.user_id:
        raise PermissionDeniedError()
    return setup


async def update_setup(
    session: AsyncSession,
    *,
    principal: Principal,
    setup_id: uuid.UUID,
    body: ReportSetupUpdate,
) -> ReportSetupItem:
    """Change the caller's setup (#125 B4). An unchanged body changes
    nothing (no version bump, no audit event)."""
    setup = await _own_setup(session, principal, setup_id)
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    if "parameters" in sent and body.parameters is not None:
        parameters: ExportParameters = body.parameters
        _check_parameters(principal, parameters)
        changes["dataset"] = parameters.dataset
        changes["parameters"] = stored_parameters(parameters)
    elif not _may_run(principal, setup.dataset):
        raise PermissionDeniedError()
    ensure_version(setup, body.version)
    if "name" in sent and body.name is not None:
        if body.name != setup.name:
            await _ensure_name_free(
                session, principal.user_id, body.name, except_id=setup.id
            )
        changes["name"] = body.name
    if "shared_role" in sent:
        changes["shared_role"] = body.shared_role
    changed = {
        field: value
        for field, value in changes.items()
        if getattr(setup, field) != value
    }
    for field, value in changed.items():
        setattr(setup, field, value)
    if changed:
        await _flush(session)
    return await _item_of(session, principal, setup)


async def delete_setup(
    session: AsyncSession, *, principal: Principal, setup_id: uuid.UUID
) -> None:
    """Delete the caller's setup (#125 B5): a hard delete, audited. Allowed
    even when the caller may no longer export its dataset."""
    setup = await _own_setup(session, principal, setup_id)
    await session.delete(setup)
    await session.flush()
