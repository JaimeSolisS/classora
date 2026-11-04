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
    "LabelledCategory",
    "VendorInactiveError",
    "VendorUnknownError",
    "add_category",
    "add_vendor",
    "category_label",
    "change_category",
    "change_vendor",
    "create_expense",
    "expense_figures",
    "expense_status",
    "get_expense",
    "insert_expense",
    "list_categories",
    "list_expenses",
    "list_vendors",
    "post_adjustment",
    "record_payment",
    "reverse_payment",
    "usable_category",
    "usable_vendor",
]
