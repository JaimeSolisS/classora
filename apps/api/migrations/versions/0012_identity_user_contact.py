"""Identity: optional contact fields on ``users``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19

M2-13 (#31; ADR-0007 D7). Adds two nullable columns to ``users``, edited by
staff on the user detail page and never used to sign in:

- ``email VARCHAR(254)`` with CHECK ``ck_users_email_format``
  (``^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$``);
- ``phone VARCHAR(32)`` with CHECK ``ck_users_phone_format``
  (``^\\+?[0-9][0-9 ().-]{5,30}[0-9]$``).

Neither is unique: family members may share them. Both are audited
automatically (``users`` is audited, #22). Adds no extension. Downgrading
drops exactly these columns and CHECKs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL_CHECK = r"email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'"
PHONE_CHECK = r"phone ~ '^\+?[0-9][0-9 ().-]{5,30}[0-9]$'"


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))
    op.create_check_constraint(op.f("ck_users_email_format"), "users", EMAIL_CHECK)
    op.create_check_constraint(op.f("ck_users_phone_format"), "users", PHONE_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_phone_format"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_email_format"), "users", type_="check")
    op.drop_column("users", "phone")
    op.drop_column("users", "email")
