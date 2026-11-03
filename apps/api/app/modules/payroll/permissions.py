"""Record policies of tutor compensation (#105 E5, E6; ADR-0017 E10, E12).

Pure functions, no I/O.

**Who reads a tutor's compensation** (:func:`can_view_compensation`): a holder
of ``payroll.view`` reads any tutor; the tutor reads their own while holding
the ``tutor`` role. Anyone else gets the same ``404`` as a missing id,
including Administrators and Coordinators who hold ``tutor.view``, and
Administrators delegated ``payroll.finalize``, who see amounts only in the
period review (#110).

**Who writes** (:func:`can_write_compensation`; the routes already require
``payroll.manage``): nobody writes their own pay, except an Owner.

**Who reads a period (#110 E8):** a ``payroll.view`` holder reads every period,
and a ``payroll.finalize`` holder without it, a delegated Administrator, reads
what there is to review: :data:`REVIEWABLE_STATUSES` (:func:`can_read_period`).
Nothing else opens to them: no terms, rates, items or unresolved work.

**Who finalizes (ADR-0017 E10):** :func:`finalize_refusal`. The preparer can't,
and nobody finalizes their own pay. An Owner is excepted from both, the
``closeout_self_review`` precedent, so for a sole Owner the audit trail is the
only control. Preparing your own pay is allowed (#110 E11).

The role and permission codes are the identity catalog's, repeated here
because this module imports no other module's internals (ADR-0002);
``tests/unit/test_payroll_permissions.py`` checks they match.
"""

import uuid
from typing import Final, Literal

from app.core.authz import Principal

OWNER: Final = "owner"
TUTOR: Final = "tutor"
PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_PREPARE: Final = "payroll.prepare"
PAYROLL_FINALIZE: Final = "payroll.finalize"
REVIEWABLE_STATUSES: Final = ("prepared", "finalized")


def can_view_compensation(principal: Principal, tutor_user_id: uuid.UUID) -> bool:
    """Whether ``principal`` may read this tutor's terms and rates."""
    if principal.has(PAYROLL_VIEW):
        return True
    return principal.user_id == tutor_user_id and TUTOR in principal.roles


def can_write_compensation(principal: Principal, tutor_user_id: uuid.UUID) -> bool:
    """Whether this ``payroll.manage`` holder may write this tutor's terms and
    rates: anyone else's, and their own only as an Owner."""
    return principal.user_id != tutor_user_id or OWNER in principal.roles


def can_list_periods(principal: Principal) -> bool:
    """Whether ``principal`` reaches the period reads at all (#110 F1)."""
    return principal.has(PAYROLL_VIEW) or principal.has(PAYROLL_FINALIZE)


def can_read_period(principal: Principal, status: str) -> bool:
    """Whether ``principal`` reads a period in this ``status`` (#110 E8)."""
    return principal.has(PAYROLL_VIEW) or (
        principal.has(PAYROLL_FINALIZE) and status in REVIEWABLE_STATUSES
    )


def can_return_period(principal: Principal) -> bool:
    """Either permission returns a period (#110 E7)."""
    return principal.has(PAYROLL_PREPARE) or principal.has(PAYROLL_FINALIZE)


def finalize_refusal(
    principal: Principal,
    *,
    tutor_user_id: uuid.UUID,
    prepared_by_user_id: uuid.UUID | None,
) -> Literal["preparer", "own_pay"] | None:
    """Why this ``payroll.finalize`` holder can't finalize, or ``None``."""
    if OWNER in principal.roles:
        return None
    if principal.user_id == prepared_by_user_id:
        return "preparer"
    if principal.user_id == tutor_user_id:
        return "own_pay"
    return None
