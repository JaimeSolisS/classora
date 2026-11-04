"""The finance module's public interface (ADR-0002).

**Expense configuration** (#114): the reads and writes behind
:mod:`~app.modules.finance.router`
(:mod:`~app.modules.finance.categories`, :mod:`~app.modules.finance.vendors`).

**For new expenses and templates** (#114 E7; #115, #116):
:func:`usable_category` and :func:`usable_vendor` return the row read
``FOR SHARE``, or raise ``422 expense_category_unknown`` / ``vendor_unknown``
for an unknown id and ``409 expense_category_inactive`` / ``vendor_inactive``
for an inactive one. Call them before inserting the row that references them,
in the same Unit of Work.

**Operating expenses** (#115): the reads and writes behind
:mod:`~app.modules.finance.expenses_router`. :func:`insert_expense` is the one
way to add an expense: the route and the recurring job (#116, with
``created_by_user_id=None``) both use it, so ``usable_*`` and the date rules
govern every row. :func:`expense_figures` gives every expense's ``net``,
``paid``, ``open`` and ``status`` as one SQL subquery (#117's payables reuse
it), and :func:`expense_status` is the same rule in Python.

**Recurring templates** (#116): the reads and writes behind
:mod:`~app.modules.finance.templates_router`
(:mod:`~app.modules.finance.templates`), and what the job
``finance:generate_recurring_expenses`` composes
(``app.workflows.recurring_expenses``): :func:`due_template_ids` (the keyset
candidates, no lock) and :func:`generate_template_periods` (one template's
unit: the template ``FOR UPDATE``, a savepoint per due period through
:func:`insert_expense`, then ``next_period_start``). The grid is
:func:`period_start`, :func:`first_on_or_after` and :func:`next_after`.

**For documents** (#117): :func:`category_label` gives the catalog label of a
seeded category while it holds its key, else the stored name.
"""

from app.modules.finance.categories import (
    LabelledCategory,
    add_category,
    category_label,
    change_category,
    list_categories,
    usable_category,
)
from app.modules.finance.errors import (
    ExpenseCategoryInactiveError,
    ExpenseCategoryUnknownError,
    VendorInactiveError,
    VendorUnknownError,
)
from app.modules.finance.expense_adjustments import post_adjustment
from app.modules.finance.expense_payments import record_payment, reverse_payment
from app.modules.finance.expenses import (
    ExpenseFilters,
    create_expense,
    expense_figures,
    expense_status,
    get_expense,
    insert_expense,
    list_expenses,
    unreversed,
)
from app.modules.finance.template_periods import (
    Frequency,
    first_on_or_after,
    next_after,
    period_start,
)
from app.modules.finance.templates import (
    CANDIDATE_PAGE as TEMPLATE_CANDIDATE_PAGE,
)
from app.modules.finance.templates import (
    GeneratedPeriods,
    TemplateChange,
    TemplateFilters,
    change_template,
    create_template,
    due_template_ids,
    generate_template_periods,
    get_template,
    list_templates,
)
from app.modules.finance.vendors import (
    add_vendor,
    change_vendor,
    list_vendors,
    usable_vendor,
)

__all__ = [
    "ExpenseCategoryInactiveError",
    "ExpenseCategoryUnknownError",
    "ExpenseFilters",
    "Frequency",
    "GeneratedPeriods",
    "LabelledCategory",
    "TEMPLATE_CANDIDATE_PAGE",
    "TemplateChange",
    "TemplateFilters",
    "VendorInactiveError",
    "VendorUnknownError",
    "add_category",
    "add_vendor",
    "category_label",
    "change_category",
    "change_template",
    "change_vendor",
    "create_expense",
    "create_template",
    "due_template_ids",
    "expense_figures",
    "expense_status",
    "first_on_or_after",
    "generate_template_periods",
    "get_expense",
    "get_template",
    "insert_expense",
    "list_categories",
    "list_expenses",
    "list_templates",
    "list_vendors",
    "next_after",
    "period_start",
    "post_adjustment",
    "record_payment",
    "reverse_payment",
    "unreversed",
    "usable_category",
    "usable_vendor",
]
