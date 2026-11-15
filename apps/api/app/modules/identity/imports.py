"""The ``tutors`` import kind (#122 A3, B6, D5; E5, E7).

Registered by the composition roots (``app/wiring.py``) through
``identity.service.TUTORS_IMPORT_KIND``. Who may import: holders of
``user.invite`` who may assign the ``tutor`` role (``user.role.assign``):
the Owner and the Administrator (E5), as for ``POST /api/v1/invitations``.

**Columns**: ``username``\\*, ``display_name``\\*, ``email``, ``phone``.
``username`` is normalized like an invitation's (``username_invalid``);
``display_name`` is 1-200 characters after trimming; ``email`` and ``phone``
follow the ``users`` patterns (``invalid_email``, ``invalid_phone``).

**Natural key** (E6): the normalized username. An existing user with it who
has the ``tutor`` role makes the row ``duplicate`` (``duplicate_existing``);
any other existing user is ``error`` (``username_taken``); a repeat of an
earlier ready row is ``duplicate`` (``duplicate_in_file``). One statement for
the whole file.

**Commit** (E7): what ``POST /api/v1/invitations`` creates, through
``invitations.create_imported_invitation``: a pending user with the ``tutor``
role, the email and phone, and an invitation token whose raw value is
discarded. The admin gets each link from the Invitations page (Reissue).

This file imports ``identity.service`` and ``identity.invitations`` inside
the functions that use them: ``identity.service`` exports this kind.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.identity.errors import UsernameInvalidError
from app.modules.identity.models import Role, User, UserRole
from app.modules.identity.permissions import TUTOR, USER_INVITE, may_assign_role
from app.modules.identity.schemas import ContactEmail, ContactPhone, DisplayName

KIND: Final = "tutors"
COLUMNS: Final = (
    administration.ImportColumn("username", required=True),
    administration.ImportColumn("display_name", required=True),
    administration.ImportColumn("email"),
    administration.ImportColumn("phone"),
)


class TutorRow(BaseModel):
    """A row's fields, validated with the invitation and contact rules."""

    model_config = ConfigDict(extra="ignore")

    display_name: DisplayName
    email: ContactEmail = None
    phone: ContactPhone = None


@dataclass(frozen=True)
class CheckedTutor:
    username: str
    row: TutorRow


def _code(column: str, error_type: str) -> str:
    if error_type in {"missing", "string_too_short"}:
        return "required"
    if error_type == "string_too_long":
        return "too_long"
    if error_type == "string_pattern_mismatch":
        return "invalid_email" if column == "email" else "invalid_phone"
    return "invalid"


def check_row(
    values: Mapping[str, str],
) -> tuple[CheckedTutor | None, list[administration.RowIssue]]:
    """Validate one row (pure but for the username normalization)."""
    from app.modules.identity.service import normalize_username

    errors: list[administration.RowIssue] = []
    raw_username = values.get("username", "").strip()
    username: str | None = None
    if not raw_username:
        errors.append(administration.RowIssue("username", "required"))
    else:
        try:
            username = normalize_username(raw_username)
        except UsernameInvalidError:
            errors.append(administration.RowIssue("username", "username_invalid"))
    try:
        row = TutorRow.model_validate(
            {
                "display_name": values.get("display_name", ""),
                "email": values.get("email", ""),
                "phone": values.get("phone", ""),
            }
        )
    except ValidationError as exc:
        seen: set[str] = set()
        for error in exc.errors(include_input=False, include_url=False):
            column = str(error["loc"][0]) if error["loc"] else "display_name"
            if column not in seen:
                seen.add(column)
                errors.append(
                    administration.RowIssue(column, _code(column, error["type"]))
                )
        return None, errors
    if username is None:
        return None, errors
    return CheckedTutor(username=username, row=row), errors


async def _existing_usernames(
    session: AsyncSession, usernames: Sequence[str]
) -> dict[str, bool]:
    """``{username: has the tutor role}`` for the existing users among
    ``usernames`` (one statement)."""
    if not usernames:
        return {}
    is_tutor = (
        exists()
        .where(UserRole.user_id == User.id)
        .where(UserRole.role_id == Role.id, Role.code == TUTOR)
    )
    rows = await session.execute(
        select(User.username, is_tutor.label("is_tutor")).where(
            User.username.in_(set(usernames))
        )
    )
    return {username: bool(tutor) for username, tutor in rows}


@dataclass(frozen=True)
class TutorsImportKind:
    """See the module docstring."""

    code: str = KIND
    permission: str = USER_INVITE
    columns: Sequence[administration.ImportColumn] = COLUMNS
    batch_size: int = administration.DEFAULT_BATCH_SIZE

    def may_import(self, principal: Principal) -> bool:
        return principal.has(USER_INVITE) and may_assign_role(
            principal.permissions, TUTOR
        )

    async def prepare(
        self,
        session: AsyncSession,
        rows: Sequence[Mapping[str, str]],
        principal: Principal | None,
    ) -> list[administration.RowResult]:
        checked = [check_row(row) for row in rows]
        existing = await _existing_usernames(
            session, [item.username for item, _ in checked if item is not None]
        )
        seen: set[str] = set()
        results: list[administration.RowResult] = []
        for item, errors in checked:
            if item is None or errors:
                results.append(administration.RowResult("error", errors=tuple(errors)))
                continue
            tutor = existing.get(item.username)
            if tutor is False:
                results.append(
                    administration.RowResult(
                        "error",
                        errors=(administration.RowIssue("username", "username_taken"),),
                    )
                )
                continue
            if tutor is True:
                warning = "duplicate_existing"
            elif item.username in seen:
                warning = "duplicate_in_file"
            else:
                seen.add(item.username)
                results.append(administration.RowResult("ready"))
                continue
            results.append(
                administration.RowResult(
                    "duplicate", warnings=(administration.RowIssue(None, warning),)
                )
            )
        return results

    async def commit_row(
        self,
        session: AsyncSession,
        row_values: Mapping[str, str],
        ref: administration.ImportRowRef,
        actor_user_id: uuid.UUID,
    ) -> administration.CommitOutcome:
        from app.modules.identity import invitations

        item, errors = check_row(row_values)
        if item is None or errors:
            return administration.CommitOutcome.failed("invalid", errors)
        user_id = await invitations.create_imported_invitation(
            session,
            username=item.username,
            display_name=item.row.display_name,
            email=item.row.email,
            phone=item.row.phone,
            role=TUTOR,
            issued_by=actor_user_id,
            import_job_id=ref.job_id,
        )
        return administration.CommitOutcome.created(user_id)


TUTORS_IMPORT_KIND: Final = TutorsImportKind()
