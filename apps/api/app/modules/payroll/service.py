"""The payroll module's public interface (ADR-0002).

**Compensation** (#105; :mod:`~app.modules.payroll.rates`): the reads and
writes the ``app.workflows.payroll_compensation`` workflow calls.

**Settings** (#106; :mod:`~app.modules.payroll.settings`): the readers
:func:`payroll_starts_on`, :func:`biweekly_anchor` and
:func:`salary_proration_basis`. The routes are
:mod:`~app.modules.payroll.settings_router`.

**Periods** (#106; :mod:`~app.modules.payroll.period_rows`,
:mod:`~app.modules.payroll.periods`): :func:`tutors_with_terms` and
:func:`open_due_periods` for the ``payroll:open_periods`` job,
:func:`lock_payroll_periods` for every writer of terms (only
:mod:`~app.modules.payroll.rates` today, #111 next), and the pure
:func:`natural_bounds`, which defines ``natural_days`` for #108.

**Session pay rules** (#441; :mod:`~app.modules.payroll.pay_rules`): the reads
and writes the ``app.workflows.session_pay_rules`` workflow calls.

**For the calculation:** :func:`rate_in_effect`, :func:`terms_in_effect`,
:func:`session_pay_rule_in_effect`, and the run's batch reads
:func:`rate_versions`, :func:`terms_basis_versions` and
:func:`session_pay_rule_versions`.

**Lines** (#107; :mod:`~app.modules.payroll.lines`, the pure rules in
:mod:`~app.modules.payroll.calculation`): :func:`lock_open_period`,
:func:`replace_lines`, :func:`period_lines`, and :func:`draft_lines` with its
frozen values. Per-course pay (#442) adds :func:`finalized_period_dates`,
:func:`finalized_course_lines` and :func:`planned_sessions`. Salary (#108) adds
the pure :func:`salary_drafts` (:mod:`~app.modules.payroll.salary`).

**Bonuses and deductions** (#109; :mod:`~app.modules.payroll.pay_items`, the pure
placement in :mod:`~app.modules.payroll.pay_item_lines`): the reads and writes
the ``app.workflows.payroll_pay_items`` workflow calls, and for a run
:func:`pay_item_facts`, :func:`unfinalized_periods`,
:func:`finalized_pay_item_ids` and :func:`pay_item_drafts`.

**Unresolved work** (#443): the pure :func:`classify_unresolved`, the unexecuted
:func:`has_no_session_line`, and the lock-free batch reads
:func:`first_period_starts`, :func:`rate_versions_of_tutors` and
:func:`terms_basis_versions_of_tutors`.

**Work after lock** (#111; :mod:`~app.modules.payroll.adjustments`): the
unexecuted predicates :func:`has_no_adjustment`, :func:`has_no_adjustment_keyed`,
:func:`paid_after_lock` and :func:`revision_unpaid`, the lock-free reads
:func:`finalized_spans_of_tutors`, :func:`salary_covers_of_tutors`,
:func:`paid_so_far` and :func:`earliest_open_period`, the write
:func:`post_adjustment`, :func:`open_period_for_adjustment` for a tutor with no
open period, and the pure :func:`late_approval_draft`, :func:`correction_draft`
and :func:`owed`; M7-08d adds the per-course :func:`course_anchor_draft`,
:func:`course_correction_draft` and :func:`in_walk_order`. M7-08c adds a person's
writes :func:`post_manual_adjustment` and :func:`post_carry_forward`, their
checks :func:`settled_by_tutor` and :func:`line_period_status`,
:func:`settled_session_ids` for a run, and
:func:`lock_unfinalized_periods_of_tutor`.

**The period's review** (#110; :mod:`~app.modules.payroll.period_review`): the
lock-free reads, the status moves, and the pure :func:`line_drift`, for
``app.workflows.payroll_workflow``.

**Record policies** (:mod:`~app.modules.payroll.permissions`):
:func:`can_view_compensation`, :func:`can_write_compensation`, and for a period
:func:`can_list_periods`, :func:`can_read_period`, :func:`can_return_period` and
:func:`finalize_refusal`.
"""

from app.modules.payroll.adjustments import (
    APPROVAL_KEY,
    CANCELLATION_KEY,
    AdjustmentDraft,
    FinalizedSpan,
    ManualDraft,
    PaidSession,
    PostedAdjustment,
    approval_key,
    cancellation_key,
    earliest_open_period,
    finalized_span_of,
    finalized_spans_of_tutors,
    has_no_adjustment,
    has_no_adjustment_keyed,
    keyed,
    line_period_status,
    paid_after_lock,
    paid_so_far,
    post_adjustment,
    post_carry_forward,
    post_manual_adjustment,
    revision_unpaid,
    salary_covers_of_tutors,
    settled_by_tutor,
    settled_session_ids,
)
from app.modules.payroll.calculation import (
    PER_COURSE,
    ApprovedSession,
    CohortStep,
    CohortWalk,
    CourseWalkError,
    FinalizedCourseLine,
    LineDraft,
    LineDrift,
    OriginSnapshot,
    PayableCancellation,
    PayBasis,
    SalaryCover,
    TermsBasisVersion,
    UnresolvedCandidate,
    UnresolvedItem,
    classify_unresolved,
    correction_draft,
    course_anchor_draft,
    course_correction_draft,
    course_rate,
    draft_lines,
    in_walk_order,
    late_approval_draft,
    line_drift,
    owed,
    planned_sessions,
)
from app.modules.payroll.errors import (
    PayrollLineNotFinalizedError,
    PayrollNegativeTotalError,
    PayrollNoPeriodError,
    PayrollOwnCompensationError,
    PayrollPeriodNotEndedError,
    PayrollPeriodNotOpenError,
    PayrollPeriodNotPreparedError,
    PayrollPeriodOutOfOrderError,
    PayrollPeriodStaleError,
    PayrollReviewOutdatedError,
    PayrollReviewRouteRequiredError,
    PayrollSelfFinalizeError,
    PayrollTotalNotNegativeError,
    PayrollUnresolvedWorkError,
    PayrollWorkBeforeFirstPeriodError,
    PayrollWorkNotUnresolvedError,
    PayrollWorkSettledError,
)
from app.modules.payroll.lines import (
    REBUILT_KINDS,
    finalized_course_lines,
    finalized_period_dates,
    first_period_starts,
    has_no_session_line,
    lock_open_period,
    lock_period,
    lock_unfinalized_periods_of_tutor,
    period_lines,
    replace_lines,
)
from app.modules.payroll.models import HOURS_CORRECTION, LATE_APPROVAL
from app.modules.payroll.pay_item_lines import (
    RECURRING,
    PayItemFact,
    PeriodDates,
    pay_item_drafts,
    placed_period,
)
from app.modules.payroll.pay_items import (
    add_pay_item,
    add_pay_item_type,
    change_pay_item_type,
    delete_pay_item,
    end_pay_item,
    finalized_pay_item_ids,
    get_pay_item,
    list_pay_item_types,
    list_pay_items,
    pay_item_facts,
    unfinalized_periods,
)
from app.modules.payroll.pay_rules import (
    create_session_pay_rule,
    delete_session_pay_rule,
    get_session_pay_rule_overview,
    session_pay_rule_in_effect,
    session_pay_rule_versions,
)
from app.modules.payroll.period_review import (
    EarlierPeriod,
    earliest_unfinalized_before,
    lines_total,
    list_periods,
    mark_finalized,
    mark_prepared,
    mark_returned,
    period_row,
    period_rows,
    rebuilt_only,
    stored_lines,
)
from app.modules.payroll.period_rows import (
    lock_payroll_periods,
    open_due_periods,
    open_period_for_adjustment,
    tutors_with_terms,
)
from app.modules.payroll.periods import natural_bounds
from app.modules.payroll.permissions import (
    REVIEWABLE_STATUSES,
    can_list_periods,
    can_read_period,
    can_return_period,
    can_view_compensation,
    can_write_compensation,
    finalize_refusal,
)
from app.modules.payroll.rates import (
    FirstSessionDates,
    add_compensation_rate,
    add_pay_terms,
    compensation_summaries,
    delete_compensation_rate,
    delete_pay_terms,
    list_compensation_rates,
    list_pay_terms,
    rate_in_effect,
    rate_versions,
    rate_versions_of_tutors,
    terms_basis_versions,
    terms_basis_versions_of_tutors,
    terms_in_effect,
)
from app.modules.payroll.salary import salary_drafts
from app.modules.payroll.settings import (
    biweekly_anchor,
    payroll_starts_on,
    salary_proration_basis,
)

__all__ = [
    "APPROVAL_KEY",
    "CANCELLATION_KEY",
    "HOURS_CORRECTION",
    "LATE_APPROVAL",
    "PER_COURSE",
    "REBUILT_KINDS",
    "RECURRING",
    "REVIEWABLE_STATUSES",
    "AdjustmentDraft",
    "ApprovedSession",
    "CohortStep",
    "CohortWalk",
    "CourseWalkError",
    "EarlierPeriod",
    "FinalizedCourseLine",
    "FinalizedSpan",
    "FirstSessionDates",
    "LineDraft",
    "LineDrift",
    "ManualDraft",
    "OriginSnapshot",
    "PaidSession",
    "PayBasis",
    "PayItemFact",
    "PayableCancellation",
    "PayrollLineNotFinalizedError",
    "PayrollNegativeTotalError",
    "PayrollNoPeriodError",
    "PayrollOwnCompensationError",
    "PayrollPeriodNotEndedError",
    "PayrollPeriodNotOpenError",
    "PayrollPeriodNotPreparedError",
    "PayrollPeriodOutOfOrderError",
    "PayrollPeriodStaleError",
    "PayrollReviewOutdatedError",
    "PayrollReviewRouteRequiredError",
    "PayrollSelfFinalizeError",
    "PayrollTotalNotNegativeError",
    "PayrollUnresolvedWorkError",
    "PayrollWorkBeforeFirstPeriodError",
    "PayrollWorkNotUnresolvedError",
    "PayrollWorkSettledError",
    "PeriodDates",
    "PostedAdjustment",
    "SalaryCover",
    "TermsBasisVersion",
    "UnresolvedCandidate",
    "UnresolvedItem",
    "add_compensation_rate",
    "add_pay_item",
    "add_pay_item_type",
    "add_pay_terms",
    "approval_key",
    "biweekly_anchor",
    "can_list_periods",
    "can_read_period",
    "can_return_period",
    "can_view_compensation",
    "can_write_compensation",
    "cancellation_key",
    "change_pay_item_type",
    "classify_unresolved",
    "compensation_summaries",
    "correction_draft",
    "course_anchor_draft",
    "course_correction_draft",
    "course_rate",
    "create_session_pay_rule",
    "delete_compensation_rate",
    "delete_pay_item",
    "delete_pay_terms",
    "delete_session_pay_rule",
    "draft_lines",
    "earliest_open_period",
    "earliest_unfinalized_before",
    "end_pay_item",
    "finalize_refusal",
    "finalized_course_lines",
    "finalized_pay_item_ids",
    "finalized_period_dates",
    "finalized_span_of",
    "finalized_spans_of_tutors",
    "first_period_starts",
    "get_pay_item",
    "get_session_pay_rule_overview",
    "has_no_adjustment",
    "has_no_adjustment_keyed",
    "has_no_session_line",
    "in_walk_order",
    "keyed",
    "late_approval_draft",
    "line_drift",
    "line_period_status",
    "lines_total",
    "list_compensation_rates",
    "list_pay_item_types",
    "list_pay_items",
    "list_pay_terms",
    "list_periods",
    "lock_open_period",
    "lock_payroll_periods",
    "lock_period",
    "lock_unfinalized_periods_of_tutor",
    "mark_finalized",
    "mark_prepared",
    "mark_returned",
    "natural_bounds",
    "open_due_periods",
    "open_period_for_adjustment",
    "owed",
    "paid_after_lock",
    "paid_so_far",
    "pay_item_drafts",
    "pay_item_facts",
    "payroll_starts_on",
    "period_lines",
    "period_row",
    "period_rows",
    "placed_period",
    "planned_sessions",
    "post_adjustment",
    "post_carry_forward",
    "post_manual_adjustment",
    "rate_in_effect",
    "rate_versions",
    "rate_versions_of_tutors",
    "rebuilt_only",
    "replace_lines",
    "revision_unpaid",
    "salary_covers_of_tutors",
    "salary_drafts",
    "salary_proration_basis",
    "session_pay_rule_in_effect",
    "session_pay_rule_versions",
    "settled_by_tutor",
    "settled_session_ids",
    "stored_lines",
    "terms_basis_versions",
    "terms_basis_versions_of_tutors",
    "terms_in_effect",
    "tutors_with_terms",
    "unfinalized_periods",
]
