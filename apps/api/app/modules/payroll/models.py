"""Payroll tables (#105; ADR-0017 E1; data-model §11.2, §11.3, §14.2).

``tutor_pay_terms`` holds what applies to a tutor as a whole and
``compensation_rates`` the rate per class kind. Each is a list of versions:
one runs until the next one's ``effective_from``, so the unique key is the
"no overlap" constraint (invariant P1). A row whose values are ``NULL`` means
"not on payroll" or "no rate" from that date.

Both tables are guarded: a row a finalized payroll line uses can't change or
go away, and neither table can be truncated (migration ``0072``). The amounts
are masked in the audit log (#105 E2), because Administrators hold
``audit.view`` and see no pay (ADR-0017 E12).
"""

import uuid
from datetime import date
from typing import ClassVar, Final

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model
from app.core.money import Money, MoneyType, Rate, RateType

PAY_FREQUENCIES: Final = ("weekly", "biweekly", "monthly")
CLASS_KINDS: Final = ("individual", "group", "course")
RATE_MODELS: Final = ("per_hour", "per_session", "per_course")
PER_COURSE: Final = "per_course"
COURSE: Final = "course"

PAY_FREQUENCY_MAX: Final = 10
CLASS_KIND_MAX: Final = 20
RATE_MODEL_MAX: Final = 20

TERMS_KEY: Final = "uq_tutor_pay_terms_tutor_user_id_effective_from"
RATE_KEY: Final = "uq_compensation_rates_tutor_user_id_class_kind_effective_from"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class TutorPayTerms(Audited, Model):
    """One version of a tutor's pay frequency and salary (data-model §11.2).

    ``salary_amount`` is per full pay period of this row's frequency.
    ``pay_frequency`` ``NULL`` means "not on payroll from this date".
    """

    __tablename__ = "tutor_pay_terms"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"salary_amount"})
    __table_args__ = (
        CheckConstraint(
            _one_of("pay_frequency", PAY_FREQUENCIES), name="pay_frequency_valid"
        ),
        CheckConstraint("salary_amount > 0", name="salary_positive"),
        CheckConstraint(
            "pay_frequency IS NOT NULL OR salary_amount IS NULL",
            name="salary_needs_frequency",
        ),
        UniqueConstraint("tutor_user_id", "effective_from", name=TERMS_KEY),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    effective_from: Mapped[date] = mapped_column(Date)
    pay_frequency: Mapped[str | None] = mapped_column(
        String(PAY_FREQUENCY_MAX), nullable=True
    )
    salary_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)


class CompensationRate(Audited, Model):
    """One version of a tutor's rate for a class kind (data-model §11.3).

    ``rate_model`` and ``rate`` are both ``NULL`` to mean "no rate from this
    date".
    """

    __tablename__ = "compensation_rates"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"rate"})
    __table_args__ = (
        CheckConstraint(_one_of("class_kind", CLASS_KINDS), name="class_kind_valid"),
        CheckConstraint(_one_of("rate_model", RATE_MODELS), name="rate_model_valid"),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
        CheckConstraint(
            "(rate_model IS NULL) = (rate IS NULL)", name="rate_model_with_rate"
        ),
        CheckConstraint(
            "rate_model <> 'per_course' OR class_kind = 'course'",
            name="per_course_only_course",
        ),
        UniqueConstraint(
            "tutor_user_id", "class_kind", "effective_from", name=RATE_KEY
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    class_kind: Mapped[str] = mapped_column(String(CLASS_KIND_MAX))
    effective_from: Mapped[date] = mapped_column(Date)
    rate_model: Mapped[str | None] = mapped_column(
        String(RATE_MODEL_MAX), nullable=True
    )
    rate: Mapped[Rate | None] = mapped_column(RateType(), nullable=True)
