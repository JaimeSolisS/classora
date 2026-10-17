"""Baseline: an empty schema under Alembic control.

Revision ID: 0001
Revises:
Create Date: 2026-10-17

Creates no tables and no extensions. Each extension is enabled by the revision
of the first issue that needs it, once the decision that names it is accepted:
``pg_trgm`` and ``unaccent`` per ISSUE-D6 (first consumer M2-15), and
``btree_gist`` per ISSUE-D4 / ADR-0005 (M4-01 gate). UUIDv7 keys need no
extension: ``uuidv7()`` is built into PostgreSQL 18 (ADR-0001).
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
