"""Billing errors (#89 B9, #90 C6). Each ``AppError`` renders as Problem
Details (``app.core.errors``) and carries no record data beyond the ids
(and, for ``charge_net_negative``, the amounts) in ``details``."""

import uuid
from collections.abc import Sequence
from datetime import date

from app.core.errors import AppError, FieldError
from app.core.money import Money


class PriceCatalogValidationError(AppError):
    """``422 validation_error`` with the reference errors of a catalog write
    (#89 B3): unknown or inactive subject and level, unknown cohort or
    group, and a scope of another subject, all in one response."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class ServicePriceExistsError(AppError):
    """A catalog item with the same key already exists (#89 B3);
    ``details.service_price_id`` is its id."""

    status = 409
    code = "service_price_exists"
    detail = (
        "A price with this class type, pricing model, subject, level, duration"
        " and scope already exists."
    )

    def __init__(self, service_price_id: uuid.UUID | None) -> None:
        super().__init__(
            details=None
            if service_price_id is None
            else {"service_price_id": str(service_price_id)}
        )


class PriceEffectiveFromInvalidError(AppError):
    """A later version must start on or after the center's local today and
    after the item's latest version (#89 B5, E3)."""

    status = 422
    code = "price_effective_from_invalid"
    detail = (
        "A new version must start today or later, and after the price's latest version."
    )

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "effective_from"],
                    "code": self.code,
                    "message": self.detail or "",
                }
            ]
        )


class PriceVersionInEffectError(AppError):
    """Only an upcoming version can be deleted (#89 B6, E3)."""

    status = 409
    code = "price_version_in_effect"
    detail = (
        "This version is in effect or in the past: only an upcoming version can"
        " be deleted."
    )

    def __init__(self) -> None:
        super().__init__()


class PriceVersionInUseError(AppError):
    """The version is referenced by a charge or a package (the
    ``price_versions`` guard, SQLSTATE 42501; #89 B6, E2)."""

    status = 409
    code = "price_version_in_use"
    detail = "This version is used by a charge or a package and can't be deleted."

    def __init__(self) -> None:
        super().__init__()


class ChargeNetNegativeError(AppError):
    """An adjustment would take a charge's net below ``0.00`` (I1; #90 C4).
    ``details``: the charge id, its current net and the refused amount.
    Nothing is written."""

    status = 409
    code = "charge_net_negative"
    detail = "This adjustment would take the charge below zero."

    def __init__(self, charge_id: uuid.UUID, net: Money, amount: Money) -> None:
        super().__init__(
            details={
                "charge_id": str(charge_id),
                "net": str(net),
                "amount": str(amount),
            }
        )


class StudentHasNoFamilyError(Exception):
    """The student belongs to no family, so there is no billing account to
    post to (#86 E4; #90 C3). Not an ``AppError``: #91 lists the item as
    ``no_billing_account``."""

    def __init__(self, student_id: uuid.UUID) -> None:
        super().__init__("student has no family")
        self.student_id = student_id


# --- Discounts and custom prices (#95 F1) --------------------------------------------


class BillingValidationError(AppError):
    """``422 validation_error`` with the rule or reference errors of a
    discount or custom price write (#95 D3, D5, D6, E3), all in one
    response."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class DiscountNameExistsError(AppError):
    """Another discount has this name, ignoring case (#95 D3)."""

    status = 409
    code = "discount_name_exists"
    detail = "Another discount already has this name."

    def __init__(self) -> None:
        super().__init__()


class DiscountAssignmentExistsError(AppError):
    """The student or family is already assigned to the discount (#95 D6)."""

    status = 409
    code = "discount_assignment_exists"
    detail = "This student or family is already assigned to the discount."

    def __init__(self) -> None:
        super().__init__()


class AudienceNotAssignedError(AppError):
    """Only an ``assigned`` discount has assignments (#95 D6)."""

    status = 422
    code = "audience_not_assigned"
    detail = "This discount is for everyone: it has no assignments."

    def __init__(self) -> None:
        super().__init__()


class CustomPriceOverlapError(AppError):
    """Another custom price of the same student or family for the same item
    overlaps these dates (#95 E3, E5; also the exclusion constraints)."""

    status = 409
    code = "custom_price_overlap"
    detail = (
        "Another custom price for this student or family and this price"
        " overlaps these dates."
    )

    def __init__(self) -> None:
        super().__init__()


def _field(field: str, code: str, message: str) -> list[FieldError]:
    return [{"path": ["body", field], "code": code, "message": message}]


class CustomPriceEffectiveFromInvalidError(AppError):
    """A custom price starts on or after the center's local today (#95 H7:
    no backdating)."""

    status = 422
    code = "custom_price_effective_from_invalid"
    detail = "A custom price starts today or later."

    def __init__(self) -> None:
        super().__init__(errors=_field("effective_from", self.code, self.detail or ""))


class CustomPriceExpiryInvalidError(AppError):
    """The end date is before today, before the start, or before a session
    already charged at this price (#95 E5, H7)."""

    status = 422
    code = "custom_price_expiry_invalid"
    detail = (
        "The end date must be today or later, on or after the start, and on or"
        " after the last service date charged at this price."
    )

    def __init__(self) -> None:
        super().__init__(errors=_field("expires_on", self.code, self.detail or ""))


class CustomPriceInEffectError(AppError):
    """Only an upcoming custom price can be deleted (#95 E6)."""

    status = 409
    code = "custom_price_in_effect"
    detail = (
        "This custom price is in effect or in the past: end it instead of deleting it."
    )

    def __init__(self) -> None:
        super().__init__()


class CustomPriceInUseError(AppError):
    """A charge uses the custom price (the guard, SQLSTATE 42501; #95 E6)."""

    status = 409
    code = "custom_price_in_use"
    detail = "This custom price is used by a charge and can't be deleted."

    def __init__(self) -> None:
        super().__init__()


# --- Tuition plans (#93 D3-D8) -------------------------------------------------------


class _FieldRuleError(AppError):
    """A ``422`` whose code is also the one field error at ``field``."""

    status = 422
    field = ""

    def __init__(self, field: str | None = None) -> None:
        super().__init__(
            errors=_field(field or self.field, self.code, self.detail or "")
        )


class TuitionPlanPriceInvalidError(_FieldRuleError):
    """The pinned item is unknown, not ``per_period``, an ``individual``
    item with an enrollment, a ``group`` item without one, or a group item
    neither scoped to the enrollment's group nor unscoped for its subject
    (#93 D3, K6)."""

    code = "tuition_plan_price_invalid"
    field = "service_price_id"
    detail = (
        "Choose a per-period price: an individual one for a plan without a"
        " group, or one for the group (or its subject) for a group plan."
    )


class TuitionPlanEnrollmentInvalidError(_FieldRuleError):
    """The group enrollment is unknown, another student's, or withdrawn
    (#93 D3, K6)."""

    code = "tuition_plan_enrollment_invalid"
    field = "group_enrollment_id"
    detail = "Choose an active group enrollment of this student."


class TuitionPlanStartsOnInvalidError(_FieldRuleError):
    """``starts_on`` is more than 31 days before or 366 days after the
    center's local today, or before the linked enrollment's local date
    (#93 D3, K5)."""

    code = "tuition_plan_starts_on_invalid"
    field = "starts_on"
    detail = (
        "The plan starts at most 31 days ago and at most 366 days ahead, and"
        " not before the group enrollment."
    )


class TuitionPlanEndsOnInvalidError(_FieldRuleError):
    """``ends_on`` is not the end of one of the plan's periods, or (on a
    ``PATCH``) ends before a posted period or the first period (#93 D3, D5;
    K3: no proration)."""

    code = "tuition_plan_ends_on_invalid"
    field = "ends_on"
    detail = (
        "The plan ends on the last day of one of its periods, not before a"
        " period already charged."
    )


class TuitionPauseInvalidError(_FieldRuleError):
    """A pause starts on a period start after the last posted period, not
    before today's period and not after the plan's end; it ends on a period
    end (#93 D7, K4)."""

    code = "tuition_pause_invalid"
    field = "starts_on"
    detail = (
        "A pause starts on the first day of a period that isn't charged yet and"
        " isn't in the past, and ends on the last day of a period."
    )


class TuitionResumeInvalidError(_FieldRuleError):
    """``resume_on`` is not a period start, is before the pause's start
    (or equal to it once the pause began) or before today's period (#93
    D8)."""

    code = "tuition_resume_invalid"
    field = "resume_on"
    detail = (
        "Resume on the first day of a period after the pause began, not in the past."
    )


class StudentHasNoFamilyAccountError(AppError):
    """The student belongs to no family, so a plan has no account to bill
    (#93 D3, K9)."""

    status = 409
    code = "student_has_no_family"
    detail = "The student isn't in a family yet: add them to one first."

    def __init__(self) -> None:
        super().__init__()


class _Conflict(AppError):
    status = 409

    def __init__(self) -> None:
        super().__init__()


class TuitionPlanOverlapError(_Conflict):
    """Another plan of the same group enrollment, or of the same student and
    price, overlaps these dates (#93 A3; also the exclusion constraints)."""

    code = "tuition_plan_overlap"
    detail = "Another tuition plan for this student and price overlaps these dates."


class TuitionPlanEnrollmentFeeChargedError(_Conflict):
    """The group enrollment already has a flat fee with a net above
    ``0.00`` (#93 K6)."""

    code = "tuition_plan_enrollment_fee_charged"
    detail = "This group enrollment already has a flat fee charged."


class TuitionPlanSessionsChargedError(_Conflict):
    """The student has session charges with a net above ``0.00`` on group
    sessions of the enrollment inside the plan's window (#93 K6)."""

    code = "tuition_plan_sessions_charged"
    detail = (
        "Sessions of this group enrollment inside the plan's dates are already"
        " charged per session."
    )


class TuitionPlanEndedError(_Conflict):
    """The plan has ended: it can't be extended or paused; create a new one
    (#93 D5, D7)."""

    code = "tuition_plan_ended"
    detail = "This plan has ended. Create a new plan instead."


class TuitionPlanHasChargesError(_Conflict):
    """A plan with charges can't be deleted; end it instead (#93 D6)."""

    code = "tuition_plan_has_charges"
    detail = "This plan has charges and can't be deleted: end it instead."


class TuitionPauseOverlapError(_Conflict):
    """The pause overlaps another pause of the plan (#93 D7)."""

    code = "tuition_pause_overlap"
    detail = "This pause overlaps another pause of the plan."


class TuitionPauseNotOpenError(_Conflict):
    """The plan has no open pause to resume (#93 D8)."""

    code = "tuition_pause_not_open"
    detail = "This plan has no open pause."


# --- Prepaid packages (#94) ----------------------------------------------------------


class PackageProductInactiveError(_Conflict):
    """The package product is deactivated: it can't be sold (#94 C2)."""

    code = "package_product_inactive"
    detail = "This package is no longer sold. Choose an active one."


class PackageNotOfferedError(_Conflict):
    """The product has no price version in effect today, or the version in
    effect says "not offered" (``null``) (#94 C2, L8)."""

    code = "package_not_offered"
    detail = "This package has no price today, so it can't be sold."


class SessionSettledByPackageError(Exception):
    """A ``session`` charge was asked for a ``(class_session_id,
    student_id)`` a package already paid for (``consume`` row; #94 D3, I9).
    A plain exception: the session sweeper counts it as ``package_covered``;
    nothing was written."""

    def __init__(self, class_session_id: uuid.UUID, student_id: uuid.UUID) -> None:
        super().__init__("the session is settled by a package")
        self.class_session_id = class_session_id
        self.student_id = student_id


# --- Payments and allocations (#96 B4, C2; M3-M5) ------------------------------------


class AllocationExceedsOpenError(_Conflict):
    """An explicit allocation line is above the charge's open amount under
    the account lock (I2; #96 M3). ``details``: the charge id and its open
    amount. Nothing is written."""

    code = "allocation_exceeds_open"
    detail = "An allocation is more than what is still open on the charge."

    def __init__(self, charge_id: uuid.UUID, open_amount: Money) -> None:
        AppError.__init__(
            self, details={"charge_id": str(charge_id), "open": str(open_amount)}
        )


class InsufficientAccountCreditError(_Conflict):
    """A credit allocation asks for more than the account credit (#96 M4).
    ``details.account_credit`` is the credit under the account lock."""

    code = "insufficient_account_credit"
    detail = "The account doesn't have that much credit."

    def __init__(self, account_credit: Money) -> None:
        AppError.__init__(self, details={"account_credit": str(account_credit)})


class NothingToAllocateError(_Conflict):
    """A credit allocation found no account credit or no open charge (#96
    M4). Nothing is written."""

    code = "nothing_to_allocate"
    detail = "There is no account credit or no open charge to allocate it to."


class AllocationChargeNotFoundError(AppError):
    """Explicit allocation lines name charges that are not on the family's
    account (#96 M3): one field error per line, at
    ``["body", "allocation", "lines", i, "charge_id"]``."""

    status = 422
    code = "allocation_charge_not_found"
    detail = "Choose charges of this family's account."

    def __init__(self, indexes: Sequence[int]) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "allocation", "lines", index, "charge_id"],
                    "code": self.code,
                    "message": self.detail or "",
                }
                for index in indexes
            ]
        )


class PaymentMethodUnavailableError(_FieldRuleError):
    """The payment method is unknown or deactivated (#96 M5, E12)."""

    code = "payment_method_unavailable"
    field = "payment_method_code"
    detail = "Choose an active payment method."


class ReceivedOnInFutureError(_FieldRuleError):
    """The payment is dated after the center's local today (#96 M5)."""

    code = "received_on_in_future"
    field = "received_on"
    detail = "The payment date can't be after today."


# --- Due dates (#97 D1; N5) ----------------------------------------------------------


class ChargeSettledError(_Conflict):
    """The charge is ``paid`` or ``void`` under the account lock: its due
    date can't change (#97 D1, N5). Nothing is written."""

    code = "charge_settled"
    detail = "This charge is paid or void: its due date can't change."


class DueDateUnchangedError(_Conflict):
    """The new due date is the charge's current effective due date (#97
    D1). ``details.due_date`` is that date. Nothing is written."""

    code = "due_date_unchanged"
    detail = "The charge is already due on that date."

    def __init__(self, due_date: date) -> None:
        AppError.__init__(self, details={"due_date": due_date.isoformat()})


# --- Cancellation billing rules (#99 B3) ---------------------------------------------


class CancellationRuleEffectiveFromError(AppError):
    """A new cancellation billing rule version would take effect in the
    past, or not after every existing version (#99 B3; the #61 code)."""

    status = 422
    code = "invalid_effective_from"
    detail = (
        "A new cancellation billing rule must take effect now or later, and"
        " after every upcoming change."
    )

    def __init__(self) -> None:
        super().__init__()


class CancellationRuleInEffectError(AppError):
    """Only an upcoming cancellation billing rule version can be deleted
    (#99 B3; the #61 code)."""

    status = 409
    code = "policy_version_in_effect"
    detail = "This rule is already in effect, so it can't be deleted."

    def __init__(self) -> None:
        super().__init__()


# --- Corrections, reversals and refunds (#100 B, C) ----------------------------------


class StudentNotInFamilyError(_FieldRuleError):
    """A manual charge names a student whose **current** family is not the
    path family (another family's student, a student without a family, an
    unknown id; #100 R7). Nothing is written."""

    code = "student_not_in_family"
    field = "student_id"
    detail = "Choose a student of this family."


class PackagePurchaseNotAdjustableError(_Conflict):
    """A manual adjustment on a ``package_purchase`` charge: its money and
    its units move together, through a package cancellation (#100 R6).
    Nothing is written."""

    code = "package_purchase_not_adjustable"
    detail = "A package purchase can't be adjusted: cancel the package to credit it."


class PaymentAlreadyReversedError(_Conflict):
    """The payment has a reversal already (#100 R5). Nothing is written."""

    code = "payment_already_reversed"
    detail = "This payment was already reversed."


class PaymentHasRefundsError(_Conflict):
    """A payment with a refund can't be reversed (#100 R5, #90 QA O3: the
    ledger sum identity). Nothing is written."""

    code = "payment_has_refunds"
    detail = "This payment has refunds, so it can't be reversed."


class PaymentReversedError(_Conflict):
    """A refund from a reversed payment (#100 R5). Nothing is written."""

    code = "payment_reversed"
    detail = "This payment was reversed, so nothing can be refunded from it."


class RefundExceedsRemainderError(_Conflict):
    """The refund is more than the payment's unallocated remainder (#100 R4;
    I3). ``details.remainder`` is the remainder under the account lock.
    Nothing is written."""

    code = "refund_exceeds_remainder"
    detail = "The refund is more than what is left unallocated on this payment."

    def __init__(self, remainder: Money) -> None:
        AppError.__init__(self, details={"remainder": str(remainder)})


class RefundedOnInFutureError(_FieldRuleError):
    """The refund is dated after the center's local today (#100 C4)."""

    code = "refunded_on_in_future"
    field = "refunded_on"
    detail = "The refund date can't be after today."


class RefundedOnBeforePaymentError(_FieldRuleError):
    """The refund is dated before the payment was received (#100 C4)."""

    code = "refunded_on_before_payment"
    field = "refunded_on"
    detail = "The refund date can't be before the payment date."


class PackageNotCancellableError(_Conflict):
    """The package can't be cancelled (#100 R6): ``details.reason`` is
    ``cancelled`` (already cancelled), ``expired`` (an ``expire`` row or
    past ``expires_on``) or ``exhausted`` (no units left). Nothing is
    written."""

    code = "package_not_cancellable"
    detail = "This package is cancelled, expired or used up, so it can't be cancelled."

    def __init__(self, reason: str) -> None:
        AppError.__init__(self, details={"reason": reason})
