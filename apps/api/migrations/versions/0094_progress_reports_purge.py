"""Academics: let the retention job delete soft-deleted progress reports.

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-06

M8-12 (#129 C3). ``progress_reports_immutable()`` (0057) refused every
``DELETE`` because reports were only ever soft-deleted. The retention job
now hard-deletes a report 30 days after its soft delete, so the function
allows a ``DELETE`` of a row whose ``deleted_at`` is set (such a row is
always ``draft`` or ``changes_requested`` and never approved: the
``deleted_editable`` and ``approved_status`` CHECKs) and still refuses the
``DELETE`` of a live row with SQLSTATE 42501. The ``UPDATE`` rules are
unchanged. Downgrading restores the 0057 body.
"""

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0094"
down_revision: str | Sequence[str] | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORTS: Final = "progress_reports"
PROTECTED: Final = (
    "student_id",
    "author_user_id",
    "subject_id",
    "kind",
    "period_start",
    "period_end",
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
    "facts",
    "facts_computed_at",
    "approved_at",
    "approved_by_user_id",
)

_CHANGED = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in PROTECTED)

_UPDATE_RULE: Final = f"""
    IF OLD.approved_at IS NOT NULL AND ({_CHANGED}) THEN
        RAISE EXCEPTION 'an approved progress report never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
"""

PURGEABLE_FUNCTION: Final = f"""
CREATE OR REPLACE FUNCTION {REPORTS}_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.deleted_at IS NULL THEN
            RAISE EXCEPTION '{REPORTS} rows are never deleted'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
{_UPDATE_RULE}END;
$$
"""

ORIGINAL_FUNCTION: Final = f"""
CREATE OR REPLACE FUNCTION {REPORTS}_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{REPORTS} rows are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
{_UPDATE_RULE}END;
$$
"""


def upgrade() -> None:
    op.execute(PURGEABLE_FUNCTION)


def downgrade() -> None:
    op.execute(ORIGINAL_FUNCTION)
