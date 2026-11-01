"""Payroll errors (#105 G, #106 B, F). Each ``AppError`` renders as Problem Details
(``app.core.errors``) and carries no amount."""

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
    """A finalized payroll line uses this version (the guard's 42501)."""

    status = 409
    code = "compensation_rate_in_use"
    detail = "A finalized payroll period uses this rate."


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
