"""The payroll module's public interface (ADR-0002).

**Compensation** (#105; :mod:`~app.modules.payroll.rates`): the reads and
writes the ``app.workflows.payroll_compensation`` workflow calls.

**For the calculation:** :func:`rate_in_effect` and :func:`terms_in_effect`.

**Record policies** (:mod:`~app.modules.payroll.permissions`):
:func:`can_view_compensation`, :func:`can_write_compensation`.
"""

from app.modules.payroll.errors import PayrollOwnCompensationError
from app.modules.payroll.permissions import (
    can_view_compensation,
    can_write_compensation,
)
from app.modules.payroll.rates import (
    add_compensation_rate,
    add_pay_terms,
    compensation_summaries,
    delete_compensation_rate,
    delete_pay_terms,
    list_compensation_rates,
    list_pay_terms,
    rate_in_effect,
    terms_in_effect,
)

__all__ = [
    "PayrollOwnCompensationError",
    "add_compensation_rate",
    "add_pay_terms",
    "can_view_compensation",
    "can_write_compensation",
    "compensation_summaries",
    "delete_compensation_rate",
    "delete_pay_terms",
    "list_compensation_rates",
    "list_pay_terms",
    "rate_in_effect",
    "terms_in_effect",
]
