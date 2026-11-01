"""Payroll errors (#105 G). Each ``AppError`` renders as Problem Details
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
