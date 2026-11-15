"""Reminder stages and their windows (#97 F1, N1 (a); ADR-0006 E11;
data-model §3.27). Pure: no I/O.

With ``D`` = a charge's **effective** due date (data-model §3.11) and
``today`` = the center's local date, a charge with something open is in at
most one stage:

- ``due_soon``: ``D − 3 ≤ today ≤ D`` ("due soon / due today");
- ``overdue``: ``D + 1 ≤ today ≤ D + 13`` (the first days it is overdue,
  matching the ``overdue`` status, which starts at ``D + 1``);
- ``overdue_followup``: ``today ≥ D + 14``.

The reminder job (``app.workflows.charge_reminders``) sends only the stage
of the window a charge is in **now**, at most once per ``(charge_id,
stage)`` (``charge_reminders``' natural key): a missed window is never
caught up, and a due-date change after a stage was sent never sends that
stage again. The stage codes are letters and ``_`` only, so the web's
``_<kind>`` message variants apply (#55 F4). ``charge_reminders``' CHECK
is generated from :data:`REMINDER_STAGES`.
"""

from datetime import date, timedelta
from typing import Final, Literal

type ReminderStage = Literal["due_soon", "overdue", "overdue_followup"]

DUE_SOON: Final = "due_soon"
OVERDUE: Final = "overdue"
OVERDUE_FOLLOWUP: Final = "overdue_followup"
REMINDER_STAGES: Final[tuple[ReminderStage, ...]] = (
    DUE_SOON,
    OVERDUE,
    OVERDUE_FOLLOWUP,
)
"""The stage codes, in the order a charge passes through them."""

DUE_SOON_LEAD_DAYS: Final = 3
"""``due_soon`` starts this many days before the due date."""
FOLLOWUP_AFTER_DAYS: Final = 14
"""``overdue_followup`` starts this many days after the due date."""


def reminder_stage(effective_due: date, today: date) -> ReminderStage | None:
    """The stage whose window ``today`` is in for a charge due on
    ``effective_due``, or ``None`` before ``due_soon`` starts. The caller
    checks that something is open."""
    if today < effective_due - timedelta(days=DUE_SOON_LEAD_DAYS):
        return None
    if today <= effective_due:
        return DUE_SOON
    if today < effective_due + timedelta(days=FOLLOWUP_AFTER_DAYS):
        return OVERDUE
    return OVERDUE_FOLLOWUP
