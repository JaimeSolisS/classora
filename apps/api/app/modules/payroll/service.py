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

**Session pay rules** (#441; :mod:`~app.modules.payroll.pay_rules`): the reads
and writes the ``app.workflows.session_pay_rules`` workflow calls.

**For the calculation:** :func:`rate_in_effect`, :func:`terms_in_effect`,
:func:`session_pay_rule_in_effect`, and the run's batch reads
:func:`rate_versions`, :func:`terms_basis_versions` and
:func:`session_pay_rule_versions`.

**Lines** (#107; :mod:`~app.modules.payroll.lines`, the pure rules in
:mod:`~app.modules.payroll.calculation`): :func:`lock_open_period`,
:func:`replace_lines`, :func:`period_lines`, and :func:`draft_lines` with its
frozen values.

**Record policies** (:mod:`~app.modules.payroll.permissions`):
:func:`can_view_compensation`, :func:`can_write_compensation`.
"""

from app.modules.payroll.calculation import (
    ApprovedSession,
    LineDraft,
    PayableCancellation,
    PayBasis,
    draft_lines,
)
from app.modules.payroll.errors import PayrollOwnCompensationError
from app.modules.payroll.lines import (
    REBUILT_KINDS,
    lock_open_period,
    period_lines,
    replace_lines,
)
from app.modules.payroll.pay_rules import (
    create_session_pay_rule,
    delete_session_pay_rule,
    get_session_pay_rule_overview,
    session_pay_rule_in_effect,
    session_pay_rule_versions,
)
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
    rate_versions,
    terms_basis_versions,
    terms_in_effect,
)
from app.modules.payroll.settings import (
    biweekly_anchor,
    payroll_starts_on,
    salary_proration_basis,
)

__all__ = [
    "REBUILT_KINDS",
    "ApprovedSession",
    "LineDraft",
    "PayBasis",
    "PayableCancellation",
    "PayrollOwnCompensationError",
    "add_compensation_rate",
    "add_pay_terms",
    "biweekly_anchor",
    "can_view_compensation",
    "can_write_compensation",
    "compensation_summaries",
    "create_session_pay_rule",
    "delete_compensation_rate",
    "delete_pay_terms",
    "delete_session_pay_rule",
    "draft_lines",
    "get_session_pay_rule_overview",
    "list_compensation_rates",
    "list_pay_terms",
    "lock_open_period",
    "lock_payroll_periods",
    "natural_bounds",
    "open_due_periods",
    "payroll_starts_on",
    "period_lines",
    "rate_in_effect",
    "rate_versions",
    "replace_lines",
    "salary_proration_basis",
    "session_pay_rule_in_effect",
    "session_pay_rule_versions",
    "terms_basis_versions",
    "terms_in_effect",
    "tutors_with_terms",
]
