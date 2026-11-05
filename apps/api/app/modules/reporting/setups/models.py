"""Saved export setups (#125 A1, A2; migration 0091).

``report_setups``: a name and one #124 ``ExportParameters`` (dataset, format,
ordered columns, ``group_by`` and filters with absolute dates), as validated
and dumped as JSON with omitted optional fields kept omitted (#125 E1, E2).
It belongs to its creator (``created_by_user_id``, ``ON DELETE RESTRICT``)
and may be shared with one staff role (``shared_role``, #125 E3). Running it
posts ``parameters`` to ``POST /api/v1/exports``, so nothing here is a file
or a row of data: ``parameters`` hold ids, codes and dates only.

Audited (the automatic create, update and delete events) and versioned (the
optimistic ``version``). ``name`` is staff free text (#129 retention).
"""

import uuid
from typing import Any, Final, Literal, get_args

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy import column as sql_column
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.modules.reporting.exports.models import DATASET_CODE_PATTERN

type SharedRole = Literal[
    "owner", "administrator", "academic_coordinator", "finance_staff"
]
SHARED_ROLES: Final[tuple[str, ...]] = get_args(SharedRole.__value__)
"""The staff roles a setup may be shared with (``identity.service.STAFF_ROLES``)."""

NAME_MAX_LENGTH: Final = 80
NAME_UNIQUE: Final = "uq_report_setups_creator_name"
SHARED_INDEX: Final = "ix_report_setups_shared_role"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class ReportSetup(Audited, VersionedMixin, Model):
    __tablename__ = "report_setups"
    __table_args__ = (
        CheckConstraint(
            f"name = btrim(name) AND char_length(btrim(name)) BETWEEN 1"
            f" AND {NAME_MAX_LENGTH}",
            name="name_trimmed",
        ),
        CheckConstraint(f"dataset ~ '{DATASET_CODE_PATTERN}'", name="dataset_format"),
        CheckConstraint(
            "jsonb_typeof(parameters) = 'object'"
            " AND coalesce(parameters->>'dataset', '') = dataset",
            name="parameters_dataset",
        ),
        CheckConstraint(
            f"shared_role IS NULL OR shared_role IN ({_quoted(SHARED_ROLES)})",
            name="shared_role_valid",
        ),
        # One name per creator, case-insensitively (#125 E5).
        Index(
            NAME_UNIQUE,
            "created_by_user_id",
            func.lower(sql_column("name")),
            unique=True,
        ),
        # The list's shared part.
        Index(
            SHARED_INDEX,
            "shared_role",
            postgresql_where=text("shared_role IS NOT NULL"),
        ),
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    shared_role: Mapped[str | None] = mapped_column(Text, nullable=True)
