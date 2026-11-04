"""Finance errors (#114). Each ``AppError`` renders as Problem Details
(``app.core.errors``)."""

import uuid

from app.core.errors import AppError
from app.core.money import Money


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


# --- Expenses (#115) ----------------------------------------------------------------


class _FieldRuleError(AppError):
    """A ``422`` whose code is also the one field error at ``body.<field>``."""

    status = 422
    field = ""

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", self.field],
                    "code": self.code,
                    "message": self.detail or "",
                }
            ]
        )


class IncurredOnInFutureError(_FieldRuleError):
    """The expense is dated after the center's local today (#115 E2)."""

    code = "incurred_on_in_future"
    field = "incurred_on"
    detail = "The expense date can't be after today."


class DueOnBeforeIncurredOnError(_FieldRuleError):
    """The due date is before the expense date (#115 E2)."""

    code = "due_on_before_incurred_on"
    field = "due_on"
    detail = "The due date can't be before the expense date."


class PaidOnInFutureError(_FieldRuleError):
    """The payment is dated after the center's local today (#115 E2). Any
    earlier date passes, before the expense date included."""

    code = "paid_on_in_future"
    field = "paid_on"
    detail = "The payment date can't be after today."


class ExpenseAmountOutOfRangeError(AppError):
    """The adjustment would take the expense's net beyond the money range
    (#115 F1): ``422 validation_error`` with ``money_out_of_range`` at
    ``body.amount``, the body's own code for an amount out of range."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "amount"],
                    "code": "money_out_of_range",
                    "message": "The expense's amount would be out of range.",
                }
            ]
        )


class IncurredRangeInvalidError(AppError):
    """``incurred_to`` is before ``incurred_from`` (#115 C1): ``422
    validation_error`` with ``range_invalid`` at ``query.incurred_to``."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["query", "incurred_to"],
                    "code": "range_invalid",
                    "message": "The last date must not be before the first.",
                }
            ]
        )


class ExpenseVoidError(AppError):
    """The expense is void (its net is ``0.00``): no payment or adjustment,
    ever; it is re-entered as a new expense (#115 E4)."""

    status = 409
    code = "expense_void"
    detail = "This expense is void. Enter a new expense instead."


class ExpensePaymentDuplicateError(AppError):
    """A payment of this expense without a reversal has the same method and
    reference, ignoring case and surrounding spaces (#115 E5)."""

    status = 409
    code = "expense_payment_duplicate"
    detail = "A payment with this method and reference is already recorded."

    def __init__(self, payment_id: uuid.UUID) -> None:
        super().__init__(details={"payment_id": str(payment_id)})


class ExpensePaymentExceedsOpenError(AppError):
    """The payments would add up to more than the expense's net (#115 D3,
    X1). ``details.open`` is what can still be paid."""

    status = 409
    code = "expense_payment_exceeds_open"
    detail = "The payment is more than what is still owed on this expense."

    def __init__(self, open_amount: Money) -> None:
        super().__init__(details={"open": str(open_amount)})


class ExpenseAdjustmentExceedsOpenError(AppError):
    """A negative adjustment larger than the open amount: it would take the
    net below what was paid (#115 F1, F3, X1). Reverse a payment first.
    ``details.open`` is how far the amount may go down."""

    status = 409
    code = "expense_adjustment_exceeds_open"
    detail = "The adjustment takes the expense below what was already paid."

    def __init__(self, open_amount: Money) -> None:
        super().__init__(details={"open": str(open_amount)})


class ExpensePaymentAlreadyReversedError(AppError):
    """The payment already has its reversal (#115 E1)."""

    status = 409
    code = "expense_payment_already_reversed"
    detail = "This payment was already reversed."
