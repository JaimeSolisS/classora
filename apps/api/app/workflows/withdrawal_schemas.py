"""Withdrawal shapes (#101 C1, D1-D3; ``app.workflows.withdrawals``).

Staff only (``billing.view`` to read, ``billing.manage`` or
``finance.refund.approve`` to confirm): there is no guardian redaction
here (#102 decides any guardian view).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money
from app.modules.billing.models import PAYMENT_METHOD_CODE_MAX
from app.modules.billing.schemas import ReasonText, ReferenceText
from app.workflows.billing_account_schemas import BillingAccountSummary

type SubjectKind = Literal["group_enrollment", "cohort_enrollment", "package"]
type CreditPolicy = Literal[
    "pro_rata", "nothing_delivered", "open_ended_fee", "fully_used"
]
type WarningCode = Literal[
    "future_covered_sessions", "fee_posted_now", "tuition_posted_now", "open_ended_fee"
]
type BlockingCode = Literal[
    "withdrawal_exists",
    "withdrawal_closeout_pending",
    "package_not_withdrawable",
    "student_has_no_family",
    "withdrawal_spans_accounts",
]

OVERRIDES_MAX = 50
"""At most this many credit overrides per confirm (a withdrawal has a few
lines)."""
RECEIVED_ON_MIN = date(2000, 1, 1)


class WithdrawalSubjectBody(BaseModel):
    """Exactly one subject (``withdrawal_subject_invalid`` otherwise)."""

    model_config = ConfigDict(extra="forbid")

    group_enrollment_id: uuid.UUID | None = None
    cohort_enrollment_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = [
            v
            for v in (
                self.group_enrollment_id,
                self.cohort_enrollment_id,
                self.package_id,
            )
            if v is not None
        ]
        if len(given) != 1:
            raise PydanticCustomError(
                "withdrawal_subject_invalid",
                "Name exactly one group enrollment, cohort enrollment or package.",
            )
        return self

    def subject(self) -> tuple[SubjectKind, uuid.UUID]:
        if self.group_enrollment_id is not None:
            return "group_enrollment", self.group_enrollment_id
        if self.cohort_enrollment_id is not None:
            return "cohort_enrollment", self.cohort_enrollment_id
        assert self.package_id is not None  # noqa: S101 - validated above
        return "package", self.package_id


class CreditOverride(BaseModel):
    """The applied credit of one line instead of the computed one: from
    ``0.00`` (``amount_negative``) to the charge's net (``409
    charge_net_negative`` above it)."""

    model_config = ConfigDict(extra="forbid")

    charge_id: uuid.UUID
    amount: Money

    @field_validator("amount")
    @classmethod
    def _not_negative(cls, value: Money) -> Money:
        if value.is_negative():
            raise PydanticCustomError(
                "amount_negative", "The amount can't be less than 0."
            )
        return value


class RefundTerms(BaseModel):
    """How the refund went out: its date (from 2000-01-01, not after today,
    not before the latest source payment), an active method and an optional
    reference."""

    model_config = ConfigDict(extra="forbid")

    refunded_on: date
    payment_method_code: Annotated[
        str, Field(min_length=1, max_length=PAYMENT_METHOD_CODE_MAX)
    ]
    reference: ReferenceText = None

    @field_validator("refunded_on")
    @classmethod
    def _from_2000(cls, value: date) -> date:
        if value < RECEIVED_ON_MIN:
            raise PydanticCustomError(
                "date_out_of_range", "Choose a date from 2000-01-01 on."
            )
        return value


class WithdrawalCreditConfirm(BaseModel):
    """``POST /api/v1/billing/withdrawals/account-credit`` (#101 D2): the
    subject, the enrollment's ``version`` (required for an active
    enrollment, ignored otherwise), the reason (trimmed, 1-500), the
    preview's ``fingerprint`` and optional per-line overrides (unique
    charges: ``credit_override_duplicate``)."""

    model_config = ConfigDict(extra="forbid")

    subject: WithdrawalSubjectBody
    version: int | None = None
    reason: ReasonText
    fingerprint: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    credit_overrides: Annotated[
        list[CreditOverride], Field(max_length=OVERRIDES_MAX)
    ] = []

    @field_validator("credit_overrides")
    @classmethod
    def _unique(cls, value: list[CreditOverride]) -> list[CreditOverride]:
        ids = [o.charge_id for o in value]
        if len(ids) != len(set(ids)):
            raise PydanticCustomError(
                "credit_override_duplicate", "Override each charge at most once."
            )
        return value


class WithdrawalRefundConfirm(WithdrawalCreditConfirm):
    """``POST /api/v1/billing/withdrawals/refund`` (#101 D2): the same plus
    the refund's date, method and reference."""

    refund: RefundTerms


# --- The preview (C1) ----------------------------------------------------------------


class WithdrawalSubject(BaseModel):
    """The enrollment or package being withdrawn."""

    kind: SubjectKind
    id: uuid.UUID


class PreviewRecord(BaseModel):
    """A record's id and name."""

    id: uuid.UUID
    name: str


class PreviewSessions(BaseModel):
    """The owner's counted sessions of the period (S1): ``consumed`` before
    the withdrawal instant, ``unused`` after it, and their minutes."""

    consumed: int
    unused: int
    consumed_minutes: int
    unused_minutes: int


class PreviewUnits(BaseModel):
    """A package's units: granted, consumed (or expired) and remaining."""

    unit: Literal["sessions", "minutes"]
    granted: int
    consumed: int
    remaining: int


class LineBasis(BaseModel):
    """The counts behind a line's credit: ``credit = net × unused /
    denominator`` (S1)."""

    consumed: int
    unused: int
    denominator: int


class WithdrawalLine(BaseModel):
    """One charge the withdrawal credits (S1). ``posting: "now"``: the
    charge is due but not posted yet; the confirm posts it first (S6), so
    ``charge_id`` is ``null`` until then. ``allocated`` is the valid
    allocations; ``computed_credit`` the rule's credit before overrides."""

    charge_id: uuid.UUID | None
    kind: Literal[
        "group_enrollment", "cohort_enrollment", "tuition_period", "package_purchase"
    ]
    service_date: date
    service_period_end: date | None
    net: Money
    allocated: Money
    basis: LineBasis
    policy: CreditPolicy
    computed_credit: Money
    posting: Literal["posted", "now"]


class AutomaticCredit(BaseModel):
    """A #92 removal credit the group withdrawal posts by itself (the whole
    net of a session charge the student leaves); never part of the
    withdrawal's amount."""

    charge_id: uuid.UUID
    class_session_id: uuid.UUID
    amount: Money


class PreviewTotals(BaseModel):
    """``credit`` = Σ computed credits; ``reduces_debt`` = the part of it
    that lowers what is owed; ``refundable_cash`` = the paid money the
    credits (and the automatic credits) free; ``automatic_credits`` = Σ the
    automatic credits; ``account_credit_after_credit`` = the account credit
    once everything is posted (before any refund)."""

    credit: Money
    reduces_debt: Money
    refundable_cash: Money
    automatic_credits: Money
    account_credit_after_credit: Money


class PreviewWarning(BaseModel):
    """Something to know before confirming (``class_session_ids`` for
    ``future_covered_sessions``)."""

    code: WarningCode
    count: int
    class_session_ids: list[uuid.UUID] = []


class PreviewBlocking(BaseModel):
    """Why the withdrawal can't be confirmed (the confirm answers this code
    as its error)."""

    code: BlockingCode
    details: dict[str, str | list[str]] = {}


class WithdrawalPreview(BaseModel):
    """``GET /api/v1/billing/withdrawal-preview`` (#101 C1). ``at`` is the
    withdrawal instant (now for an active enrollment or a package, the
    enrollment's ``withdrawn_at`` to settle a withdrawn one). Send
    ``fingerprint`` with the confirm: a different one under the locks is
    ``409 withdrawal_preview_stale`` with the fresh preview."""

    subject: WithdrawalSubject
    student: PreviewRecord
    family: PreviewRecord | None
    billing_account_id: uuid.UUID | None
    enrollment_status: Literal["active", "withdrawn"] | None
    enrollment_version: int | None
    enrolled_at: datetime | None
    at: datetime
    sessions: PreviewSessions | None
    units: PreviewUnits | None
    lines: list[WithdrawalLine]
    automatic_credits: list[AutomaticCredit]
    totals: PreviewTotals
    warnings: list[PreviewWarning]
    blocking: list[PreviewBlocking]
    fingerprint: str


# --- Results and reads (C2, D3) ------------------------------------------------------


class WithdrawalItem(BaseModel):
    """One withdrawal (#101 D3): its subject, outcome, applied credit
    (``amount``), cash refunded, the instant used, the reason and who
    confirmed it, with the ids of its adjustments, its refunds and the #92
    automatic credits it caused."""

    id: uuid.UUID
    subject: WithdrawalSubject
    student_id: uuid.UUID
    family_id: uuid.UUID
    billing_account_id: uuid.UUID
    outcome: Literal["refund", "account_credit"]
    amount: Money
    refund_amount: Money
    withdrawn_at: datetime
    reason: str
    confirmed_by_user_id: uuid.UUID
    created_at: datetime
    adjustment_ids: list[uuid.UUID]
    refund_ids: list[uuid.UUID]
    automatic_credit_adjustment_ids: list[uuid.UUID]


class WithdrawalDetail(WithdrawalItem):
    """A withdrawal with the preview it was confirmed on (the applied
    amounts, the overrides and the outcome included)."""

    preview: dict[str, Any]


class WithdrawalConfirmed(BaseModel):
    """``201`` of a confirm (#101 C2): the withdrawal, the package
    ``refund`` units row (packages), the account credit afterwards and the
    family's account summary."""

    withdrawal: WithdrawalDetail
    package_refund_transaction_id: uuid.UUID | None
    account_credit_after: Money
    account: BillingAccountSummary
