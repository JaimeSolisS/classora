"""The simulated operations seed against PostgreSQL (#543 HF-01: A4, B,
C4, C5, D). Every run uses the ``small`` profile with the real clock frozen
at :data:`~tests.integration.simulation_support.FROZEN_NOW`, except the
run-date independence test (C4) and the one ``full`` run (D1, D7)."""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from tests.conftest import frozen_clock
from tests.factories import demo
from tests.factories.base import RESERVED_EMAIL_DOMAINS
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.anchor import SIM_NOW, SIM_START, local_instant
from tests.factories.simulation.plan import (
    STAFF_USERNAMES,
    plan_simulation,
)
from tests.factories.simulation.settings import SettingsModule
from tests.factories.simulation.texts import POOLS, RECEIPT_MESSAGES
from tests.factories.simulation.timeline import SEEDED_EARLIER
from tests.integration.auth_support import auth_client, create_user, login
from tests.integration.conftest import run_sql
from tests.integration.demo_support import FROZEN_NOW as DEMO_FROZEN_NOW
from tests.integration.simulation_support import (
    FROZEN_NOW,
    RERUN_SEED_LINES,
    SERVICE_CANCELLATION_REASONS,
    academic_free_text,
    billing_free_text,
    billing_texts,
    canonical_dump,
    composed_texts,
    dump_differences,
    first_run_lines,
    free_text,
    migrate,
    payroll_free_text,
    payroll_texts,
    run_command,
    simulation_env,
    table_counts,
)

ACCENTED = set("áéíóúñ¿¡")
FROZEN_LATER = datetime(2027, 1, 10, 12, 0, tzinfo=UTC)
"""A rerun long after the first run still changes nothing (B2)."""


def _fresh(database_url: str) -> Iterator[str]:
    import uuid

    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


@pytest.fixture(scope="module")
def es_url(database_url: str) -> Iterator[str]:
    """B1's database: one ``small`` Spanish run (shared, read only)."""
    for url in _fresh(database_url):
        code, _, _ = run_command(url, "es")
        assert code == 0
        yield url


@pytest.fixture(scope="module")
def en_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, _, _ = run_command(url, "en")
        assert code == 0
        yield url


FULL_RUN: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def full_url(database_url: str) -> Iterator[str]:
    """The one ``full`` Spanish run (D1, D7)."""
    for url in _fresh(database_url):
        code, out, _ = run_command(url, "es", profile="full")
        assert code == 0
        FULL_RUN["out"] = out
        yield url


@pytest.fixture
def migrated_url(head_url: str) -> str:
    return head_url


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    return run_sql(url, sql, params or None)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return rows(url, sql, **params)[0][0]


# --- A4, B: the command, the clean check, reruns and atomicity --------------------


def test_first_run_prints_the_seed_lines_and_pinned_module_totals(migrated_url):
    code, out, err = run_command(migrated_url, "es")

    assert (code, err) == (0, "")
    assert out == first_run_lines("small")
    names = rows(migrated_url, "SELECT username, display_name FROM users")
    printed = "\n".join(out)
    assert all(u.username not in printed for u in names)


def test_a_rerun_writes_nothing(migrated_url):
    assert run_command(migrated_url, "es")[0] == 0
    before = table_counts(migrated_url)

    code, out, err = run_command(migrated_url, "es", now=FROZEN_LATER)

    assert (code, err) == (0, "")
    assert out == [
        *RERUN_SEED_LINES,
        "people: 0 rows",
        "catalog: 0 rows",
        "staff: 0 rows",
        "settings: 0 rows",
        "schedule: 0 rows",
        "courses: 0 rows",
        "academics: 0 rows",
        "billing: 0 rows",
        "payroll: 0 rows",
        "finance: 0 rows",
        "administration: 0 rows",
        "reporting: 0 rows",
    ]
    assert table_counts(migrated_url) == before


def test_the_other_language_is_refused(migrated_url):
    assert run_command(migrated_url, "es")[0] == 0
    before = table_counts(migrated_url)

    code, out, err = run_command(migrated_url, "en")

    assert code == 3
    assert out == []
    assert err.strip() == (
        "The database holds the Spanish simulation; use a separate database"
        ' (README "Simulated operations data")'
    )
    assert table_counts(migrated_url) == before


def assert_not_empty_refusal(url: str) -> None:
    before = table_counts(url)

    code, out, err = run_command(url, "es")

    assert code == 3
    assert out == []
    assert err.startswith(
        "The database is not empty (for example demo data or a bootstrap Owner);"
        " seed-data needs a freshly migrated database. Reset:"
    )
    assert "docker compose down -v" in err and "make db-create" in err
    assert table_counts(url) == before


def test_a_database_with_a_user_is_refused(seeded_url):
    create_user(seeded_url, roles=("owner",))

    assert_not_empty_refusal(seeded_url)


def test_a_demo_database_is_refused(migrated_url):
    with simulation_env(migrated_url), frozen_clock(DEMO_FROZEN_NOW):
        assert demo.main([]) == 0

    assert_not_empty_refusal(migrated_url)


def test_billing_turned_on_by_hand_is_refused(seeded_url):
    run_sql(seeded_url, "UPDATE billing_settings SET billing_starts_on = '2026-09-01'")

    assert_not_empty_refusal(seeded_url)


def test_a_database_not_at_head_exits_1(fresh_database_url):
    code, out, err = run_command(fresh_database_url, "es")

    assert code == 1
    assert out == []
    assert "make migrate" in err or "head" in err


class FailingLastModule(SettingsModule):
    # Replaces the last module (HF-01f: ``reporting``); ``settings`` itself
    # still runs, so this one must not set up the settings again.
    name = "failing"

    async def setup(self, session: Any, ctx: Any) -> None:
        return None

    def days(self, ctx: Any) -> set[date]:
        return {SIM_START}

    async def on_day(self, session: Any, ctx: Any, day: date) -> None:
        raise RuntimeError("injected")


def test_a_failure_in_the_last_module_rolls_back_the_whole_run(migrated_url):
    before = table_counts(migrated_url)
    modules = (*SIMULATION_MODULES[:-1], FailingLastModule())

    with pytest.raises(RuntimeError, match="injected"):
        run_command(migrated_url, "es", modules=modules)

    assert table_counts(migrated_url) == before


class LaterModule(SettingsModule):
    name = "later"

    async def marker(self, session: Any, ctx: Any) -> bool:
        return False

    async def setup(self, session: Any, ctx: Any) -> None:  # pragma: no cover
        raise AssertionError("a rerun runs no step")


def test_a_module_added_later_is_skipped_on_a_seeded_database(migrated_url):
    assert run_command(migrated_url, "es")[0] == 0
    before = table_counts(migrated_url)

    code, out, _ = run_command(
        migrated_url, "es", modules=(*SIMULATION_MODULES, LaterModule())
    )

    assert code == 0
    assert out[-1] == f"later: skipped ({SEEDED_EARLIER})"
    assert table_counts(migrated_url) == before


# --- C4, C5: the run date doesn't matter --------------------------------------------


def test_the_run_date_does_not_change_the_data(database_url):
    dumps = []
    for now in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        for url in _fresh(database_url):
            code, _, _ = run_command(url, "es", now=now)
            assert code == 0
            dumps.append(canonical_dump(url))

    # Compared table by table with a short, field-level message: pytest's
    # diff of two whole dumps (thousands of long strings) takes hours.
    differences = dump_differences(dumps[0], dumps[1])
    assert not differences, differences
    assert sum(len(v) for v in dumps[0].values()) > 250
    # HF-01b B7 and HF-01c B7: the schedule, the courses and the academic
    # record are in the dump too.
    for table in (
        "class_sessions",
        "session_closeouts",
        "course_cohorts",
        "session_notes",
        "evaluations",
        "learning_plan_milestones",
        "assignment_submissions",
        "progress_reports",
        # HF-01e (#553 B6): payroll and expenses.
        "payroll_periods",
        "payroll_lines",
        "payroll_adjustments",
        "payslips",
        "expenses",
        "expense_payments",
    ):
        assert dumps[0][table], table


def local(day: date, hhmm: str) -> datetime:
    return local_instant(day, hhmm)


def test_business_instants_are_the_plans_not_the_real_clock(es_url):
    accepted = rows(es_url, "SELECT accepted_at FROM privacy_notice_acceptances")
    assert {r.accepted_at for r in accepted} == {
        local(date(2026, 10, 19), "09:00"),
        local(date(2026, 10, 19), "09:30"),
    }
    granted = {
        r.granted_at for r in rows(es_url, "SELECT granted_at FROM consent_records")
    }
    assert granted == {local(date(2026, 10, 19), "09:05")}
    revoked = scalar(
        es_url,
        "SELECT revoked_at FROM tutor_qualifications WHERE status = 'revoked'",
    )
    assert revoked == local(date(2026, 10, 20), "10:00")
    rejected = scalar(
        es_url,
        "SELECT a.resolved_at FROM approval_requests a"
        " JOIN tutor_qualifications q ON q.id = a.subject_id"
        " WHERE q.status = 'rejected'",
    )
    assert rejected == local(date(2026, 9, 10), "11:00")
    # HF-01's exceptions (HF-01b's time off has its own notes).
    exceptions = rows(
        es_url,
        "SELECT starts_on FROM tutor_availability_exceptions"
        " WHERE note = ANY(:notes) ORDER BY 1",
        notes=POOLS["es"]["exception_notes"],
    )
    assert [r.starts_on for r in exceptions] == [
        date(2026, 9, 25),
        date(2026, 10, 17),
        date(2026, 10, 22),
        date(2026, 11, 26),
        date(2026, 11, 28),
    ]
    decided = rows(
        es_url, "SELECT status, decided_at FROM registration_requests ORDER BY status"
    )
    assert [(r.status, r.decided_at) for r in decided] == [
        ("approved", local(date(2026, 9, 7), "12:00")),
        ("pending", None),
    ]
    plan = plan_simulation("es", "small", 1)
    tutor4 = plan.tutor("t04").account.username
    activated = scalar(
        es_url,
        "SELECT t.used_at FROM account_tokens t JOIN users u ON u.id = t.user_id"
        " WHERE u.username = :name",
        name=tutor4,
    )
    assert activated == local(date(2026, 10, 1), "09:00")
    deactivated = scalar(
        es_url, "SELECT is_active FROM classrooms WHERE name = 'Aula 3'"
    )
    assert deactivated is False


# --- D: people, catalog, staff and settings --------------------------------------


def test_people_counts_small(es_url):
    # HF-01f: + the two imported children.
    assert scalar(es_url, "SELECT count(*) FROM students") == 12
    assert scalar(es_url, "SELECT count(*) FROM families") == 8
    # HF-01d: + the adult's paying parent; every first guardian has a login.
    assert scalar(es_url, "SELECT count(*) FROM guardians") == 11
    assert (
        scalar(es_url, "SELECT count(*) FROM guardians WHERE user_id IS NOT NULL") == 8
    )
    assert (
        scalar(es_url, "SELECT count(*) FROM students WHERE user_id IS NOT NULL") == 2
    )
    tutors = scalar(
        es_url,
        "SELECT count(*) FROM users u JOIN user_roles ur ON ur.user_id = u.id"
        " JOIN roles r ON r.id = ur.role_id WHERE r.code = 'tutor'"
        " AND u.state = 'active'",
    )
    assert tutors == 4
    staff = {
        r.username
        for r in rows(
            es_url,
            "SELECT u.username FROM users u JOIN user_roles ur ON ur.user_id = u.id"
            " JOIN roles r ON r.id = ur.role_id WHERE r.code IN"
            " ('owner', 'administrator', 'academic_coordinator', 'finance_staff')",
        )
    }
    assert staff == set(STAFF_USERNAMES["es"].values())


def test_full_profile_counts_and_usernames(full_url):
    assert scalar(full_url, "SELECT count(*) FROM students") == 42
    assert scalar(full_url, "SELECT count(*) FROM families") == 30
    assert scalar(full_url, "SELECT count(*) FROM guardians") == 46
    assert (
        scalar(full_url, "SELECT count(*) FROM guardians WHERE user_id IS NOT NULL")
        == 30
    )
    assert (
        scalar(full_url, "SELECT count(*) FROM students WHERE user_id IS NOT NULL") == 6
    )
    assert scalar(full_url, "SELECT count(*) FROM tutor_profiles") == 12
    adults = scalar(
        full_url,
        "SELECT count(*) FROM students WHERE birthdate <= DATE '2008-08-24'",
    )
    assert adults == 4
    usernames = {r.username for r in rows(full_url, "SELECT username FROM users")}
    assert set(STAFF_USERNAMES["es"].values()) <= usernames


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("direccion", "classora-sim-owner"),
        ("administracion", "classora-sim-admin"),
        ("coordinacion", "classora-sim-coordinator"),
        ("finanzas", "classora-sim-finance"),
    ],
)
def test_staff_sign_in_with_their_role_password(es_url, username, password):
    with auth_client(es_url) as client:
        response = login(client, username, password)
        assert response.status_code == 200, response.text


def test_a_tutor_a_guardian_and_a_student_sign_in(es_url):
    plan = plan_simulation("es", "small", 1)
    tutor = plan.tutors[0].account
    guardian = next(g.account for g in plan.guardians if g.account is not None)
    student = next(s.account for s in plan.students if s.account is not None)
    with auth_client(es_url) as client:
        for account in (tutor, guardian, student):
            response = login(client, account.username, account.password)
            assert response.status_code == 200, account.role
            client.cookies.clear()
    without_login = [g for g in plan.guardians if g.account is None]
    names = {r.display_name for r in rows(es_url, "SELECT display_name FROM users")}
    assert all(f"{g.given_names} {g.family_names}" not in names for g in without_login)


def test_the_pending_tutor_invitation_expires_a_day_after_the_anchor(es_url):
    plan = plan_simulation("es", "small", 1)
    [token] = rows(
        es_url,
        "SELECT t.kind, t.used_at, t.expires_at, u.state FROM account_tokens t"
        " JOIN users u ON u.id = t.user_id WHERE u.username = :name",
        name=plan.pending_tutor.username,
    )
    assert (token.kind, token.used_at, token.state) == ("invitation", None, "pending")
    assert token.expires_at == SIM_NOW + timedelta(hours=24)


def test_every_login_accepts_the_notice_and_stores_the_language(es_url, en_url):
    for url, language in ((es_url, "es"), (en_url, "en")):
        logins = rows(
            url,
            "SELECT u.id, p.language, a.accepted_at, v.version, v.published_at"
            " FROM users u"
            " JOIN user_preferences p ON p.user_id = u.id"
            " JOIN privacy_notice_acceptances a ON a.user_id = u.id"
            " JOIN privacy_notice_versions v ON v.id = a.notice_version_id"
            " WHERE u.state = 'active'",
        )
        assert len(logins) == scalar(
            url, "SELECT count(*) FROM users WHERE state = 'active'"
        )
        assert len(logins) == 18  # HF-01d: 5 more guardian logins
        for row in logins:
            assert row.language == language and row.version == 1
            assert row.accepted_at >= row.published_at
        themes = {r.theme for r in rows(url, "SELECT theme FROM user_preferences")}
        assert themes == {"light", "dark", None}


def free_texts(url: str) -> list[str]:
    return [
        *(r.name for r in rows(url, "SELECT name FROM subjects")),
        *(r.name for r in rows(url, "SELECT name FROM classrooms")),
        *(r.name for r in rows(url, "SELECT name FROM closures")),
        *(r.name for r in rows(url, "SELECT name FROM academic_periods")),
        *(r.note for r in rows(url, "SELECT note FROM tutor_availability_exceptions")),
        *(
            r.professional_experience
            for r in rows(url, "SELECT professional_experience FROM tutor_profiles")
        ),
        scalar(url, "SELECT center_name FROM business_settings"),
        scalar(url, "SELECT address FROM business_settings"),
        scalar(
            url,
            "SELECT revocation_reason FROM tutor_qualifications"
            " WHERE status = 'revoked'",
        ),
        scalar(
            url,
            "SELECT reason FROM approval_requests WHERE status = 'rejected'",
        ),
        # HF-01b (#550 B10): the schedule's and the courses' free text.
        *(
            value
            for _, value in free_text(url)
            if value not in SERVICE_CANCELLATION_REASONS
        ),
    ]


def pool_values(language: str) -> set[str]:
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            values.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(POOLS[language])  # type: ignore[index]
    return values


def test_free_text_comes_from_the_run_languages_pool(es_url, en_url):
    for url, language in ((es_url, "es"), (en_url, "en")):
        texts = free_texts(url)
        assert set(texts) <= pool_values(language)
        receipts = rows(
            url, "SELECT receipt_message_es, receipt_message_en FROM business_settings"
        )[0]
        assert tuple(receipts) == (RECEIPT_MESSAGES["es"], RECEIPT_MESSAGES["en"])
    for value in free_texts(en_url):
        assert not ACCENTED & set(value), value
    # HF-01c (#551 B6): the academic texts, pool values or composed by the
    # plan from pool fragments.
    for url, language in ((es_url, "es"), (en_url, "en")):
        academic = [value for _, value in academic_free_text(url)]
        allowed = pool_values(language) | composed_texts(
            plan_simulation(language, "small", 1)
        )
        assert academic and set(academic) <= allowed
        if language == "en":
            assert not [v for v in academic if ACCENTED & set(v)]
    # HF-01d (#552 B7): the billing texts, from the run language's pool.
    for url, language in ((es_url, "es"), (en_url, "en")):
        billing = [value for _, value in billing_free_text(url)]
        assert billing and set(billing) <= billing_texts(language)
        if language == "en":
            assert not [v for v in billing if ACCENTED & set(v)]
        # B4 in both languages: every family's first guardian has a login
        # with the run language, so every receipt is in it, with its message.
        receipts = rows(
            url, "SELECT language, snapshot->>'message' AS message FROM receipts"
        )
        assert receipts
        assert {(r.language, r.message) for r in receipts} == {
            (language, RECEIPT_MESSAGES[language])
        }
    # HF-01e (#553 B7): payroll and expense texts from the run language's pool,
    # and every payslip in the run language.
    for url, language in ((es_url, "es"), (en_url, "en")):
        texts = [value for _, value in payroll_free_text(url)]
        assert texts and set(texts) <= payroll_texts(language)
        if language == "en":
            assert not [v for v in texts if ACCENTED & set(v)]
        payslips = rows(url, "SELECT DISTINCT language FROM payslips")
        assert [r.language for r in payslips] == [language]


def test_reference_names_are_english_only_in_english(es_url, en_url):
    levels = "SELECT name FROM education_levels ORDER BY sort_order"
    categories = "SELECT name FROM expense_categories ORDER BY name"
    assert [r.name for r in rows(en_url, levels)] == [
        "Elementary school",
        "Middle school",
        "High school",
        "University",
    ]
    assert [r.name for r in rows(en_url, categories)] == [
        "Equipment",
        "Maintenance",
        "Rent",
        "Supplies",
        "Teaching materials",
        "Utilities",
    ]
    assert [r.name for r in rows(es_url, levels)] == [
        "Primaria",
        "Secundaria",
        "Preparatoria",
        "Universidad",
    ]
    assert [r.name for r in rows(es_url, categories)] == [
        "Equipo",
        "Insumos",
        "Mantenimiento",
        "Material didáctico",
        "Renta",
        "Servicios",
    ]


def test_names_follow_each_language(es_url, en_url):
    people = (
        "SELECT given_names, family_names FROM students"
        " UNION ALL SELECT given_names, family_names FROM guardians"
    )
    es = rows(es_url, people)
    en = rows(en_url, people)
    assert all(len(r.family_names.split()) >= 2 for r in es)
    assert all(
        re.fullmatch(r"[A-Za-z' -]+", f"{r.given_names} {r.family_names}") for r in en
    )
    es_names = {f"{r.given_names} {r.family_names}" for r in es}
    en_names = {f"{r.given_names} {r.family_names}" for r in en}
    assert not es_names & en_names
    emails = [r.email for r in rows(es_url, "SELECT email FROM guardians")]
    assert all(e.rsplit("@", 1)[1] in RESERVED_EMAIL_DOMAINS for e in emails)


def test_catalog(es_url):
    assert rows(es_url, "SELECT name, is_active FROM classrooms ORDER BY name") == [
        ("Aula 1", True),
        ("Aula 2", True),
        ("Aula 3", False),
    ]
    assert scalar(es_url, "SELECT count(*) FROM classroom_availability_windows") == 5
    closures = rows(es_url, "SELECT starts_on, ends_on FROM closures ORDER BY 1")
    assert [(r.starts_on, r.ends_on) for r in closures] == [
        (date(2026, 9, 16), date(2026, 9, 16)),
        (date(2026, 11, 2), date(2026, 11, 2)),
        (date(2026, 11, 16), date(2026, 11, 16)),
    ]
    period = rows(es_url, "SELECT name, starts_on, ends_on FROM academic_periods")
    assert [tuple(r) for r in period] == [
        ("Ciclo escolar 2026–2027", date(2026, 8, 24), date(2027, 7, 9))
    ]
    assert (
        scalar(es_url, "SELECT count(*) FROM subjects WHERE education_level_id IS NULL")
        == 0
    )
    levels = scalar(es_url, "SELECT count(DISTINCT education_level_id) FROM subjects")
    assert levels == 4


def test_full_staff_profiles(full_url):
    url = full_url
    texts = [
        r.professional_experience
        for r in rows(url, "SELECT professional_experience FROM tutor_profiles")
    ]
    assert len(texts) == 12 and len(set(texts)) == 12 and all(texts)
    approved = rows(
        url,
        "SELECT s.id, count(*) AS n FROM tutor_qualifications q"
        " JOIN subjects s ON s.id = q.subject_id WHERE q.status = 'approved'"
        " GROUP BY s.id",
    )
    assert len(approved) == 12
    assert all(r.n >= 2 for r in approved)
    assert sum(r.n >= 3 for r in approved) >= 4
    per_tutor = rows(
        url,
        "SELECT q.tutor_user_id, count(*) AS n, count(DISTINCT s.education_level_id)"
        " AS levels FROM tutor_qualifications q JOIN subjects s ON s.id = q.subject_id"
        " WHERE q.status = 'approved' GROUP BY 1",
    )
    assert sum(r.n == 5 and r.levels == 2 for r in per_tutor) >= 2
    assert sum(r.n == 2 and r.levels == 1 for r in per_tutor) >= 2
    statuses = dict(
        rows(url, "SELECT status, count(*) FROM tutor_qualifications GROUP BY 1")
    )
    assert statuses == {"approved": 39, "rejected": 1, "revoked": 1, "pending": 1}
    pending = scalar(
        url,
        "SELECT a.status FROM approval_requests a JOIN tutor_qualifications q"
        " ON q.id = a.subject_id WHERE q.status = 'pending'",
    )
    assert pending == "pending"

    windows = rows(
        url,
        "SELECT w.weekday, w.starts_at, w.ends_at, h.opens_at, h.closes_at"
        " FROM tutor_availability_windows w"
        " LEFT JOIN operating_hours h ON h.weekday = w.weekday",
    )
    assert windows
    assert all(
        r.opens_at is not None and r.opens_at <= r.starts_at < r.ends_at <= r.closes_at
        for r in windows
    )
    tutors_with_windows = scalar(
        url,
        "SELECT count(DISTINCT tutor_user_id) FROM tutor_availability_windows",
    )
    assert tutors_with_windows == 12
    split = scalar(
        url,
        "SELECT count(DISTINCT tutor_user_id) FROM (SELECT tutor_user_id, weekday"
        " FROM tutor_availability_windows GROUP BY 1, 2 HAVING count(*) >= 2) d",
    )
    assert split >= 2
    limits = rows(
        url,
        "SELECT count(daily_limit_minutes) AS daily, count(weekly_limit_minutes)"
        " AS weekly, count(required_break_minutes) AS brk,"
        " count(*) FILTER (WHERE daily_limit_minutes IS NULL AND"
        " weekly_limit_minutes IS NULL AND (required_break_minutes IS NULL OR"
        " required_break_minutes = 0)) AS unlimited"
        " FROM tutor_availability_settings",
    )[0]
    assert limits.daily >= 4 and limits.weekly >= 3 and limits.brk >= 4
    assert limits.unlimited >= 1
    exceptions = rows(
        url,
        "SELECT kind, starts_on, starts_at FROM tutor_availability_exceptions",
    )
    anchor = date(2026, 11, 19)
    past_off = [
        e for e in exceptions if e.kind == "unavailable" and e.starts_on < anchor
    ]
    assert len(past_off) >= 4 and any(e.starts_at is not None for e in past_off)
    assert any(e.kind == "available" and e.starts_on < anchor for e in exceptions)
    assert {e.kind for e in exceptions if e.starts_on >= anchor} == {
        "unavailable",
        "available",
    }
    preferred = rows(
        url,
        "SELECT p.tutor_user_id, c.name FROM tutor_preferred_classrooms p"
        " JOIN classrooms c ON c.id = p.classroom_id",
    )
    assert len({r.tutor_user_id for r in preferred}) >= 6
    assert all(r.name != "Aula 3" for r in preferred)
    plan = plan_simulation("es", "full", 1)
    tutor12 = plan.tutor("t12").account.username
    assert scalar(
        url,
        "SELECT t.used_at FROM account_tokens t JOIN users u ON u.id = t.user_id"
        " WHERE u.username = :name",
        name=tutor12,
    ) == local(date(2026, 10, 1), "09:00")
    assert (
        scalar(
            url,
            "SELECT count(*) FROM tutor_profiles p JOIN users u ON u.id = p.user_id"
            " WHERE u.username = :name",
            name=tutor12,
        )
        == 1
    )


def test_delegations_and_registrations(es_url):
    current = rows(
        es_url,
        "SELECT u.username, p.code FROM user_delegations d"
        " JOIN users u ON u.id = d.user_id"
        " JOIN permissions p ON p.id = d.permission_id",
    )
    assert [tuple(r) for r in current] == [("administracion", "finance.refund.approve")]
    ended = rows(
        es_url,
        "SELECT action FROM audit_events WHERE record_type = 'user_delegations'"
        " ORDER BY action",
    )
    assert [r.action for r in ended] == ["create", "create", "delete"]
    requests = rows(
        es_url,
        "SELECT r.status AS request, a.status AS approval FROM registration_requests r"
        " JOIN approval_requests a ON a.subject_id = r.id ORDER BY r.status",
    )
    assert [tuple(r) for r in requests] == [
        ("approved", "approved"),
        ("pending", "pending"),
    ]


def test_writes_went_through_the_services_with_their_actors(es_url):
    plan = plan_simulation("es", "small", 1)
    ids = {r.username: r.id for r in rows(es_url, "SELECT id, username FROM users")}
    admin, owner = ids["administracion"], ids["direccion"]

    def actors(table: str) -> set[Any]:
        return {
            r.actor_user_id
            for r in rows(
                es_url,
                "SELECT actor_user_id FROM audit_events"
                " WHERE record_type = :table AND action = 'create'",
                table=table,
            )
        }

    for table in (
        "families",
        "guardians",
        "students",
        "student_guardians",
        "family_students",
        "family_guardians",
        "student_emergency_contacts",
        "subjects",
        "classrooms",
        "closures",
        "academic_periods",
        "tutor_availability_exceptions",
    ):
        assert actors(table) == {admin}, table
    for table in (
        "tutor_profiles",
        "tutor_availability_settings",
        "scheduling_policies",
    ):
        assert actors(table) == {owner}, table
    tutors = {ids[t.account.username] for t in plan.tutors}
    assert actors("tutor_qualifications") == tutors
    guardians = {
        ids[g.account.username] for g in plan.guardians if g.account is not None
    }
    assert actors("consent_records") <= guardians | {admin}
    every = {
        r.actor_user_id for r in rows(es_url, "SELECT actor_user_id FROM audit_events")
    }
    assert every <= set(ids.values()) | {None}
    for table in ("students", "guardians", "families", "tutor_profiles", "subjects"):
        created = scalar(
            es_url,
            "SELECT count(*) FROM audit_events WHERE record_type = :t"
            " AND action = 'create'",
            t=table,
        )
        assert created == scalar(es_url, f"SELECT count(*) FROM {table}")  # noqa: S608


def test_no_background_job_is_queued(es_url, full_url):
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
    assert scalar(full_url, "SELECT count(*) FROM procrastinate_jobs") == 0


def test_the_full_run_prints_its_pinned_totals(full_url):
    assert FULL_RUN["out"] == first_run_lines("full")


# --- HF-01g: realistic margins on the full run (B1-B3, B5) ----------------------------

TERM = {"from": "2026-09-01", "to": "2026-11-18"}
MONTHS = (
    ("2026-09-01", "2026-09-30"),
    ("2026-10-01", "2026-10-31"),
    ("2026-11-01", "2026-11-18"),
)


def _report(client: Any, path: str, **params: str) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _figures(row: dict[str, Any]) -> dict[str, Decimal]:
    return {
        key: Decimal(row[key])
        for key in (
            "revenue_total",
            "direct_tutor_cost_total",
            "gross_profit",
            "operating_expenses_total",
            "net_result",
        )
    }


def test_the_full_term_shows_a_plausible_profit(full_url):
    """B1, B2 and B5's identities: K1 per month as the Owner, both bases."""
    owner = plan_simulation("es", "full", 1).owner
    with frozen_clock(FROZEN_NOW), auth_client(full_url) as client:
        assert login(client, owner.username, owner.password).status_code == 200
        trends = {
            basis: _report(
                client, "/api/v1/reports/kpis/revenue-trend", basis=basis, **TERM
            )
            for basis in ("accrual", "cash")
        }
    for basis, trend in trends.items():
        months = [_figures(m) for m in trend["months"]]
        assert [m["month"] for m in trend["months"]] == [
            "2026-09",
            "2026-10",
            "2026-11",
        ]
        for m in [*months, _figures(trend["totals"])]:
            assert (
                m["gross_profit"] == m["revenue_total"] - m["direct_tutor_cost_total"]
            )
            assert m["net_result"] == m["gross_profit"] - m["operating_expenses_total"]
        for september_or_october in months[:2]:
            margin = (
                september_or_october["net_result"]
                / september_or_october["revenue_total"]
            )
            if basis == "accrual":
                share = (
                    september_or_october["direct_tutor_cost_total"]
                    / september_or_october["revenue_total"]
                )
                assert Decimal("0.20") <= margin <= Decimal("0.40"), (basis, margin)
                assert Decimal("0.40") <= share <= Decimal("0.60"), (basis, share)
            else:
                assert Decimal("0.15") <= margin <= Decimal("0.45"), (basis, margin)
        november = months[2]
        assert november["net_result"] > 0, basis
        assert november["gross_profit"] > 0, basis
        assert _figures(trend["totals"])["net_result"] > 0, basis
        if basis == "accrual":
            assert (
                sum(m["revenue_total"] for m in months)
                == _figures(trend["totals"])["revenue_total"]
            )


def test_group_revenue_falls_in_the_months_the_groups_meet(full_url):
    """B3: the accrual ``group`` row earns in every month; linked plans post
    on the 1st; only the last group has flat fees."""
    plan = plan_simulation("es", "full", 1)
    owner = plan.owner
    with frozen_clock(FROZEN_NOW), auth_client(full_url) as client:
        assert login(client, owner.username, owner.password).status_code == 200
        for start, end in MONTHS:
            report = _report(
                client,
                "/api/v1/reports/finance/profitability",
                basis="accrual",
                **{"from": start, "to": end},
            )
            [group] = [r for r in report["class_rows"] if r["class_type"] == "group"]
            assert Decimal(group["revenue"]) > 0, start
            assert Decimal(group["gross_profit"]) > 0, start
    dates = rows(
        full_url,
        "SELECT DISTINCT c.service_date FROM charges c JOIN tuition_plans t"
        " ON t.id = c.tuition_plan_id WHERE t.group_enrollment_id IS NOT NULL",
    )
    assert {d for (d,) in dates} == {
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    }
    fees = dict(
        rows(
            full_url,
            "SELECT g.name, count(c.id) FROM class_groups g"
            " JOIN group_enrollments e ON e.group_id = g.id"
            " LEFT JOIN charges c ON c.group_enrollment_id = e.id"
            " AND c.kind = 'group_enrollment' GROUP BY g.name",
        )
    )
    *linked_groups, last = plan.timetable.groups
    assert fees == {
        **{g.name: 0 for g in linked_groups},
        last.name: len(last.series.students),
    }
