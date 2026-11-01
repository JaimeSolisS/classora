"""The payroll module's public interface (ADR-0002).

**Compensation** (#105; :mod:`~app.modules.payroll.rates`): the reads and
writes the ``app.workflows.payroll_compensation`` workflow calls.

**Settings** (#106; :mod:`~app.modules.payroll.settings`): the readers
:func:`payroll_starts_on`, :func:`biweekly_anchor` and
:func:`salary_proration_basis`. The routes are
:mod:`~app.modules.payroll.settings_router`.

**Periods** (#106; :mod:`~app.modules.payroll.period_rows`,
:mod:`~app.modules.payroll.periods`): :func:`tutors_with_terms` and
:func:`open_due_periods` for the ``payroll:open_periods`` job,
:func:`lock_payroll_periods` for every writer of terms (only
:mod:`~app.modules.payroll.rates` today, #111 next), and the pure
:func:`natural_bounds`, which defines ``natural_days`` for #108.

**For the calculation:** :func:`rate_in_effect` and :func:`terms_in_effect`.

**Record policies** (:mod:`~app.modules.payroll.permissions`):
:func:`can_view_compensation`, :func:`can_write_compensation`.
"""

from app.modules.payroll.errors import PayrollOwnCompensationError
from app.modules.payroll.period_rows import (
    lock_payroll_periods,
    open_due_periods,
    tutors_with_terms,
)
from app.modules.payroll.periods import natural_bounds
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
from app.modules.payroll.settings import (
    biweekly_anchor,
    payroll_starts_on,
    salary_proration_basis,
)

__all__ = [
    "PayrollOwnCompensationError",
    "add_compensation_rate",
    "add_pay_terms",
    "biweekly_anchor",
    "can_view_compensation",
    "can_write_compensation",
    "compensation_summaries",
    "delete_compensation_rate",
    "delete_pay_terms",
    "list_compensation_rates",
    "list_pay_terms",
    "lock_payroll_periods",
    "natural_bounds",
    "open_due_periods",
    "payroll_starts_on",
    "rate_in_effect",
    "salary_proration_basis",
    "terms_in_effect",
    "tutors_with_terms",
]
