"""Administration: effective-dated scheduling policy versions.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-20

M4-16 (#61; spec §3, §9; ADR-0005 D3).

- ``scheduling_policies`` (audited by the ORM, not versioned: rows are never
  updated): ``effective_from`` TIMESTAMPTZ, unique
  (``uq_scheduling_policies_effective_from``); ``booking_notice_hours`` and
  ``change_notice_hours`` SMALLINT 0-336; ``response_hours`` SMALLINT 1-168;
  ``makeup_after_on_time``, ``makeup_after_late`` and
  ``makeup_after_no_show`` BOOLEAN; ``makeup_window_days`` SMALLINT 1-365.

This revision inserts version 1 (precedent: #37's settings row), effective
from 2000-01-01T00:00:00Z so a policy is in force for every instant, with the
defaults: 24 h booking notice, 24 h change notice, 48 h to answer an offer, a
makeup only after an on-time cancellation, within 30 days. A raw insert
writes no audit event. ``make seed`` never touches it.

Downgrading drops the table.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "scheduling_policies"

VERSION_1: Final = {
    "effective_from": datetime(2000, 1, 1, tzinfo=UTC),
    "booking_notice_hours": 24,
    "change_notice_hours": 24,
    "response_hours": 48,
    "makeup_after_on_time": True,
    "makeup_after_late": False,
    "makeup_after_no_show": False,
    "makeup_window_days": 30,
}


def upgrade() -> None:
    policies = op.create_table(
        TABLE,
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("booking_notice_hours", sa.SmallInteger(), nullable=False),
        sa.Column("change_notice_hours", sa.SmallInteger(), nullable=False),
        sa.Column("response_hours", sa.SmallInteger(), nullable=False),
        sa.Column("makeup_after_on_time", sa.Boolean(), nullable=False),
        sa.Column("makeup_after_late", sa.Boolean(), nullable=False),
        sa.Column("makeup_after_no_show", sa.Boolean(), nullable=False),
        sa.Column("makeup_window_days", sa.SmallInteger(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "booking_notice_hours BETWEEN 0 AND 336",
            name=op.f("ck_scheduling_policies_booking_notice_hours_range"),
        ),
        sa.CheckConstraint(
            "change_notice_hours BETWEEN 0 AND 336",
            name=op.f("ck_scheduling_policies_change_notice_hours_range"),
        ),
        sa.CheckConstraint(
            "response_hours BETWEEN 1 AND 168",
            name=op.f("ck_scheduling_policies_response_hours_range"),
        ),
        sa.CheckConstraint(
            "makeup_window_days BETWEEN 1 AND 365",
            name=op.f("ck_scheduling_policies_makeup_window_days_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduling_policies")),
        sa.UniqueConstraint(
            "effective_from", name=op.f("uq_scheduling_policies_effective_from")
        ),
    )
    op.bulk_insert(policies, [VERSION_1])


def downgrade() -> None:
    op.drop_table(TABLE)
