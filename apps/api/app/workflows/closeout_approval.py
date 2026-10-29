"""The cross-module steps of a closeout approval (#78 B3; architecture
ISSUE-F1; ADR-0002; #94 E1).

``app.workflows.session_closeout_review`` calls :func:`on_closeout_approved`
once per new approval revision, in the same Unit of Work, after the
revision is inserted (a reviewer's approval, or a staff correction of an
approved closeout).

**Package credit (#94 E1, L3, L6; ADR-0006 E10).** On **revision 1 only**
(``supersedes_approval_id is None``) of an ``individual`` session, every
participant's prepaid credit is consumed through
``billing.service.consume_session_credit``: first-expiring-first, all or
nothing, with the session's subject, its local service date (center zone)
and its ``scheduled_minutes``, read under the session lock the approval
already holds (``FOR SHARE``; re-taken here, which the same transaction may
do). Attendance is ignored: an absent participant is consumed too (L6;
#99 credits by the cancellation rules). Every outcome is final and none
raises: ``short`` and ``no_package`` write nothing, and the session sweeper
charges such a session on its next run (an approved, unconsumed session is
never package-covered, L3); ``charged`` means the sweeper posted first
(I9). The approval never posts a charge. Group and course sessions, and
later revisions (a staff correction changes pay, not the family's debit),
do nothing. An unexpected error rolls the approval back (the #92 J10
precedent).

**Lock order:** closeout ``FOR UPDATE`` → session ``FOR SHARE`` → the
billing account ``FOR UPDATE`` (in ``consume_session_credit``, the last
billing lock) → the approval request row (decided after this hook by
``session_closeout_review``). No billing path takes an approval request,
so this never forms a cycle with the sweeper (session → settings →
account), a purchase (idempotency key → product → account) or the expiry
job (session → account).

**M7-04 / M7-08** (tutor pay, still to add): payroll reads the current
revision (``session_closeouts.current_approval_id``); a revision that
supersedes one already paid in a finalized period becomes an adjustment in
the next open period (M7-08).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import local_date

INDIVIDUAL = "individual"


async def on_closeout_approved(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    supersedes_approval_id: uuid.UUID | None,
    class_session_id: uuid.UUID,
) -> None:
    """Run the billing (and later the pay) steps of one approval revision
    (module docstring)."""
    del approval_id
    if supersedes_approval_id is not None:
        return
    locked = await scheduling.lock_session_for_billing(session, class_session_id)
    if locked is None or locked.kind != INDIVIDUAL:
        return
    zone = await administration.get_center_time_zone(session)
    service_date = local_date(locked.starts_at, zone)
    for student_id in locked.participant_ids:
        await billing.consume_session_credit(
            session,
            billing.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=student_id,
                subject_id=locked.subject_id,
                service_date=service_date,
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )
