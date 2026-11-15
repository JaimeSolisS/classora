"""Scheduling: clear the series assignments 0036 backfilled without an offer.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-21

M4-263 (#263, #233 QA O1; E1 (a), owner-delegated recommended decision).

Revision 0036 set ``assignment_status = 'pending'`` on every series that
already had a tutor but made no ``assignment_offers`` row, so the nightly
materialization (extend mode) raised on those series and the #233 A3
invariants were false. This revision clears those assignments; staff offer
the series again.

**Broken series:** ``assignment_status = 'pending'`` and no ``pending``
``series`` offer of the same tutor for it.

1. Their ``offered`` occurrences that no ``pending`` offer covers become
   ``unassigned`` with no tutor (``version`` bumped). Other states, and
   ``offered`` rows covered by a pending (session-scope) offer, are left
   alone.
2. The series lose ``tutor_user_id``, ``assignment_status`` and
   ``assignment_from`` (``version`` bumped).

Sessions go first, from the broken set computed once, so step 1 still sees
the series' tutor. Data only: no schema change (the metadata comparison is
unaffected), no audit rows, no notification. Idempotent: on a database with
no broken series it changes nothing.

**Downgrade:** a no-op.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BROKEN_SERIES = (
    "SELECT s.id FROM class_series s"
    " WHERE s.assignment_status = 'pending'"
    " AND NOT EXISTS (SELECT 1 FROM assignment_offers o"
    " WHERE o.scope = 'series' AND o.status = 'pending'"
    " AND o.class_series_id = s.id AND o.tutor_user_id = s.tutor_user_id)"
)


def upgrade() -> None:
    op.execute(
        f"CREATE TEMPORARY TABLE broken_series ON COMMIT DROP AS {BROKEN_SERIES}"
    )
    op.execute(
        "UPDATE class_sessions cs SET state = 'unassigned', tutor_user_id = NULL,"
        " version = cs.version + 1, updated_at = now()"
        " WHERE cs.series_id IN (SELECT id FROM broken_series)"
        " AND cs.state = 'offered'"
        " AND NOT EXISTS (SELECT 1 FROM assignment_offer_sessions aos"
        " JOIN assignment_offers o ON o.id = aos.offer_id"
        " WHERE aos.class_session_id = cs.id AND o.status = 'pending')"
    )
    op.execute(
        "UPDATE class_series SET tutor_user_id = NULL, assignment_status = NULL,"
        " assignment_from = NULL, version = version + 1, updated_at = now()"
        " WHERE id IN (SELECT id FROM broken_series)"
    )
    op.execute("DROP TABLE broken_series")


def downgrade() -> None:
    # The cleared assignments can't be restored, and 0036's state was the
    # broken one (#263).
    pass
