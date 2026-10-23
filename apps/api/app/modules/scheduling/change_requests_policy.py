"""The record policy of change requests (#62 S1, S2; ADR-0005 D2, D9).

Pure functions, no I/O: the caller loads the session, its participants and
the caller's #59 family outcome for each participant
(:mod:`~app.modules.scheduling.requests_policy`) once per request.

**Create** (:func:`create_access`). A caller may ask to cancel or move
session X when:

- **tutor:** they hold the ``tutor`` role and X is theirs
  (``X.tutor_user_id``), in any state (the state is refused later with
  ``409 class_session_not_changeable``, B2 step 7). ``initiated_by =
  "tutor"``. A user who is both X's tutor and a family member of a
  participant acts as the tutor **(choice)**;
- **family:** X is family-visible (``FAMILY_VISIBLE_STATES``: ``confirmed``
  or ``completed``, #52 E3) and the caller passes the #59 S1 policy for one
  of X's participants (a guardian of an active linked profile, with the
  ``guardian_academic_access`` consent for an adult, or the student
  themselves). ``initiated_by = "family"``, ``student_id`` = that
  participant (the first by id when several qualify);
- a linked guardian of an adult participant without the consent (and no
  other way in) → :attr:`CreateOutcome.CONSENT_REQUIRED` (``403
  student_consent_required``);
- **nobody else** (staff edit the schedule directly, other families, other
  tutors, a session families can't see) → :attr:`CreateOutcome.HIDDEN`: the
  same ``404`` as an unknown id.

**Read** (:func:`may_read`): a family caller reads the ``family`` requests
of the students they may act for (S1), so the child's other guardians see
them too; a tutor reads only the ``tutor`` requests they made. Tutor
requests are never visible to families (E5). Only the requester may
withdraw (:func:`may_withdraw`); anyone else who can read it gets ``404``.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, Literal

from app.core.authz import Principal
from app.modules.scheduling.requests_policy import FamilyAccess
from app.modules.scheduling.states import FAMILY_VISIBLE_STATES, SessionState

TUTOR: Final = "tutor"

Initiator = Literal["family", "tutor"]


class CreateOutcome(Enum):
    ALLOWED = "allowed"
    CONSENT_REQUIRED = "consent_required"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class CreateAccess:
    """The S1/S2 answer for one create."""

    outcome: CreateOutcome
    initiated_by: Initiator | None = None
    student_id: uuid.UUID | None = None


HIDDEN: Final = CreateAccess(CreateOutcome.HIDDEN)


def is_sessions_tutor(principal: Principal, tutor_user_id: uuid.UUID | None) -> bool:
    """The caller holds ``tutor`` and teaches the session."""
    return TUTOR in principal.roles and tutor_user_id == principal.user_id


def is_family_visible_state(state: str) -> bool:
    try:
        return SessionState(state) in FAMILY_VISIBLE_STATES
    except ValueError:
        return False


def create_access(
    principal: Principal,
    *,
    state: str,
    tutor_user_id: uuid.UUID | None,
    family: Mapping[uuid.UUID, FamilyAccess],
) -> CreateAccess:
    """S1/S2 for creating a request on a session in ``state`` taught by
    ``tutor_user_id``; ``family`` maps each participant the caller is
    linked to (or is) to their #59 outcome (absent participants are
    hidden)."""
    if is_sessions_tutor(principal, tutor_user_id):
        return CreateAccess(CreateOutcome.ALLOWED, initiated_by="tutor")
    if not is_family_visible_state(state):
        return HIDDEN
    allowed = sorted(
        student for student, access in family.items() if access is FamilyAccess.ALLOWED
    )
    if allowed:
        return CreateAccess(
            CreateOutcome.ALLOWED, initiated_by="family", student_id=allowed[0]
        )
    if any(access is FamilyAccess.CONSENT_REQUIRED for access in family.values()):
        return CreateAccess(CreateOutcome.CONSENT_REQUIRED)
    return HIDDEN


def may_read(
    principal: Principal,
    *,
    initiated_by: str,
    requested_by_user_id: uuid.UUID,
    student_id: uuid.UUID | None,
    family_students: Collection[uuid.UUID],
) -> bool:
    """S2 read visibility of one request; ``family_students`` are the
    students the caller may act for (S1, already filtered by the adult
    rule)."""
    if initiated_by == "tutor":
        return TUTOR in principal.roles and requested_by_user_id == principal.user_id
    return student_id is not None and student_id in family_students


def may_withdraw(principal: Principal, *, requested_by_user_id: uuid.UUID) -> bool:
    """D3: only the requester (the caller already passed :func:`may_read`)."""
    return requested_by_user_id == principal.user_id
