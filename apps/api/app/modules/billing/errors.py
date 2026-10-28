"""Billing errors (#89 B9, #90 C6). Each ``AppError`` renders as Problem
Details (``app.core.errors``) and carries no record data beyond the ids
(and, for ``charge_net_negative``, the amounts) in ``details``."""

import uuid
from collections.abc import Sequence

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
