"""The simulated administration and reporting against PostgreSQL (#554
HF-01f: B, C). One ``small`` Spanish run (shared) with the real clock frozen
at :data:`~tests.integration.simulation_support.FROZEN_NOW`, plus a probe
module that notes, independently of ``administration``, every notification
that exists at the end of the read-snapshot day. The HF-01e-only rerun (B9)
uses its own database; B9's run-date independence is #543 C4's test
(``test_simulation_seed.py``, whose canonical dump holds the HF-01f tables);
B8's coverage sweep and C5's English check are
``test_simulation_coverage.py``. The job parity test (B7) and the new export
(C4) run last: they write on the shared database."""

import asyncio
import uuid
from collections.abc import Iterable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration.imports.tasks import run_imports_with
from app.modules.administration.models import Notification
from app.modules.administration.retention import run_retention_with
from app.modules.identity import service as identity
from app.modules.reporting.dashboard.registry import WIDGETS
from app.modules.reporting.exports import service as exports
from app.modules.reporting.tasks import run_exports_with
from app.wiring import register_approval_wiring
from tests.conftest import frozen_clock
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.administration import (
    ASSIGNMENT_SET,
    BOOKING_NOTICE_HOURS,
    CLOSEOUT_APPROVED,
    POLICY_EFFECTIVE,
    SNAPSHOT_DAY,
)
from tests.factories.simulation.anchor import SIM_ANCHOR, SIM_NOW, sim_clock
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.reporting import PARAMETERS
from tests.factories.simulation.timeline import SEEDED_EARLIER, SimulationContext
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.simulation_support import (
    FROZEN_NOW,
    MODULE_ROWS,
    migrate,
    run_command,
    simulation_env,
    table_counts,
)

PLAN = plan_simulation("es", "small", 1)
ADMIN = PLAN.administration
REPORTING = PLAN.reporting
KPI_RANGE = {"from": "2026-09-01", "to": "2026-11-18"}
HF01F = ("administration", "reporting")


class SnapshotProbe:
    """A test-only last module: every notification id at the end of
    :data:`SNAPSHOT_DAY` (all of them older than the read cutoff)."""

    name = "probe"
    tables: ClassVar[tuple[Any, ...]] = ()
    seen: ClassVar[set[uuid.UUID]] = set()

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return True

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return (SNAPSHOT_DAY,)

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        SnapshotProbe.seen = set(await session.scalars(select(Notification.id)))

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


def _fresh(database_url: str) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


SMALL_RUN: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def es_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, out, err = run_command(
            url, "es", modules=(*SIMULATION_MODULES, SnapshotProbe())
        )
        assert code == 0, err
        SMALL_RUN["out"] = out
        yield url


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    return run_sql(url, sql, params or None)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return rows(url, sql, **params)[0][0]


def user_id(url: str, username: str) -> Any:
    return scalar(url, "SELECT id FROM users WHERE username = :u", u=username)


def _get(client: Any, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


# --- B1: imports ----------------------------------------------------------------------


def test_the_run_prints_the_administration_and_reporting_totals(es_url):
    assert SMALL_RUN["out"][-3:] == [
        f"administration: {MODULE_ROWS['small']['administration']} rows",
        f"reporting: {MODULE_ROWS['small']['reporting']} rows",
        "probe: 0 rows",
    ]


def test_four_import_jobs_with_their_states_counters_and_committers(es_url):
    jobs = {
        r.kind: r
        for r in rows(
            es_url,
            "SELECT j.id, j.kind, j.status, j.total_rows, j.ready_rows,"
            " j.duplicate_rows, j.error_rows, j.created_rows, j.skipped_rows,"
            " j.failed_rows, j.file_name, j.committed_at, c.username AS creator,"
            " m.username AS committer FROM import_jobs j"
            " JOIN users c ON c.id = j.created_by_user_id"
            " LEFT JOIN users m ON m.id = j.committed_by_user_id",
        )
    }
    admin, finance = PLAN.administrator.username, PLAN.finance.username
    counters = {
        kind: (
            j.status,
            j.total_rows,
            j.ready_rows,
            j.duplicate_rows,
            j.error_rows,
            j.created_rows,
            j.skipped_rows,
            j.failed_rows,
        )
        for kind, j in jobs.items()
    }
    assert counters == {
        "students": ("committed", 3, 0, 1, 0, 2, 0, 0),
        "opening_balances": ("committed", 2, 0, 0, 0, 2, 0, 0),
        "schedules": ("committed", 2, 0, 0, 0, 1, 0, 1),
        "tutors": ("staged", 2, 1, 0, 1, 0, 0, 0),
    }
    assert {k: (j.creator, j.committer) for k, j in jobs.items()} == {
        "students": (admin, admin),
        "opening_balances": (finance, finance),
        "schedules": (admin, admin),
        "tutors": (admin, None),
    }
    assert {j.file_name for j in jobs.values()} == set(ADMIN.files.values())
    assert jobs["tutors"].committed_at is None
    # Committed rows drop their values; the staged job keeps them.
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM import_rows r JOIN import_jobs j ON j.id = r.job_id"
            " WHERE j.status = 'committed' AND r.values IS NOT NULL",
        )
        == 0
    )
    failed = scalar(
        es_url,
        "SELECT r.errors FROM import_rows r WHERE r.job_id = :j"
        " AND r.status = 'failed'",
        j=jobs["schedules"].id,
    )
    assert "scheduling_conflict" in str(failed)
    # The opening balances carry their import job.
    charges = rows(
        es_url,
        "SELECT kind, amount, due_date FROM charges WHERE import_job_id = :j",
        j=jobs["opening_balances"].id,
    )
    assert sorted((c.kind, c.amount, c.due_date) for c in charges) == sorted(
        ("manual", Decimal(b.amount), b.due_date) for b in ADMIN.balances
    )


def test_the_imported_children_join_the_family_and_one_tries_two_classes(es_url):
    assert scalar(es_url, "SELECT count(*) FROM students") == len(PLAN.students) + 2
    family = next(f for f in PLAN.families if f.key == ADMIN.family)
    for child in ADMIN.students:
        found = rows(
            es_url,
            "SELECT s.id, f.name, (SELECT count(*) FROM student_guardians g"
            " WHERE g.student_id = s.id) AS guardians,"
            " (SELECT count(*) FROM consent_records c WHERE c.student_id = s.id)"
            " AS consents FROM students s JOIN family_students fs"
            " ON fs.student_id = s.id JOIN families f ON f.id = fs.family_id"
            " WHERE s.given_names = :g AND s.family_names = :f",
            g=child.given_names,
            f=child.family_names,
        )
        assert [(r.name, r.guardians, r.consents) for r in found] == [
            (family.name, len(family.guardians), 1)
        ]
    trial = ADMIN.trial
    child = ADMIN.students[0]
    sessions = rows(
        es_url,
        "SELECT s.state, s.starts_at, c.status AS closeout,"
        " (SELECT sum(a.amount) FROM payment_allocations a JOIN charges ch"
        "  ON ch.id = a.charge_id WHERE ch.class_session_id = s.id) AS paid"
        " FROM class_sessions s JOIN class_session_participants p"
        " ON p.class_session_id = s.id JOIN students st ON st.id = p.student_id"
        " LEFT JOIN session_closeouts c ON c.class_session_id = s.id"
        " WHERE st.given_names = :g AND st.family_names = :f ORDER BY s.starts_at",
        g=child.given_names,
        f=child.family_names,
    )
    assert [r.starts_at for r in sessions] == [p.starts_at for p in trial.placements()]
    for r in sessions:
        assert (r.state, r.closeout) == ("completed", "approved")
        assert r.paid is not None and r.paid > 0
    other = ADMIN.students[1]
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM class_session_participants p JOIN students s"
            " ON s.id = p.student_id WHERE s.given_names = :g",
            g=other.given_names,
        )
        == 0
    )


def test_the_imported_series_is_scheduled_after_the_anchor(es_url):
    series = ADMIN.series
    tutor = user_id(es_url, PLAN.tutor(series.tutor).account.username)
    found = rows(
        es_url,
        "SELECT s.state, s.starts_at FROM class_sessions s JOIN import_rows r"
        " ON r.record_id = s.series_id JOIN import_jobs j ON j.id = r.job_id"
        " WHERE j.kind = 'schedules' AND s.tutor_user_id = :t ORDER BY 1, 2",
        t=tutor,
    )
    assert [r.state for r in found] == ["confirmed"] * len(series.dates)
    assert all(r.starts_at.date() >= SIM_ANCHOR for r in found)
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM assignment_offers o JOIN import_rows r"
            " ON r.record_id = o.class_series_id WHERE o.status = 'accepted'",
        )
        == 1
    )


# --- B2-B4: approvals, preferences, read state ----------------------------------------


def test_two_items_are_assigned_and_the_rest_stay_unassigned(es_url):
    assigned = rows(
        es_url,
        "SELECT a.id, a.request_type, u.username FROM approval_requests a"
        " JOIN users u ON u.id = a.assignee_user_id WHERE a.status = 'pending'",
    )
    assert {(r.request_type, r.username) for r in assigned} == {
        ("staff.qualification", PLAN.administrator.username),
        ("academics.progress_report", PLAN.coordinator.username),
    }
    for r in assigned:
        notified = scalar(
            es_url,
            "SELECT count(*) FROM notifications n JOIN users u"
            " ON u.id = n.recipient_user_id WHERE u.username = :u"
            " AND n.notification_type = 'administration.approval_assigned'"
            " AND n.params->>'request_id' = :r",
            u=r.username,
            r=str(r.id),
        )
        assert notified == 1
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM approval_requests WHERE status = 'pending'"
            " AND assignee_user_id IS NULL",
        )
        >= 2
    )


def test_muted_types_reach_nobody_who_muted_them(es_url):
    opt_outs = rows(
        es_url,
        "SELECT u.username, o.notification_type FROM notification_opt_outs o"
        " JOIN users u ON u.id = o.user_id",
    )
    assert {(r.username, r.notification_type) for r in opt_outs} == {
        (o.username, o.notification_type) for o in ADMIN.opt_outs
    }
    assert {o.notification_type for o in ADMIN.opt_outs} == {
        CLOSEOUT_APPROVED,
        ASSIGNMENT_SET,
    }
    for o in ADMIN.opt_outs:
        assert (
            scalar(
                es_url,
                "SELECT count(*) FROM notifications n JOIN users u"
                " ON u.id = n.recipient_user_id WHERE u.username = :u"
                " AND n.notification_type = :t",
                u=o.username,
                t=o.notification_type,
            )
            == 0
        )
        # The type itself is sent to others during the term.
        assert (
            scalar(
                es_url,
                "SELECT count(*) FROM notifications WHERE notification_type = :t",
                t=o.notification_type,
            )
            > 0
        )


def test_older_notifications_are_read_at_the_anchor_and_newer_ones_unread(es_url):
    """B4: every notification that existed at the end of 11 Nov (business
    event more than 7 days before the anchor) is read at SIM_NOW; every
    later one is unread."""
    assert SnapshotProbe.seen
    found = rows(es_url, "SELECT id, read_at FROM notifications")
    for r in found:
        if r.id in SnapshotProbe.seen:
            assert r.read_at == SIM_NOW, r
        else:
            assert r.read_at is None, r
    unread = dict(
        rows(
            es_url,
            "SELECT r.code, count(*) FILTER (WHERE n.read_at IS NULL)"
            " FROM notifications n JOIN user_roles ur"
            " ON ur.user_id = n.recipient_user_id JOIN roles r ON r.id = ur.role_id"
            " GROUP BY 1",
        )
    )
    # Finance's only notification (its 3 Nov export) is older than 7 days (D).
    for role in (
        "owner",
        "administrator",
        "academic_coordinator",
        "tutor",
        "guardian",
        "student",
    ):
        assert unread.get(role, 0) >= 1, role


# --- B5, B6: setups, exports, layouts, recently deleted -------------------------------


def test_setups_exports_and_layouts(es_url):
    setups = rows(
        es_url,
        "SELECT s.name, s.dataset, s.shared_role, u.username FROM report_setups s"
        " JOIN users u ON u.id = s.created_by_user_id",
    )
    assert {(s.name, s.username, s.shared_role) for s in setups} == {
        (p.name, getattr(PLAN, role).username, p.shared_role)
        for p in REPORTING.setups
        for role in [
            {
                "admin": "administrator",
                "coordinator": "coordinator",
                "finance": "finance",
            }[p.creator]
        ]
    }
    jobs = rows(
        es_url,
        "SELECT j.status, j.dataset, j.row_count, j.finished_at, j.expires_at,"
        " (SELECT count(*) FROM export_files f WHERE f.job_id = j.id) AS files,"
        " u.username FROM export_jobs j JOIN users u ON u.id = j.requested_by_user_id"
        " ORDER BY j.finished_at",
    )
    assert [(j.status, j.dataset, j.files, j.username) for j in jobs] == [
        ("expired", "expenses", 0, PLAN.finance.username),
        ("ready", "class_sessions", 1, PLAN.owner.username),
    ]
    assert all(j.row_count > 0 for j in jobs)
    ready = jobs[1]
    assert ready.expires_at == ready.finished_at + timedelta(hours=24)
    assert ready.finished_at < SIM_NOW < ready.expires_at
    layouts = {
        r.username
        for r in rows(
            es_url,
            "SELECT u.username FROM dashboard_layouts d JOIN users u"
            " ON u.id = d.user_id",
        )
    }
    assert layouts == {PLAN.owner.username, PLAN.finance.username}


def test_two_records_are_restorable_by_the_tutor_who_deleted_them(es_url):
    deleted = REPORTING.deleted
    tutor = PLAN.tutor(deleted.tutor).account
    assert (
        scalar(
            es_url,
            "SELECT (SELECT count(*) FROM learning_plans"
            "  WHERE deleted_at IS NOT NULL)"
            " + (SELECT count(*) FROM session_notes WHERE deleted_at IS NOT NULL)",
        )
        == 2
    )
    with frozen_clock(FROZEN_NOW), auth_client(es_url) as client:
        assert login(client, tutor.username, tutor.password).status_code == 200
        items = _get(client, "/api/v1/deleted-records")["items"]
    assert sorted(i["kind"] for i in items) == ["learning_plan", "session_note"]
    assert {i["title"] for i in items} == {deleted.title, None}


def test_the_settings_history_and_the_policy_version(es_url):
    assert scalar(es_url, "SELECT contact_email FROM business_settings") == (
        ADMIN.contact_email
    )
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM audit_events WHERE record_type = 'business_settings'"
            " AND action = 'update'",
        )
        >= 2
    )
    latest = rows(
        es_url,
        "SELECT effective_from, booking_notice_hours FROM scheduling_policies"
        " ORDER BY effective_from DESC LIMIT 1",
    )[0]
    assert (latest.effective_from, latest.booking_notice_hours) == (
        POLICY_EFFECTIVE,
        BOOKING_NOTICE_HOURS,
    )


# --- B9: rerun and skip ---------------------------------------------------------------


def test_a_rerun_writes_nothing(es_url):
    before = table_counts(es_url)

    code, out, _ = run_command(es_url, "es")

    assert code == 0
    assert out[-2:] == ["administration: 0 rows", "reporting: 0 rows"]
    assert table_counts(es_url) == before


def test_a_database_seeded_by_hf01e_only_skips_administration_and_reporting(
    database_url,
):
    earlier = tuple(m for m in SIMULATION_MODULES if m.name not in HF01F)
    for url in _fresh(database_url):
        assert run_command(url, "es", modules=earlier)[0] == 0
        before = table_counts(url)

        code, out, _ = run_command(url, "es")

        assert code == 0
        assert out[-3:] == [
            "finance: 0 rows",
            f"administration: skipped ({SEEDED_EARLIER})",
            f"reporting: skipped ({SEEDED_EARLIER})",
        ]
        assert table_counts(url) == before


# --- C1, C2: KPIs and dashboards per role ---------------------------------------------


def test_the_kpis_on_the_simulated_term(es_url):
    owner = PLAN.owner
    with frozen_clock(FROZEN_NOW), auth_client(es_url) as client:
        assert login(client, owner.username, owner.password).status_code == 200
        trend = _get(
            client, "/api/v1/reports/kpis/revenue-trend", basis="accrual", **KPI_RANGE
        )
        courses = _get(client, "/api/v1/reports/kpis/course-profitability", **KPI_RANGE)
        rooms = _get(client, "/api/v1/reports/kpis/room-utilization", **KPI_RANGE)
        retention = _get(
            client,
            "/api/v1/reports/kpis/student-retention",
            **{"from": "2026-10-01", "to": "2026-10-31"},
        )
        tutors = _get(client, "/api/v1/reports/kpis/tutor-performance", **KPI_RANGE)
    # K1: revenue in each of the three months.
    assert [m["month"] for m in trend["months"]] == ["2026-09", "2026-10", "2026-11"]
    assert all(Decimal(m["revenue_total"]) > 0 for m in trend["months"])
    # K2: both cohorts, with revenue and cost.
    assert {c["cohort"]["name"] for c in courses["cohorts"]} == {
        c.name for c in PLAN.timetable.cohorts
    }
    assert Decimal(courses["total"]["revenue"]) > 0
    # K4: every active room with a percent; the deactivated one is not listed.
    active = {r.name for r in PLAN.classrooms if r.deactivated_at is None}
    assert {r["room"]["name"] for r in rooms["rooms"]} == active
    assert all(r["utilization_percent"] is not None for r in rooms["rooms"])
    # K5: October against September: the trial child is lost.
    assert retention["retained"] >= 1
    assert retention["new"] >= 1
    assert retention["lost"] >= 1
    # K6: every tutor, with attendance.
    names = {f"{t.given_names} {t.family_names}" for t in PLAN.tutors}
    assert {t["tutor"]["display_name"] for t in tutors["tutors"]} == names
    assert all(Decimal(t["attendance_percent"]) > 0 for t in tutors["tutors"])


@pytest.mark.parametrize("role", ["owner", "administrator", "coordinator", "finance"])
def test_each_staff_dashboard_and_its_kpi_access(es_url, role):
    account = getattr(PLAN, role)
    layout = next(
        (item for item in REPORTING.layouts if getattr(PLAN, item.user) == account),
        None,
    )
    with frozen_clock(FROZEN_NOW), auth_client(es_url) as client:
        assert login(client, account.username, account.password).status_code == 200
        found = _get(client, "/api/v1/reports/dashboard/layout")
        widgets = [w["widget"] for w in found["widgets"]]
        for widget in WIDGETS:
            params: dict[str, str] = dict(KPI_RANGE)
            if widget.code == "revenue_trend":
                params["basis"] = "accrual"
            if widget.code == "balances":
                params = {}
            status = client.get(widget.route, params=params).status_code
            assert status == (200 if widget.code in widgets else 403), (
                role,
                widget.code,
                status,
            )
    assert found["customized"] is (layout is not None)
    if layout is None:
        # The role default: every available widget, in registry order, shown.
        assert widgets == [w.code for w in WIDGETS if w.code in widgets]
        assert all(w["visible"] for w in found["widgets"])
    else:
        assert widgets[: len([c for c in layout.order if c in widgets])] == [
            c for c in layout.order if c in widgets
        ]
        hidden = {w["widget"] for w in found["widgets"] if not w["visible"]}
        assert hidden == {layout.hidden} & set(widgets)


# --- C3: the audit log ----------------------------------------------------------------

MODULE_RECORDS = {
    "users": "users",
    "students": "students",
    "subjects": "subjects",
    "tutor_qualifications": "tutor_qualifications",
    "business_settings": "business_settings",
    "class_sessions": "class_sessions",
    "session_notes": "session_notes",
    "charges": "charges",
    # A pay period's audit record type is ``payroll_period`` (#110).
    "payroll_period": "payroll_periods",
    "expenses": "expenses",
    "import_jobs": "import_jobs",
    "report_setups": "report_setups",
}
"""A record type per module (people, catalog, staff, settings, schedule,
academics, billing, payroll, finance, administration, reporting)."""


@pytest.mark.parametrize("role", ["owner", "administrator"])
def test_the_audit_log_has_every_module_and_record_histories(es_url, role):
    account = getattr(PLAN, role)
    charge = scalar(es_url, "SELECT id FROM charges WHERE kind = 'manual' LIMIT 1")
    session_id = scalar(
        es_url, "SELECT id FROM class_sessions WHERE state = 'completed' LIMIT 1"
    )
    period = scalar(
        es_url, "SELECT id FROM payroll_periods WHERE status = 'finalized' LIMIT 1"
    )
    firsts = {
        record_type: scalar(es_url, f'SELECT id FROM "{table}" LIMIT 1')  # noqa: S608
        for record_type, table in MODULE_RECORDS.items()
    }
    with frozen_clock(FROZEN_NOW), auth_client(es_url) as client:
        assert login(client, account.username, account.password).status_code == 200
        for record_type in MODULE_RECORDS:
            page = _get(
                client,
                "/api/v1/audit-events",
                record_type=record_type,
                record_id=str(firsts[record_type]),
            )
            assert page["items"], record_type
        for record_type, record_id in (
            ("charges", charge),
            ("class_sessions", session_id),
            ("payroll_period", period),
        ):
            items = _get(
                client,
                "/api/v1/audit-events",
                record_type=record_type,
                record_id=str(record_id),
            )["items"]
            assert items, record_type
            if record_type != "payroll_period":  # recalculated, then finalized
                assert items[-1]["action"] == "create", record_type
            occurred = [i["occurred_at"] for i in items]
            assert occurred == sorted(occurred, reverse=True)
            if record_type != "charges":
                assert len(items) >= 2, record_type


# --- B7, C4: the jobs at the anchor and a new export (last: they write) ---------------


def test_zz_the_jobs_have_nothing_to_do_at_the_anchor(es_url):
    assert scalar(es_url, "SELECT count(*) FROM job_failures") == 0
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
    staged_at = scalar(
        es_url, "SELECT updated_at FROM import_jobs WHERE status = 'staged'"
    )
    expires = staged_at + timedelta(days=7) < SIM_NOW

    async def run() -> tuple[Any, Any, Any]:
        engine = short_lived_engine(es_url)
        try:
            maker = create_sessionmaker(engine)
            with sim_clock(SIM_NOW):
                retention = await run_retention_with(maker, now=SIM_NOW)
                imported = await run_imports_with(maker)
                exported = await run_exports_with(maker)
            return retention, imported, exported
        finally:
            await engine.dispose()

    with simulation_env(es_url):
        register_approval_wiring()
        retention, imported, exported = asyncio.run(run())

    assert retention.total == 0 and not retention.failed
    # The staged tutors file expires 7 days after its real-time last change
    # (E9): only when the seed ran more than 7 days before the anchor.
    assert (imported.batches, imported.committed, imported.failed) == (0, 0, 0)
    assert imported.expired == int(expires)
    assert (exported.jobs, exported.ready, exported.failed, exported.expired) == (
        0,
        0,
        0,
        0,
    )
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0


def test_zz_finance_exports_the_term_and_the_rows_match_the_list(es_url):
    """C4: a new expenses export for 1 Sep - 18 Nov (request and build, in
    one Unit of Work as the worker would) has the list's total."""
    filters = {"incurred_from": "2026-09-01", "incurred_to": "2026-11-18"}
    finance = PLAN.finance
    finance_id = user_id(es_url, finance.username)

    async def run() -> int | None:
        engine = short_lived_engine(es_url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                me = await identity.load_principal(uow.session, finance_id)
                assert me is not None
                job = await exports.request_export(
                    uow.session,
                    principal=me,
                    parameters=PARAMETERS.validate_python(
                        {"dataset": "expenses", "format": "csv", "filters": filters}
                    ),
                    guard=None,  # type: ignore[arg-type]  # not a personal dataset
                )
                await exports.run_job(uow.session, job.id)
                return (
                    await exports.get_job(uow.session, principal=me, job_id=job.id)
                ).row_count
        finally:
            await engine.dispose()

    with simulation_env(es_url):
        row_count = asyncio.run(run())
    with frozen_clock(FROZEN_NOW), auth_client(es_url) as client:
        assert login(client, finance.username, finance.password).status_code == 200
        listed = _get(client, "/api/v1/finance/expenses", limit=1, **filters)
    assert row_count == listed["total"] > 0
