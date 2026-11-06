"""#129 C1, C5, C6: the retention registry, the protected-tables pin and
the summary line."""

import logging
from datetime import UTC, datetime

import pytest

from app.core import jobs
from app.modules.academics.deleted_records import PURGED_TABLES
from app.modules.administration import retention
from app.wiring import (
    DELETED_RECORDS_RULE,
    FINISHED_JOBS_RULE,
    register_approval_wiring,
    register_retention_wiring,
)


async def _none(sessionmaker, now):  # noqa: ANN001, ANN202
    return 0


@pytest.fixture
def scratch_rule():
    rule = retention.RetentionRule(code="test.scratch", tables=frozenset(), run=_none)
    yield rule
    retention.unregister_retention_rule("test.scratch")


def test_registering_the_same_rule_again_changes_nothing(scratch_rule):
    retention.register_retention_rule(scratch_rule)
    retention.register_retention_rule(scratch_rule)

    assert retention.registered_retention_rules().count(scratch_rule) == 1


def test_another_rule_under_a_registered_code_raises(scratch_rule):
    retention.register_retention_rule(scratch_rule)
    other = retention.RetentionRule(
        code="test.scratch", tables=frozenset({"x"}), run=_none
    )

    with pytest.raises(ValueError, match=r"test\.scratch"):
        retention.register_retention_rule(other)


def test_the_wiring_registers_the_two_rules():
    register_approval_wiring()
    register_retention_wiring()

    codes = {rule.code for rule in retention.registered_retention_rules()}
    assert {"academics.deleted_records", "jobs.finished_jobs"} <= codes
    assert (
        DELETED_RECORDS_RULE.tables
        == PURGED_TABLES
        == frozenset(
            {
                "session_notes",
                "evaluations",
                "evaluation_scores",
                "learning_plans",
                "learning_plan_objectives",
                "learning_plan_milestones",
                "assignments",
                "assignment_submissions",
                "progress_reports",
            }
        )
    )
    assert FINISHED_JOBS_RULE.tables == frozenset(
        {"procrastinate_jobs", "procrastinate_events", "job_failures"}
    )
    assert FINISHED_JOBS_RULE.run is jobs.purge_finished_jobs


def test_no_rule_deletes_from_a_protected_table():
    register_approval_wiring()
    protected = retention.protected_tables()
    for rule in retention.registered_retention_rules():
        allowed = retention.PROTECTED_EXCEPTIONS.get(rule.code, frozenset())
        assert not (rule.tables & protected) - allowed, rule.code


def test_the_protected_tables_hold_money_payroll_expenses_audit_and_consent():
    protected = retention.protected_tables()

    for table in (
        "charges",
        "payments",
        "receipts",
        "refunds",
        "packages",
        "payroll_lines",
        "payroll_periods",
        "payslips",
        "expenses",
        "expense_payments",
        "audit_events",
        "consent_records",
        "privacy_notice_acceptances",
        "import_jobs",
        "export_jobs",
        "job_failures",
    ):
        assert table in protected, table
    assert retention.PROTECTED_EXCEPTIONS == {
        "jobs.finished_jobs": frozenset({"job_failures"})
    }


def test_the_summary_carries_counts_duration_and_failed_codes_only():
    result = retention.RetentionResult(
        counts={"jobs.finished_jobs": 3, "academics.deleted_records": 2},
        failed=("x.broken",),
    )

    assert retention.summary_extra(result, 12) == {
        "counts": {"academics.deleted_records": 2, "jobs.finished_jobs": 3},
        "deleted_count": 5,
        "duration_ms": 12,
        "failed_rules": ["x.broken"],
    }


@pytest.mark.anyio
async def test_a_failing_rule_is_logged_and_the_others_still_run(caplog, monkeypatch):
    ran: list[str] = []

    async def broken(sessionmaker, now):  # noqa: ANN001, ANN202
        raise RuntimeError("secret value")

    async def fine(sessionmaker, now):  # noqa: ANN001, ANN202
        ran.append("fine")
        return 0

    monkeypatch.setattr(
        retention,
        "_rules",
        {
            "a.broken": retention.RetentionRule("a.broken", frozenset(), broken),
            "b.fine": retention.RetentionRule("b.fine", frozenset(), fine),
        },
    )
    caplog.set_level(logging.INFO, logger=retention.__name__)

    result = await retention.run_retention_with(
        None,  # type: ignore[arg-type]  (nothing deleted: no audit event)
        now=datetime(2031, 1, 1, tzinfo=UTC),
    )

    assert ran == ["fine"]
    assert result.counts == {"b.fine": 0}
    assert result.failed == ("a.broken",)
    logged = [r for r in caplog.records if r.name == retention.__name__]
    assert all("secret value" not in repr(vars(r)) for r in logged)
    [summary] = [r for r in caplog.records if r.message == retention.RUN_COMPLETED]
    assert summary.failed_rules == ["a.broken"]
    assert summary.counts == {"b.fine": 0}
