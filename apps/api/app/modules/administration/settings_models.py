"""Business settings tables (#37; spec §9, §16; ADR-0004).

- ``business_settings``: the center's one settings record: its name and
  contact details and the **center time zone** (default
  ``America/Mexico_City``). At most one row exists: ``singleton`` is always
  true (CHECK) and unique. Migration 0020 inserts the row, so it exists in
  every database built by migration; nothing else ever inserts or deletes it.
  Versioned (optimistic concurrency, architecture §4.4).
- ``operating_hours``: one opening interval per ISO weekday (1 = Monday …
  7 = Sunday), local wall-clock ``TIME`` values without seconds. A weekday
  without a row is closed. Not versioned: it is edited only through the
  settings ``PATCH``, whose ``business_settings.version`` covers it.

Both are :class:`~app.core.audit.Audited` with nothing excluded or masked
(#22); none of their columns is personal data.

The contact patterns are the #31 ones, copied verbatim (ADR-0002: no import of
identity's models); ``tests/unit/test_business_settings_schemas.py`` keeps
them equal.
"""

from datetime import time
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, SmallInteger, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.clock import DEFAULT_CENTER_TIME_ZONE
from app.core.models import Model, VersionedMixin

CENTER_NAME_MAX_LENGTH: Final = 100
EMAIL_MAX_LENGTH: Final = 254
PHONE_MAX_LENGTH: Final = 32
ADDRESS_MAX_LENGTH: Final = 300
TIME_ZONE_MAX_LENGTH: Final = 64

EMAIL_PATTERN: Final = r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"
PHONE_PATTERN: Final = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"

FIRST_WEEKDAY: Final = 1
LAST_WEEKDAY: Final = 7


class BusinessSettings(Audited, VersionedMixin, Model):
    __tablename__ = "business_settings"
    __table_args__ = (
        CheckConstraint("singleton", name="singleton_true"),
        CheckConstraint("center_name = btrim(center_name)", name="center_name_trimmed"),
        CheckConstraint("center_name ~ '[^[:space:]]'", name="center_name_not_blank"),
        CheckConstraint(f"contact_email ~ '{EMAIL_PATTERN}'", name="email_format"),
        CheckConstraint(f"contact_phone ~ '{PHONE_PATTERN}'", name="phone_format"),
        CheckConstraint("address = btrim(address)", name="address_trimmed"),
    )

    singleton: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), unique=True
    )
    center_name: Mapped[str] = mapped_column(String(CENTER_NAME_MAX_LENGTH))
    contact_phone: Mapped[str | None] = mapped_column(
        String(PHONE_MAX_LENGTH), nullable=True
    )
    contact_email: Mapped[str | None] = mapped_column(
        String(EMAIL_MAX_LENGTH), nullable=True
    )
    address: Mapped[str | None] = mapped_column(
        String(ADDRESS_MAX_LENGTH), nullable=True
    )
    time_zone: Mapped[str] = mapped_column(
        String(TIME_ZONE_MAX_LENGTH),
        server_default=DEFAULT_CENTER_TIME_ZONE,
    )


class OperatingHours(Audited, Model):
    __tablename__ = "operating_hours"
    __table_args__ = (
        CheckConstraint(
            f"weekday BETWEEN {FIRST_WEEKDAY} AND {LAST_WEEKDAY}", name="weekday_range"
        ),
        CheckConstraint("opens_at < closes_at", name="opens_before_closes"),
        CheckConstraint(
            "EXTRACT(SECOND FROM opens_at) = 0 AND EXTRACT(SECOND FROM closes_at) = 0",
            name="zero_seconds",
        ),
    )

    weekday: Mapped[int] = mapped_column(SmallInteger, unique=True)
    opens_at: Mapped[time] = mapped_column(Time(timezone=False))
    closes_at: Mapped[time] = mapped_column(Time(timezone=False))
