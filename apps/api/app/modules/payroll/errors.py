"""Payroll errors (#105 G, #106 B, F, #441 C, #107 G, H6, #109, #110). Each
``AppError`` renders as Problem Details (``app.core.errors``) and carries no
amount."""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Final, Literal, Protocol

from app.core.errors import AppError

UNRESOLVED_ITEMS_SHOWN: Final = 20
"""The most ``payroll_unresolved_work`` lists (#110 B4); ``count`` says how many
there are."""


class _Unresolved(Protocol):
    @property
    def class_session_id(self) -> uuid.UUID: ...
    @property
    def service_date(self) -> date: ...
    @property
    def reason(self) -> str: ...


class PayTermsExistsError(AppError):
    """The tutor already has a terms version on this date (invariant P1)."""

    status = 409
    code = "pay_terms_exists"
    detail = "This tutor already has pay terms from this date."


class CompensationRateExistsError(AppError):
    """The tutor already has a version of this class kind on this date
    (invariant P1)."""

    status = 409
    code = "compensation_rate_exists"
    detail = "This tutor already has a rate for this class kind from this date."


class PayTermsInUseError(AppError):
    """A line of a prepared period (the foreign key's 23001) or a finalized one
    (the guard's 42501) uses this version."""

    status = 409
    code = "pay_terms_in_use"
    detail = "A payroll period uses these pay terms."


class CompensationRateInUseError(AppError):
    """A line of a prepared or finalized period uses this version (the
    foreign key's refusal, or the guard's 42501; #107 E8)."""

    status = 409
    code = "compensation_rate_in_use"
    detail = "A payroll period uses this rate."


class PayrollOwnCompensationError(AppError):
    """Only an Owner may write their own terms or rates (#105 E5)."""

    status = 403
    code = "payroll_own_compensation"
    detail = "Someone else has to change your own compensation."


class EffectiveFromInsideGeneratedPeriodError(AppError):
    """After this write, a generated period would hold days of another
    frequency (#106 E3)."""

    status = 422
    code = "effective_from_inside_generated_period"
    detail = "This change would put another pay frequency inside a generated period."


class EffectiveFromBeforeFirstPeriodError(AppError):
    """After this write, days no period can cover would have pay terms
    (#106 E4)."""

    status = 422
    code = "effective_from_before_first_period"
    detail = "Pay terms can't start before the tutor's first payroll period."


class PayRuleEffectiveFromError(AppError):
    """A new pay rule version would start in the past, or not after every
    version of its event (#107 E9; the #61 code)."""

    status = 422
    code = "invalid_effective_from"
    detail = (
        "A new version starts today or later, and after every version of this event."
    )


class PayRuleInEffectError(AppError):
    """Only an upcoming pay rule version can be deleted (#441 C3)."""

    status = 409
    code = "pay_rule_in_effect"
    detail = "This pay rule is in force or in the past, so it can't be deleted."


class SessionPayRuleInUseError(AppError):
    """A line keeps this version (#107 G3): in practice a run committed one
    while the delete waited on the version. E9 keeps a rule out of prepared and
    finalized periods, so only a clock edge reaches one of those."""

    status = 409
    code = "session_pay_rule_in_use"
    detail = "A payroll period uses this pay rule."


class PayItemTypeExistsError(AppError):
    """Another type has this name, ignoring case (#109 D2)."""

    status = 409
    code = "pay_item_type_exists"
    detail = "A pay item type with this name already exists."


class PayItemTypeInUseError(AppError):
    """A recurring item of this type is still running, so deactivating it would
    leave it paying under a retired type (#109 E8)."""

    status = 409
    code = "pay_item_type_in_use"
    detail = "A recurring pay item of this type is still running."


class PayItemTypeUnknownError(AppError):
    """No such type (#109 F3)."""

    status = 422
    code = "pay_item_type_unknown"
    detail = "This pay item type doesn't exist."


class PayItemTypeInactiveError(AppError):
    """The type was deactivated (#109 F3)."""

    status = 409
    code = "pay_item_type_inactive"
    detail = "This pay item type is inactive."


class PayItemNotRecurringError(AppError):
    """Only a recurring item has an end (#109 F5)."""

    status = 409
    code = "pay_item_not_recurring"
    detail = "Only a recurring pay item can be ended."


class PayItemEndBeforeStartError(AppError):
    """The new end is before the item's start (#109 F5, the ``422`` a create
    gets from its body)."""

    status = 422
    code = "pay_item_end_before_start"
    detail = "The end date can't be before the start date."


class PayItemEndBeforePaidPeriodError(AppError):
    """The new end would disown a paid line: it is before the start of the
    latest finalized period holding one (#109 F5)."""

    status = 422
    code = "pay_item_end_before_paid_period"
    detail = "The end date can't be before a finalized period that paid this item."


class PayItemInUseError(AppError):
    """A line of a prepared period (the foreign key's 23001) or a finalized one
    (the guard's 42501) uses this item (#109 F6)."""

    status = 409
    code = "pay_item_in_use"
    detail = "A prepared or finalized payroll period uses this pay item."


class EffectiveFromUnderFinalizedLineError(AppError):
    """The new version would re-price a finalized line of this tutor and class
    kind (#110 E5). ``details.latest_finalized_on`` is the newest date it would
    take over, the cohort's first date for a per-course line, so a version after
    it passes: correct with one, and a ``manual`` adjustment (#111) for what was
    paid."""

    status = 422
    code = "effective_from_under_finalized_line"
    detail = "This rate would change a finalized payroll line."

    def __init__(self, latest_finalized_on: date) -> None:
        super().__init__(
            details={"latest_finalized_on": latest_finalized_on.isoformat()}
        )


class PayrollPeriodNotOpenError(AppError):
    """Only an ``open`` period is calculated or prepared (#107 E7, #110 B8).
    ``details.status`` is the period's status."""

    status = 409
    code = "payroll_period_not_open"
    detail = "This payroll period isn't open."

    def __init__(self, status: str) -> None:
        super().__init__(details={"status": status})


class PayrollPeriodNotPreparedError(AppError):
    """Only a ``prepared`` period is returned or finalized (#110 C2, D2).
    ``details.status`` is the period's status."""

    status = 409
    code = "payroll_period_not_prepared"
    detail = "This payroll period isn't prepared."

    def __init__(self, status: str) -> None:
        super().__init__(details={"status": status})


class PayrollPeriodNotEndedError(AppError):
    """A period is prepared only after its last day, in the center's zone
    (#110 B3)."""

    status = 409
    code = "payroll_period_not_ended"
    detail = "This payroll period hasn't ended yet."

    def __init__(self, ends_on: date) -> None:
        super().__init__(details={"ends_on": ends_on.isoformat()})


class PayrollUnresolvedWorkError(AppError):
    """Work that can't be paid stops the period (#107 E12, #110 B4, D2):
    ``details.count``, and ``details.items`` newest first, at most
    :data:`UNRESOLVED_ITEMS_SHOWN`."""

    status = 409
    code = "payroll_unresolved_work"
    detail = "Some approved work of this tutor can't be paid yet."

    def __init__(self, items: Sequence[_Unresolved]) -> None:
        super().__init__(
            details={
                "count": len(items),
                "items": [
                    {
                        "class_session_id": str(item.class_session_id),
                        "service_date": item.service_date.isoformat(),
                        "reason": item.reason,
                    }
                    for item in items[:UNRESOLVED_ITEMS_SHOWN]
                ],
            }
        )


class PayrollNegativeTotalError(AppError):
    """The period's lines add up to less than ``0.00`` (#109 E2, #110 B5). The
    amount is never in the body."""

    status = 409
    code = "payroll_negative_total"
    detail = "This payroll period's total is below zero."


class PayrollSelfFinalizeError(AppError):
    """The preparer can't finalize, and nobody finalizes their own pay, but an
    Owner (ADR-0017 E10). ``details.reason`` is ``preparer`` or ``own_pay``."""

    status = 403
    code = "payroll_self_finalize"
    detail = "Someone else has to finalize this payroll period."

    def __init__(self, reason: Literal["preparer", "own_pay"]) -> None:
        super().__init__(details={"reason": reason})


class PayrollReviewOutdatedError(AppError):
    """The period was prepared again after the page was read (#110 E4)."""

    status = 409
    code = "payroll_review_outdated"
    detail = "This payroll period changed after you opened it."


class PayrollPeriodOutOfOrderError(AppError):
    """An earlier period of the tutor isn't finalized (#110 E2).
    ``details.earliest_period_id`` and ``details.status`` name it."""

    status = 409
    code = "payroll_period_out_of_order"
    detail = "An earlier payroll period of this tutor has to be finalized first."

    def __init__(self, earliest_period_id: uuid.UUID, status: str) -> None:
        super().__init__(
            details={"earliest_period_id": str(earliest_period_id), "status": status}
        )


class PayrollPeriodStaleError(AppError):
    """The facts no longer give the prepared lines (ADR-0017 E10). Counts only:
    return the period and prepare it again."""

    status = 409
    code = "payroll_period_stale"
    detail = "The lines of this payroll period no longer match its facts."

    def __init__(self, *, added: int, removed: int, changed: int) -> None:
        super().__init__(
            details={"added": added, "removed": removed, "changed": changed}
        )


class PayrollReviewRouteRequiredError(AppError):
    """``409 payroll_review_route_required`` (#110 G2): the inbox's generic routes
    can't decide a period; ``details.review_path`` is its review page."""

    status = 409
    code = "payroll_review_route_required"
    detail = "Review this payroll period on its own page."

    def __init__(self, review_path: str) -> None:
        super().__init__(details={"review_path": review_path})


class PayrollAmountOutOfRangeError(AppError):
    """A line, or the period's total, is beyond the money range (#107 D11).
    ``details.class_session_id`` names the line's session; the amount is never
    in the body."""

    status = 409
    code = "payroll_amount_out_of_range"
    detail = "An amount of this payroll period is too large to record."

    def __init__(self, class_session_id: uuid.UUID | None = None) -> None:
        super().__init__(
            details=None
            if class_session_id is None
            else {"class_session_id": str(class_session_id)}
        )


class PayrollStartLockedError(AppError):
    """A payroll period exists, so the floor can't move (#106 E6)."""

    status = 409
    code = "payroll_start_locked"
    detail = "Payroll periods exist, so the payroll start date can't change."


class BiweeklyAnchorLockedError(AppError):
    """A biweekly period exists, so the anchor can't move (#106 E6)."""

    status = 409
    code = "biweekly_anchor_locked"
    detail = "Biweekly payroll periods exist, so the anchor date can't change."
