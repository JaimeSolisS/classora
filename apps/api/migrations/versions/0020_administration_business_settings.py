"""Administration: business settings and operating hours.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-19

M3-01 (#37; spec §9, §16; ADR-0004).

- ``business_settings``: the center's one settings record. ``singleton``
  (always true: CHECK, and unique) allows at most one row. ``center_name``
  (trimmed, never blank), ``contact_phone`` and ``contact_email`` (the #31
  formats of ``users.phone``/``users.email``), ``address`` (trimmed),
  ``time_zone`` (default ``America/Mexico_City``) and ``version``. Audited by
  the ORM.
- ``operating_hours``: one interval per ISO weekday (``weekday`` 1-7, unique),
  ``opens_at < closes_at``, both without seconds. A weekday without a row is
  closed. Audited by the ORM.

This revision inserts the single settings row (precedent: #174's privacy
notice version 1): ``center_name = 'Classora'``, no contact details, the
default zone, ``version = 1``; and the default hours, Monday to Friday 09:00
to 20:00 and Saturday 09:00 to 14:00 (Sunday closed). Raw inserts write no
audit event. ``make seed`` never touches these rows.

Downgrading drops both tables.
"""

from collections.abc import Sequence
from datetime import time
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CENTER_NAME: Final = "Classora"
DEFAULT_TIME_ZONE: Final = "America/Mexico_City"
"""``app.core.clock.DEFAULT_CENTER_TIME_ZONE``, copied (a migration never
imports application code that may change later)."""

DEFAULT_HOURS: Final = (
    *((weekday, time(9, 0), time(20, 0)) for weekday in range(1, 6)),
    (6, time(9, 0), time(14, 0)),
)
"""``(weekday, opens_at, closes_at)``: Monday-Friday 09-20, Saturday 09-14."""

EMAIL_CHECK = r"contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'"
PHONE_CHECK = r"contact_phone ~ '^\+?[0-9][0-9 ().-]{5,30}[0-9]$'"


def _standard_columns() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    settings = op.create_table(
        "business_settings",
        sa.Column(
            "singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("center_name", sa.String(length=100), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column(
            "time_zone",
            sa.String(length=64),
            server_default=DEFAULT_TIME_ZONE,
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            "singleton", name=op.f("ck_business_settings_singleton_true")
        ),
        sa.CheckConstraint(
            "center_name = btrim(center_name)",
            name=op.f("ck_business_settings_center_name_trimmed"),
        ),
        sa.CheckConstraint(
            "center_name ~ '[^[:space:]]'",
            name=op.f("ck_business_settings_center_name_not_blank"),
        ),
        sa.CheckConstraint(EMAIL_CHECK, name=op.f("ck_business_settings_email_format")),
        sa.CheckConstraint(PHONE_CHECK, name=op.f("ck_business_settings_phone_format")),
        sa.CheckConstraint(
            "address = btrim(address)",
            name=op.f("ck_business_settings_address_trimmed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_settings")),
        sa.UniqueConstraint("singleton", name=op.f("uq_business_settings_singleton")),
    )
    hours = op.create_table(
        "operating_hours",
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7", name=op.f("ck_operating_hours_weekday_range")
        ),
        sa.CheckConstraint(
            "opens_at < closes_at", name=op.f("ck_operating_hours_opens_before_closes")
        ),
        sa.CheckConstraint(
            "EXTRACT(SECOND FROM opens_at) = 0 AND EXTRACT(SECOND FROM closes_at) = 0",
            name=op.f("ck_operating_hours_zero_seconds"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operating_hours")),
        sa.UniqueConstraint("weekday", name=op.f("uq_operating_hours_weekday")),
    )
    op.execute(
        sa.insert(settings).values(
            singleton=True,
            center_name=DEFAULT_CENTER_NAME,
            time_zone=DEFAULT_TIME_ZONE,
            version=1,
        )
    )
    op.bulk_insert(
        hours,
        [
            {"weekday": weekday, "opens_at": opens_at, "closes_at": closes_at}
            for weekday, opens_at, closes_at in DEFAULT_HOURS
        ],
    )


def downgrade() -> None:
    op.drop_table("operating_hours")
    op.drop_table("business_settings")
