"""The cross-module steps of a closeout approval (#78 B3; architecture
ISSUE-F1; ADR-0002).

``app.workflows.session_closeout_review`` calls :func:`on_closeout_approved`
once per new approval revision, in the same Unit of Work, after the
revision is inserted (a reviewer's approval, or a staff correction of an
approved closeout).

Today it is a **no-op** (the ``session_cancellation.py`` precedent). The
steps to add here:

- **M6 / ISSUE-F1** (package credits): debit the family's package for the
  session on **revision 1 only** (``supersedes_approval_id is None``);
  later revisions change pay, not the family's debit;
- **M7-04 / M7-08** (tutor pay): payroll reads the current revision
  (``session_closeouts.current_approval_id``); a revision that supersedes
  one already paid in a finalized period becomes an adjustment in the next
  open period (M7-08).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def on_closeout_approved(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    supersedes_approval_id: uuid.UUID | None,
) -> None:
    """Run the billing and pay steps of one approval revision (none yet)."""
    del session, approval_id, supersedes_approval_id
