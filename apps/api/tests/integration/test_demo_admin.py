"""The M8 demo data (#539 A, B, C): ``administration.demo_receipt_message``,
``administration.demo_admin``, ``administration.demo_imports``,
``reporting.demo_setups`` and ``academics.demo_deleted``: the registry, the
whole run with them, worker parity and reruns, ``dev`` and the flag, the stale
and configured skips, a prerequisite and rollback. What each account sees is
in ``test_demo_admin_views``."""

import asyncio
import dataclasses
from datetime import timedelta
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.modules.administration.imports.errors import ImportFileInvalidError
from app.modules.administration.imports.tasks import ImportRunResult, run_imports_with
from app.modules.administration.retention import run_retention_with
from app.modules.reporting.tasks import ExportRunResult, run_exports_with
from tests.factories import demo
from tests.factories.demo_admin import (
    ADMIN_SETUP_NAME,
    BALANCES_FILE,
    DELETED_PLAN_TITLE,
    FINANCE_SETUP_NAME,
    RECEIPT_MESSAGE_CONFIGURED,
    RECEIPT_MESSAGE_EN,
    RECEIPT_MESSAGE_ES,
    SCHEDULES_FILE,
    STUDENTS_FILE,
    TUTORS_FILE,
    DemoAdminGenerator,
    DemoAdminPlan,
    DemoDeletedGenerator,
    DemoImportsGenerator,
    DemoSetupsGenerator,
    plan_demo_admin,
)
from tests.factories.demo_billing import WITHOUT_BILLING
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    ADMIN_GENERATORS,
    ALL_TABLE_COUNTS,
    DEMO_ADMIN_ROWS,
    DEMO_ADMIN_TABLE_COUNTS,
    DEMO_DELETED_ROWS,
    DEMO_IMPORTS_ROWS,
    DEMO_RECEIPT_MESSAGE_ROWS,
    DEMO_SETUPS_ROWS,
    DEV_LINES,
    FINANCE_EXPENSES_GENERATOR,
    FIRST_RUN_LINES,
    FLAGGED_GENERATORS,
    FROZEN_NOW,
    GENERATOR_NAMES,
    IMPORTED_STUDENT_ROWS,
    PRICING_GENERATOR,
    PROGRESS_REPORTS_GENERATOR,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    SEEDED_NOTHING_TABLES,
    TABLE_COUNTS_THROUGH_354,
    TABLE_COUNTS_THROUGH_501,
    count,
    demo_env,
    demo_table_counts,
    generators_through,
    migrate,
)

M8_TABLES = (
    "notification_opt_outs",
    "import_jobs",
    "import_rows",
    "report_setups",
)
SOFT_DELETE_TABLES = (
    "session_notes",
    "evaluations",
    "learning_plans",
    "assignments",
    "progress_reports",
)
"""The ``SoftDeleteMixin`` tables (#129's eligibility pin)."""


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _fresh_admin(plan: Any = plan_demo_admin) -> list[Any]:
    """The registered generators with new M8-demo instances after
    ``finance.demo_expenses`` (their ids are read after the run)."""
    return [
        *generators_through(FINANCE_EXPENSES_GENERATOR),
        DemoAdminGenerator(),
        DemoImportsGenerator(plan=plan),
        DemoSetupsGenerator(),
        DemoDeletedGenerator(),
    ]


def _one(url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    [row] = run_sql(url, sql, params)
    return row


def _user_id(url: str, username: str) -> Any:
    return _one(url, "SELECT id FROM users WHERE username = :u", {"u": username}).id


def _every_table(url: str) -> dict[str, int]:
    """Every public table's row count (C1)."""
    tables = run_sql(
        url,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        " AND tablename <> 'alembic_version'",
    )
    return {t.tablename: count(url, t.tablename) for t in tables}


def _versions(url: str) -> list[Any]:
    rows = run_sql(url, "SELECT version FROM business_settings")
    rows += run_sql(url, "SELECT id, version FROM approval_requests ORDER BY id")
    return [tuple(r) for r in rows]


def _no_m8_data(url: str) -> None:
    for table in (*M8_TABLES, *SEEDED_NOTHING_TABLES):
        assert count(url, table) == 0, table
    settings = _one(
        url, "SELECT receipt_message_es, receipt_message_en FROM business_settings"
    )
    assert tuple(settings) == (None, None)
    assert (
        run_sql(
            url,
            "SELECT id FROM learning_plans WHERE title = :t",
            {"t": DELETED_PLAN_TITLE},
        )
        == []
    )
    assert (
        run_sql(
            url, "SELECT id FROM approval_requests WHERE assignee_user_id IS NOT NULL"
        )
        == []
    )


def _actors(url: str, record_type: str, action: str = "create") -> set[Any]:
    rows = run_sql(
        url,
        "SELECT DISTINCT u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type = :t AND e.action = :a",
        {"t": record_type, "a": action},
    )
    return {r.username for r in rows}


def test_a_first_run_writes_the_m8_data(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = _fresh_admin()

    assert demo.main([], generators=generators) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    _, imports_step, _, deleted_step = generators[-4:]
    owner = _user_id(migrated_url, "demo.owner")
    admin = _user_id(migrated_url, "demo.admin")
    coordinator = _user_id(migrated_url, "demo.coordinator")
    finance = _user_id(migrated_url, "demo.finance")
    tutor = _user_id(migrated_url, "demo.tutor")

    # B1 (#121): the messages, on every demo receipt (template version 2).
    settings = _one(
        migrated_url,
        "SELECT receipt_message_es, receipt_message_en FROM business_settings",
    )
    assert tuple(settings) == (RECEIPT_MESSAGE_ES, RECEIPT_MESSAGE_EN)
    receipts = run_sql(
        migrated_url,
        "SELECT template_version, snapshot->>'v' AS v, snapshot->>'message' AS m,"
        " language FROM receipts",
    )
    assert len(receipts) == 3
    assert {(r.template_version, r.v, r.m, r.language) for r in receipts} == {
        (2, "2", RECEIPT_MESSAGE_ES, "es")
    }
    assert _actors(migrated_url, "business_settings", "update") == {"demo.owner"}
    assert {
        r.is_active
        for r in run_sql(migrated_url, "SELECT is_active FROM payment_methods")
    } == {True}

    # B2 (#119, #118): one opt-out; the two assignments; one notice.
    opt_outs = run_sql(
        migrated_url, "SELECT user_id, notification_type FROM notification_opt_outs"
    )
    assert [tuple(o) for o in opt_outs] == [
        (coordinator, "administration.approval_assigned")
    ]
    items = run_sql(
        migrated_url,
        "SELECT id, request_type, assignee_user_id, priority FROM approval_requests"
        " WHERE status IN ('pending', 'changes_requested') ORDER BY request_type",
    )
    assigned = {i.request_type: i.assignee_user_id for i in items if i.assignee_user_id}
    assert assigned == {
        "staff.qualification": admin,
        "academics.progress_report": coordinator,
    }
    assert sorted((i.request_type, i.priority) for i in items) == [
        ("academics.progress_report", "normal"),
        ("scheduling.change_request", "normal"),
        ("scheduling.offer_reassignment", "normal"),
        ("scheduling.session_request", "normal"),
        ("staff.availability_change", "normal"),
        ("staff.qualification", "normal"),
    ]
    [qualification] = [i for i in items if i.request_type == "staff.qualification"]
    notices = run_sql(
        migrated_url,
        "SELECT recipient_user_id, params->>'request_id' AS request_id, read_at"
        " FROM notifications WHERE notification_type = :t",
        {"t": "administration.approval_assigned"},
    )
    assert [(n.recipient_user_id, n.request_id, n.read_at) for n in notices] == [
        (admin, str(qualification.id), None)
    ]
    assert _actors(migrated_url, "approval_requests", "update") >= {"demo.owner"}

    # B3 (#122, #123): the four jobs.
    jobs = {
        j.file_name: j
        for j in run_sql(
            migrated_url,
            "SELECT id, kind, status, file_name, total_rows, ready_rows,"
            " duplicate_rows, error_rows, created_rows, skipped_rows, failed_rows,"
            " created_by_user_id, committed_by_user_id FROM import_jobs",
        )
    }
    assert imports_step.jobs is not None
    assert {j.id for j in jobs.values()} == {
        imports_step.jobs.students,
        imports_step.jobs.tutors,
        imports_step.jobs.schedules,
        imports_step.jobs.balances,
    }

    def counters(name: str) -> tuple[Any, ...]:
        j = jobs[name]
        return (
            j.kind, j.status, j.total_rows, j.ready_rows, j.duplicate_rows,
            j.error_rows, j.created_rows, j.skipped_rows, j.failed_rows,
        )  # fmt: skip

    assert counters(STUDENTS_FILE) == ("students", "committed", 3, 0, 1, 0, 2, 0, 0)
    assert counters(TUTORS_FILE) == ("tutors", "staged", 2, 1, 0, 1, 0, 0, 0)
    assert counters(SCHEDULES_FILE) == ("schedules", "committed", 2, 0, 0, 0, 1, 0, 1)
    assert counters(BALANCES_FILE) == (
        "opening_balances", "committed", 2, 0, 0, 0, 2, 0, 0,
    )  # fmt: skip
    assert {
        name: (j.created_by_user_id, j.committed_by_user_id) for name, j in jobs.items()
    } == {
        STUDENTS_FILE: (admin, admin),
        TUTORS_FILE: (admin, None),
        SCHEDULES_FILE: (admin, admin),
        BALANCES_FILE: (finance, finance),
    }
    rows = run_sql(
        migrated_url,
        "SELECT j.file_name, r.row_number, r.status, r.values IS NULL AS cleared,"
        " r.errors FROM import_rows r JOIN import_jobs j ON j.id = r.job_id",
    )
    assert {r.cleared for r in rows if r.file_name != TUTORS_FILE} == {True}
    assert {r.cleared for r in rows if r.file_name == TUTORS_FILE} == {False}
    [failed] = [r for r in rows if r.status == "failed"]
    assert failed.file_name == SCHEDULES_FILE
    assert {"scheduling_conflict"} <= {e["code"] for e in failed.errors}
    [invalid] = [r for r in rows if r.status == "error"]
    assert {e["code"] for e in invalid.errors} == {"invalid_email"}
    # No invitation for the staged tutors.
    assert (
        run_sql(
            migrated_url, "SELECT id FROM users WHERE username LIKE 'demo-import-%'"
        )
        == []
    )
    created = run_sql(
        migrated_url,
        "SELECT r.record_id FROM import_rows r JOIN import_jobs j ON j.id = r.job_id"
        " WHERE j.file_name = :f AND r.status = 'created'",
        {"f": STUDENTS_FILE},
    )
    student_actors = run_sql(
        migrated_url,
        "SELECT DISTINCT e.actor_user_id FROM audit_events e"
        " WHERE e.record_type = 'students' AND e.action = 'create'"
        " AND e.record_id = ANY(:ids)",
        {"ids": [c.record_id for c in created]},
    )
    assert len(created) == 2
    assert [a.actor_user_id for a in student_actors] == [admin]
    charges = run_sql(
        migrated_url,
        "SELECT c.import_job_id, c.import_row_number FROM charges c"
        " WHERE c.import_job_id IS NOT NULL ORDER BY c.import_row_number",
    )
    balance_rows = sorted(r.row_number for r in rows if r.file_name == BALANCES_FILE)
    assert [(c.import_job_id, c.import_row_number) for c in charges] == [
        (jobs[BALANCES_FILE].id, n) for n in balance_rows
    ]
    # demo.tutor's pending offer for the imported series (E5).
    [series] = [
        r.record_id
        for r in run_sql(
            migrated_url,
            "SELECT r.record_id FROM import_rows r JOIN import_jobs j"
            " ON j.id = r.job_id WHERE j.file_name = :f AND r.status = 'created'",
            {"f": SCHEDULES_FILE},
        )
    ]
    [offer] = run_sql(
        migrated_url,
        "SELECT o.id, o.status, count(os.*) AS sessions FROM assignment_offers o"
        " JOIN assignment_offer_sessions os ON os.offer_id = o.id"
        " WHERE o.tutor_user_id = :t AND o.class_series_id = :s"
        " GROUP BY o.id, o.status",
        {"t": tutor, "s": series},
    )
    assert (offer.status, offer.sessions) == ("pending", 4)
    assert count(migrated_url, "procrastinate_jobs") == 0

    # B4 (#125): the two setups, through create_setup.
    setups = run_sql(
        migrated_url,
        "SELECT s.name, s.dataset, s.shared_role, u.username,"
        " s.parameters->>'format' AS fmt FROM report_setups s"
        " JOIN users u ON u.id = s.created_by_user_id ORDER BY s.name",
    )
    assert [tuple(s) for s in setups] == [
        (ADMIN_SETUP_NAME, "students", None, "demo.admin", "xlsx"),
        (FINANCE_SETUP_NAME, "expenses", "owner", "demo.finance", "csv"),
    ]
    assert _actors(migrated_url, "report_setups") == {"demo.finance", "demo.admin"}

    # B5 (#129): the deleted draft, the demo's only soft delete.
    plan = _one(
        migrated_url,
        "SELECT id, status, deleted_at, deleted_by_user_id FROM learning_plans"
        " WHERE title = :t",
        {"t": DELETED_PLAN_TITLE},
    )
    assert plan.id == deleted_step.plan_id
    assert (plan.status, plan.deleted_at, plan.deleted_by_user_id) == (
        "draft",
        FROZEN_NOW,
        tutor,
    )
    deleted = sum(
        int(
            _one(
                migrated_url,
                f"SELECT count(*) AS n FROM {table} WHERE deleted_at IS NOT NULL",  # noqa: S608
            ).n
        )
        for table in SOFT_DELETE_TABLES
    )
    assert deleted == 1
    assert _actors(migrated_url, "learning_plans", "soft_delete") == {"demo.tutor"}

    # B6: nothing else is seeded.
    for table in SEEDED_NOTHING_TABLES:
        assert count(migrated_url, table) == 0, table
    assert (
        run_sql(
            migrated_url,
            "SELECT id FROM audit_events WHERE action = 'administration.retention_run'",
        )
        == []
    )
    languages = run_sql(
        migrated_url,
        "SELECT u.username, p.language FROM users u"
        " LEFT JOIN user_preferences p ON p.user_id = u.id"
        " WHERE u.username LIKE 'demo.%'",
    )
    assert {r.language for r in languages} == {"es"}
    assert owner is not None
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


async def _worker(url: str) -> tuple[ImportRunResult, ExportRunResult, int]:
    engine = short_lived_engine(url)
    try:
        maker = create_sessionmaker(engine)
        imported = await run_imports_with(maker)
        exported = await run_exports_with(maker)
        retained = await run_retention_with(maker, now=FROZEN_NOW)
        return imported, exported, retained.total
    finally:
        await engine.dispose()


def test_the_jobs_do_nothing_after_the_seed_and_a_rerun_writes_nothing(
    migrated_url, env_for, clock, capsys
):
    # C2: the import, export and retention runs at FROZEN_NOW change nothing;
    # C1: a rerun the same day and 10 days later prints 0 rows and changes no
    # table, no settings version and no approval version.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    before = _every_table(migrated_url)
    versions = _versions(migrated_url)

    imported, exported, retained = asyncio.run(_worker(migrated_url))

    assert imported == ImportRunResult(
        jobs=0, batches=0, committed=0, failed=0, expired=0
    )
    assert exported == ExportRunResult()
    assert retained == 0
    assert _every_table(migrated_url) == before
    capsys.readouterr()  # the runs' log lines

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert _every_table(migrated_url) == before
    assert _versions(migrated_url) == versions


def test_dev_writes_no_m8_data(migrated_url, env_for, capsys):
    # A3.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    _no_m8_data(migrated_url)


def test_without_billing_leaves_the_m8_data_out(migrated_url, env_for, clock, capsys):
    # A2, E1: the E2E seed. The five print the flag's skip; nothing M8 is
    # written; the other lines are unchanged.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main(["--without-billing"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{line.split(':')[0]}: skipped ({WITHOUT_BILLING})"
        if line.split(":")[0] in FLAGGED_GENERATORS
        else line
        for line in FIRST_RUN_LINES
    ]
    assert lines[-len(FLAGGED_GENERATORS) :] == [
        f"{name}: skipped ({WITHOUT_BILLING})" for name in FLAGGED_GENERATORS
    ]
    _no_m8_data(migrated_url)
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_354)
        == TABLE_COUNTS_THROUGH_354
    )


def test_the_m8_generators_are_registered_in_order():
    # A1, A2, E7.
    assert len(GENERATOR_NAMES) == 40
    assert [g.name for g in demo.DEMO_GENERATORS] == GENERATOR_NAMES
    first = GENERATOR_NAMES.index(ADMIN_GENERATORS[0])
    assert GENERATOR_NAMES[first - 1] == PROGRESS_REPORTS_GENERATOR
    assert GENERATOR_NAMES[first + 1] == PRICING_GENERATOR
    assert GENERATOR_NAMES[-4:] == list(ADMIN_GENERATORS[1:])
    assert GENERATOR_NAMES[-5] == FINANCE_EXPENSES_GENERATOR
    assert GENERATOR_NAMES[-len(FLAGGED_GENERATORS) :] == list(FLAGGED_GENERATORS)
    assert (FLAGGED_GENERATORS[0], *FLAGGED_GENERATORS[-4:]) == ADMIN_GENERATORS
    assert (
        sum(DEMO_ADMIN_TABLE_COUNTS.values()) + IMPORTED_STUDENT_ROWS
        == DEMO_RECEIPT_MESSAGE_ROWS
        + DEMO_ADMIN_ROWS
        + DEMO_IMPORTS_ROWS
        + DEMO_SETUPS_ROWS
        + DEMO_DELETED_ROWS
    )


# --- A4, A5: stale data, a configured message, a prerequisite, rollback ---------------


def test_data_from_an_earlier_day_is_skipped(migrated_url, env_for, clock, capsys):
    # The generators through academics.demo_progress_reports on 2026-11-04,
    # then every generator 10 days later: every flagged one gives the stale
    # skip (the M8 five included) and writes nothing.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    through = generators_through(PROGRESS_REPORTS_GENERATOR)
    assert demo.main([], generators=through) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in FLAGGED_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    _no_m8_data(migrated_url)


def test_a_message_set_by_hand_is_kept(migrated_url, env_for, clock, capsys):
    # G1's configured skip: a message set before the first run stays; the
    # receipts carry it; the other four still seed.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(PROGRESS_REPORTS_GENERATOR)) == 0
    capsys.readouterr()
    run_sql(
        migrated_url,
        "UPDATE business_settings SET receipt_message_es = 'Mensaje propio',"
        " version = version + 1",
    )

    assert demo.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert f"{ADMIN_GENERATORS[0]}: skipped ({RECEIPT_MESSAGE_CONFIGURED})" in lines
    assert lines[-4:] == FIRST_RUN_LINES[-4:]
    settings = _one(
        migrated_url,
        "SELECT receipt_message_es, receipt_message_en FROM business_settings",
    )
    assert tuple(settings) == ("Mensaje propio", None)
    messages = run_sql(migrated_url, "SELECT snapshot->>'message' AS m FROM receipts")
    assert {m.m for m in messages} == {"Mensaje propio"}


def test_a_missing_review_item_fails_the_run(migrated_url, env_for, clock):
    # R2's item cancelled: administration.demo_admin raises; nothing of the
    # four is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(FINANCE_EXPENSES_GENERATOR)) == 0
    run_sql(
        migrated_url,
        "UPDATE approval_requests SET status = 'cancelled', resolved_at = now()"
        " WHERE request_type = 'academics.progress_report' AND status = 'pending'",
    )

    with pytest.raises(DemoPrerequisiteMissingError):
        demo.main([], generators=_fresh_admin()[-4:])

    for table in M8_TABLES:
        assert count(migrated_url, table) == 0, table
    assert demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_501) == (
        TABLE_COUNTS_THROUGH_501
    )


def _unreadable_balances(today: Any) -> DemoAdminPlan:
    """The opening-balances CSV with no rows (``422 import_file_invalid``)."""
    return dataclasses.replace(plan_demo_admin(today), balances=())


def test_a_failure_in_the_imports_rolls_the_run_back(migrated_url, env_for, clock):
    # A5: the balances job is refused after the other three were written:
    # everything rolls back (the mute, the assignments, the jobs).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(FINANCE_EXPENSES_GENERATOR)) == 0
    students = count(migrated_url, "students")

    with pytest.raises(ImportFileInvalidError):
        demo.main([], generators=_fresh_admin(_unreadable_balances)[-4:])

    for table in M8_TABLES:
        assert count(migrated_url, table) == 0, table
    assert count(migrated_url, "students") == students
    assert (
        run_sql(
            migrated_url,
            "SELECT id FROM approval_requests WHERE assignee_user_id IS NOT NULL",
        )
        == []
    )
    assert demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_501) == (
        TABLE_COUNTS_THROUGH_501
    )
