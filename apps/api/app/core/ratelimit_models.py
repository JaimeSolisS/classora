"""The ``rate_limit_counters`` table (#27; ADR-0008 D15).

One row per policy, key and fixed window: how many units of the policy's
limit the key has used in that window (``app.core.ratelimit``). The key is
stored only as ``key_hash = SHA-256("<policy>\\x00<key>")``: no username or IP
address is kept in clear text. Rows refer to no other table and live for at
most their window plus the purge grace (``app.core.ratelimit_tasks``).

Not audited: counters change on every attempt, and a flood must not grow
``audit_events``.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Model


class RateLimitCounter(Model):
    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        CheckConstraint("octet_length(key_hash) = 32", name="key_hash_length"),
        CheckConstraint("window_end > window_start", name="window_order"),
        CheckConstraint("count >= 0", name="count_not_negative"),
        UniqueConstraint(
            "policy",
            "key_hash",
            "window_start",
            name="uq_rate_limit_counters_policy_key_window",
        ),
        Index("ix_rate_limit_counters_window_end", "window_end"),
    )

    policy: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
