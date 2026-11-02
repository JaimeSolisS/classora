"""Payroll errors (#105 G, #106 B, F, #441 C, #107 G, H6). Each ``AppError`` renders as
Problem Details (``app.core.errors``) and carries no amount."""

import uuid

from app.core.errors import AppError


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
    """A finalized payroll line uses this version (the guard's 42501)."""

    status = 409
    code = "pay_terms_in_use"
    detail = "A finalized payroll period uses these pay terms."


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


class PayrollPeriodNotOpenError(AppError):
    """Only an ``open`` period is calculated (#107 E7). ``details.status`` is
    the period's status."""

    status = 409
    code = "payroll_period_not_open"
    detail = "Only an open payroll period can be recalculated."

    def __init__(self, status: str) -> None:
        super().__init__(details={"status": status})


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
