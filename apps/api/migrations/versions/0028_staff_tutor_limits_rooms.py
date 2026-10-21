"""Staff: tutor teaching limits, required break and preferred classrooms.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-20

M3-09b (#197; spec §3). Configuration only: exceeding a limit is a warning
in M4-05, never a block (ISSUE-D4), and preferred rooms guide scheduling
without restricting it.

- ``tutor_availability_settings`` gains three nullable ``SMALLINT`` columns
  (``NULL`` = no limit, no break): ``daily_limit_minutes`` 15-1440,
  ``weekly_limit_minutes`` 15-10080 and not below the daily limit when both
  are set, ``required_break_minutes`` 0-240.
- ``tutor_preferred_classrooms`` (audited by the ORM): ``tutor_user_id``
  (``users``) and ``classroom_id`` (``classrooms``, indexed), both ``ON
  DELETE RESTRICT``; one row per tutor and room
  (``uq_tutor_preferred_classrooms_tutor_classroom``). At most 10 per tutor,
  enforced by the service.

No rows are inserted. Downgrading drops the table and the three columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = "tutor_availability_settings"
PREFERRED = "tutor_preferred_classrooms"
LIMIT_COLUMNS = (
    "daily_limit_minutes",
    "weekly_limit_minutes",
    "required_break_minutes",
)
CHECKS = {
    "daily_limit_range": "daily_limit_minutes BETWEEN 15 AND 1440",
    "weekly_limit_range": "weekly_limit_minutes BETWEEN 15 AND 10080",
    "weekly_not_below_daily": "weekly_limit_minutes >= daily_limit_minutes",
    "required_break_range": "required_break_minutes BETWEEN 0 AND 240",
}


def upgrade() -> None:
    for column in LIMIT_COLUMNS:
        op.add_column(SETTINGS, sa.Column(column, sa.SmallInteger(), nullable=True))
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{SETTINGS}_{name}"), SETTINGS, condition)

    op.create_table(
        PREFERRED,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("classroom_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tutor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{PREFERRED}_tutor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["classroom_id"],
            ["classrooms.id"],
            name=op.f(f"fk_{PREFERRED}_classroom_id_classrooms"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PREFERRED}")),
        sa.UniqueConstraint(
            "tutor_user_id",
            "classroom_id",
            name="uq_tutor_preferred_classrooms_tutor_classroom",
        ),
    )
    op.create_index(
        op.f(f"ix_{PREFERRED}_classroom_id"), PREFERRED, ["classroom_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table(PREFERRED)
    for name in reversed(CHECKS):
        op.drop_constraint(op.f(f"ck_{SETTINGS}_{name}"), SETTINGS, type_="check")
    for column in reversed(LIMIT_COLUMNS):
        op.drop_column(SETTINGS, column)
