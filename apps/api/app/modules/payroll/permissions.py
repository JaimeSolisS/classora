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

The role and permission codes are the identity catalog's, repeated here
because this module imports no other module's internals (ADR-0002);
``tests/unit/test_payroll_permissions.py`` checks they match.
"""

import uuid
from typing import Final

from app.core.authz import Principal

OWNER: Final = "owner"
TUTOR: Final = "tutor"
PAYROLL_VIEW: Final = "payroll.view"


def can_view_compensation(principal: Principal, tutor_user_id: uuid.UUID) -> bool:
    """Whether ``principal`` may read this tutor's terms and rates."""
    if principal.has(PAYROLL_VIEW):
        return True
    return principal.user_id == tutor_user_id and TUTOR in principal.roles


def can_write_compensation(principal: Principal, tutor_user_id: uuid.UUID) -> bool:
    """Whether this ``payroll.manage`` holder may write this tutor's terms and
    rates: anyone else's, and their own only as an Owner."""
    return principal.user_id != tutor_user_id or OWNER in principal.roles
