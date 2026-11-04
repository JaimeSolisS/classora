"""Finance errors (#114). Each ``AppError`` renders as Problem Details
(``app.core.errors``)."""

from app.core.errors import AppError


class ExpenseCategoryExistsError(AppError):
    """Another category has this name, or a keyed category shows it as its
    Spanish or English label, ignoring case (#114 E3)."""

    status = 409
    code = "expense_category_exists"
    detail = "An expense category with this name already exists."


class VendorExistsError(AppError):
    """Another vendor has this name, ignoring case (#114 E4)."""

    status = 409
    code = "vendor_exists"
    detail = "A vendor with this name already exists."


class ExpenseCategoryUnknownError(AppError):
    """No such category (#114 E7)."""

    status = 422
    code = "expense_category_unknown"
    detail = "This expense category doesn't exist."


class ExpenseCategoryInactiveError(AppError):
    """The category was deactivated, so no new expense can use it (#114 E7)."""

    status = 409
    code = "expense_category_inactive"
    detail = "This expense category is inactive."


class VendorUnknownError(AppError):
    """No such vendor (#114 E7)."""

    status = 422
    code = "vendor_unknown"
    detail = "This vendor doesn't exist."


class VendorInactiveError(AppError):
    """The vendor was deactivated, so no new expense can use it (#114 E7)."""

    status = 409
    code = "vendor_inactive"
    detail = "This vendor is inactive."
