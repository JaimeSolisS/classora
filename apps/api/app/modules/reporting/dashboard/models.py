"""The per-user dashboard layout (#127 A1, A2; migration 0092).

``dashboard_layouts``: one row per user who customized their dashboard
(``user_id`` unique, ``ON DELETE RESTRICT``). ``widgets`` is the ordered
JSON array of ``{widget, visible}`` the user saved (1-20 entries); no row
means the role default. The row holds widget codes and booleans only, never
a figure.

Not :class:`~app.core.audit.Audited` and not versioned: it is a per-user UI
preference, like ``user_preferences`` (#36) and ``notification_opt_outs``
(#119), and the last write wins (#127 E5).
"""

import uuid
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Model

MAX_STORED_WIDGETS: Final = 20
"""The most entries a stored layout holds (the table's CHECK)."""


class DashboardLayout(Model):
    __tablename__ = "dashboard_layouts"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(widgets) = 'array'"
            f" AND jsonb_array_length(widgets) BETWEEN 1 AND {MAX_STORED_WIDGETS}",
            name="widgets_array",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    widgets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
