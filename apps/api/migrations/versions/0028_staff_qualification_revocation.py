"""Staff: revoked tutor qualifications.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-20

M3-08b (#198; spec §3, §16). A reviewer withdraws an approved
qualification; the row stays, with the new status ``revoked``.

- ``tutor_qualifications.status`` also allows ``revoked``
  (``ck_tutor_qualifications_status_valid``).
- ``decided_at`` stays set for a revoked row (it was approved first):
  ``ck_tutor_qualifications_decided_iff_final`` counts ``revoked`` as final.
  ``rejection_reason`` stays ``NULL`` (``reason_iff_rejected`` unchanged).
- New columns ``revoked_at TIMESTAMPTZ``, ``revoked_by_user_id`` (``users``,
  ``ON DELETE RESTRICT``) and ``revocation_reason VARCHAR(1000)``, all
  nullable; ``ck_tutor_qualifications_revocation_iff_revoked``: the three
  are set exactly when ``status = 'revoked'``.
- The partial unique index ``uq_tutor_qualifications_active`` is not
  changed: it covers ``pending`` and ``approved`` only, so a revoked row
  doesn't block a new request.

**Downgrade refuses** (raises) while any ``revoked`` row exists, so that no
revocation is lost silently; otherwise it restores the #44 CHECKs and drops
the three columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tutor_qualifications"
STATUS_VALID = f"ck_{TABLE}_status_valid"
DECIDED_IFF_FINAL = f"ck_{TABLE}_decided_iff_final"
REVOCATION_IFF_REVOKED = f"ck_{TABLE}_revocation_iff_revoked"
REVOKED_BY_FK = f"fk_{TABLE}_revoked_by_user_id_users"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.add_column(TABLE, sa.Column("revoked_by_user_id", sa.UUID()))
    op.add_column(TABLE, sa.Column("revocation_reason", sa.String(length=1000)))
    op.create_foreign_key(
        op.f(REVOKED_BY_FK),
        TABLE,
        "users",
        ["revoked_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f(STATUS_VALID), TABLE, type_="check")
    op.create_check_constraint(
        op.f(STATUS_VALID),
        TABLE,
        "status IN ('pending', 'approved', 'rejected', 'cancelled', 'revoked')",
    )
    op.drop_constraint(op.f(DECIDED_IFF_FINAL), TABLE, type_="check")
    op.create_check_constraint(
        op.f(DECIDED_IFF_FINAL),
        TABLE,
        "(status IN ('approved', 'rejected', 'cancelled', 'revoked'))"
        " = (decided_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f(REVOCATION_IFF_REVOKED),
        TABLE,
        "(status = 'revoked') = (revoked_at IS NOT NULL)"
        " AND (status = 'revoked') = (revoked_by_user_id IS NOT NULL)"
        " AND (status = 'revoked') = (revocation_reason IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM tutor_qualifications WHERE status = 'revoked')"
        " THEN RAISE EXCEPTION"
        " 'tutor_qualifications has revoked rows: downgrading 0028 would lose them';"
        " END IF; END $$"
    )
    op.drop_constraint(op.f(REVOCATION_IFF_REVOKED), TABLE, type_="check")
    op.drop_constraint(op.f(DECIDED_IFF_FINAL), TABLE, type_="check")
    op.create_check_constraint(
        op.f(DECIDED_IFF_FINAL),
        TABLE,
        "(status IN ('approved', 'rejected', 'cancelled')) = (decided_at IS NOT NULL)",
    )
    op.drop_constraint(op.f(STATUS_VALID), TABLE, type_="check")
    op.create_check_constraint(
        op.f(STATUS_VALID),
        TABLE,
        "status IN ('pending', 'approved', 'rejected', 'cancelled')",
    )
    op.drop_constraint(op.f(REVOKED_BY_FK), TABLE, type_="foreignkey")
    op.drop_column(TABLE, "revocation_reason")
    op.drop_column(TABLE, "revoked_by_user_id")
    op.drop_column(TABLE, "revoked_at")
