"""The simulated schedule and courses against PostgreSQL (#550 HF-01b: B,
C). One ``small`` Spanish run (shared, read only) with the real clock
frozen at :data:`~tests.integration.simulation_support.FROZEN_NOW`; the
HF-01-only rerun (B8) uses its own database (B7 is #543 C4's test, in
``test_simulation_seed.py``); the one ``full`` Spanish run checks the full
minimums (B5). The worker parity test (B9) runs last on the shared
database."""

import asyncio
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.modules.scheduling import tasks as scheduling_tasks
from app.modules.staff import service as staff
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_NOW,
    ZONE,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.texts import POOLS
from tests.factories.simulation.timeline import SEEDED_EARLIER
from tests.factories.simulation.timetable import PENDING_DAYS
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.simulation_support import (
    SERVICE_CANCELLATION_REASONS,
    first_run_lines,
    free_text,
    migrate,
    run_command,
    simulation_env,
    table_counts,
)

ACCENTED = set("áéíóúñ¿¡")
ANCHOR_START = local_instant(SIM_ANCHOR, "00:00")
"""The first instant of the anchor day (local midnight)."""
OCCUPYING = ("offered", "confirmed", "completed")


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


SMALL_RUN: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def es_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, out, err = run_command(url, "es")
        assert code == 0, err
        SMALL_RUN["out"] = out
        yield url


@pytest.fixture(scope="module")
def full_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, out, err = run_command(url, "es", profile="full")
        assert code == 0, err
        yield url


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    return run_sql(url, sql, params or None)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return rows(url, sql, **params)[0][0]


def states(url: str, where: str, **params: Any) -> Counter[str]:
    return Counter(
        dict(
            rows(
                url,
                f"SELECT state, count(*) FROM class_sessions WHERE {where}"  # noqa: S608
                " GROUP BY state",
                **params,
            )
        )
    )


# --- B1-B3: history before the anchor, schedule after ---------------------------------


def test_the_run_prints_the_schedule_and_courses_totals(es_url):
    assert SMALL_RUN["out"] == first_run_lines("small")


def test_every_class_before_the_anchor_is_in_a_final_state(es_url):
    found = states(es_url, "starts_at < :anchor", anchor=ANCHOR_START)

    assert set(found) <= {"completed", "cancelled", "skipped_closure"}
    assert found["completed"] > 100
    moved = scalar(
        es_url,
        "SELECT count(*) FROM session_change_requests WHERE kind = 'reschedule'"
        " AND status = 'approved'",
    )
    assert moved >= 1
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM class_sessions s JOIN session_change_requests r"
            " ON r.class_session_id = s.id WHERE r.kind = 'reschedule'"
            " AND r.status = 'approved' AND (s.state <> 'completed'"
            " OR s.starts_at <> r.proposed_starts_at)",
        )
        == 0
    )


def test_every_completed_class_has_attendance_and_an_approved_closeout(es_url):
    missing = rows(
        es_url,
        "SELECT s.id FROM class_sessions s JOIN class_session_participants p"
        " ON p.class_session_id = s.id LEFT JOIN class_session_attendance a"
        " ON a.class_session_id = s.id AND a.student_id = p.student_id"
        " WHERE s.state = 'completed' AND a.id IS NULL",
    )
    assert missing == []
    closeouts = rows(
        es_url,
        "SELECT s.ends_at, c.status, c.submitted_at, c.last_submitted_at,"
        " max(ap.approved_at) AS approved_at FROM class_sessions s"
        " JOIN session_closeouts c ON c.class_session_id = s.id"
        " LEFT JOIN session_closeout_approvals ap ON ap.closeout_id = c.id"
        " WHERE s.state = 'completed'"
        " GROUP BY s.id, s.ends_at, c.status, c.submitted_at, c.last_submitted_at",
    )
    completed = scalar(
        es_url, "SELECT count(*) FROM class_sessions WHERE state = 'completed'"
    )
    assert len(closeouts) == completed
    for row in closeouts:
        assert row.status == "approved"
        assert row.ends_at < row.submitted_at <= row.last_submitted_at
        assert row.last_submitted_at < row.approved_at < SIM_NOW
    assert (
        scalar(es_url, "SELECT max(marked_at) FROM class_session_attendance") < SIM_NOW
    )


def test_nothing_from_the_anchor_on_is_held_or_corrected(es_url):
    found = states(es_url, "starts_at >= :anchor", anchor=ANCHOR_START)

    assert set(found) <= {"confirmed", "offered", "unassigned"}
    assert found["offered"] >= 3 and found["unassigned"] >= 2
    for table in ("class_session_attendance", "session_closeouts"):
        assert (
            scalar(
                es_url,
                f"SELECT count(*) FROM {table} t JOIN class_sessions s"  # noqa: S608
                " ON s.id = t.class_session_id WHERE s.starts_at >= :anchor",
                anchor=ANCHOR_START,
            )
            == 0
        ), table
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM class_session_attendance_corrections c"
            " JOIN class_session_attendance a ON a.id = c.attendance_id"
            " JOIN class_sessions s ON s.id = a.class_session_id"
            " WHERE s.starts_at >= :anchor",
            anchor=ANCHOR_START,
        )
        == 0
    )


# --- B4: the database re-checks the consistency MUST ----------------------------------


def test_tutors_are_qualified_and_available_and_nothing_overlaps(es_url):
    occupied = rows(
        es_url,
        "SELECT s.id, s.tutor_user_id, s.subject_id, s.starts_at, s.ends_at"
        " FROM class_sessions s WHERE s.state IN ('offered', 'confirmed', 'completed')"
        " AND s.tutor_user_id IS NOT NULL",
    )
    qualifications = {
        (q.tutor_user_id, q.subject_id): q
        for q in rows(
            es_url,
            "SELECT tutor_user_id, subject_id, status, decided_at, revoked_at"
            " FROM tutor_qualifications",
        )
    }
    for s in occupied:
        q = qualifications.get((s.tutor_user_id, s.subject_id))
        assert q is not None, s.id
        assert q.status == "approved" or (
            q.status == "revoked" and q.revoked_at > s.starts_at
        ), s.id

    async def unavailable() -> list[Any]:
        from sqlalchemy.ext.asyncio import AsyncSession

        engine = short_lived_engine(es_url)
        bad = []
        try:
            async with AsyncSession(engine) as session:
                for s in occupied:
                    found = await staff.is_tutor_available_at(
                        session, s.tutor_user_id, s.starts_at, s.ends_at
                    )
                    if not found.available:
                        bad.append(s.id)
        finally:
            await engine.dispose()
        return bad

    assert asyncio.run(unavailable()) == []

    for column in ("tutor_user_id", "classroom_id"):
        assert (
            scalar(
                es_url,
                "SELECT count(*) FROM class_sessions a JOIN class_sessions b"  # noqa: S608
                f" ON a.{column} = b.{column} AND a.id < b.id"
                " AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at"
                " WHERE a.state IN ('offered', 'confirmed', 'completed')"
                " AND b.state IN ('offered', 'confirmed', 'completed')",
            )
            == 0
        ), column
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM class_sessions s JOIN classrooms r"
            " ON r.id = s.classroom_id WHERE NOT r.is_active"
            " AND s.state IN ('offered', 'confirmed', 'completed')",
        )
        == 0
    )
    closures = {row[0] for row in rows(es_url, "SELECT starts_on FROM closures")}
    held_on_closures = rows(
        es_url,
        "SELECT (starts_at AT TIME ZONE 'America/Mexico_City')::date AS day, state"
        " FROM class_sessions",
    )
    for row in held_on_closures:
        if row.day in closures:
            assert row.state in ("skipped_closure", "cancelled"), row


# --- B5, B6: the counts and the pending items -----------------------------------------


def minimums(url: str, profile: str) -> None:
    subs, makeups, groups = (8, 6, 3) if profile == "full" else (2, 2, 1)
    assert scalar(url, "SELECT max(until_on) FROM class_series") <= SIM_HORIZON
    assert scalar(url, "SELECT count(*) FROM class_groups") == groups
    cohorts = rows(
        url,
        "SELECT c.name, min(s.starts_at) AS first, max(s.starts_at) AS last"
        " FROM course_cohorts c JOIN class_sessions s ON s.cohort_id = c.id"
        " GROUP BY c.name",
    )
    assert len(cohorts) == 2
    assert sum(c.last < ANCHOR_START for c in cohorts) == 1
    assert sum(c.first < ANCHOR_START < c.last for c in cohorts) == 1
    assert (
        scalar(
            url,
            "SELECT count(*) FROM class_session_substitutions u"
            " JOIN assignment_offers o ON o.id = u.offer_id"
            " JOIN class_sessions s ON s.id = u.class_session_id"
            " WHERE o.status = 'accepted' AND s.state = 'completed'",
        )
        >= subs
    )
    assert (
        scalar(
            url,
            "SELECT count(*) FROM class_sessions WHERE makeup_of_session_id IS NOT NULL"
            " AND state = 'completed'",
        )
        >= makeups
    )
    requests = Counter(
        dict(rows(url, "SELECT status, count(*) FROM session_requests GROUP BY status"))
    )
    assert requests["confirmed"] >= 8 and requests["rejected"] >= 2
    assert requests["pending"] >= 3
    changes = Counter(
        dict(
            rows(
                url,
                "SELECT status, count(*) FROM session_change_requests GROUP BY status",
            )
        )
    )
    assert changes["rejected"] >= 2 and changes["pending"] >= 3
    offers = Counter(
        dict(
            rows(url, "SELECT status, count(*) FROM assignment_offers GROUP BY status")
        )
    )
    assert offers["declined"] >= 3 and offers["expired"] >= 2 and offers["pending"] >= 3
    assert offers["accepted"] > offers["declined"]
    availability = Counter(
        dict(
            rows(
                url,
                "SELECT status, count(*) FROM tutor_availability_change_requests"
                " GROUP BY status",
            )
        )
    )
    assert availability["approved"] >= 2 and availability["rejected"] >= 1
    assert availability["pending"] >= 2
    assert scalar(url, "SELECT count(*) FROM class_session_attendance_corrections") >= 3
    assert scalar(url, "SELECT count(*) FROM session_closeout_corrections") >= 2
    assert (
        scalar(
            url,
            "SELECT count(*) FROM class_session_reassignment_flags f"
            " JOIN class_sessions s ON s.id = f.class_session_id"
            " WHERE s.starts_at > :now AND s.state IN"
            " ('unassigned', 'offered', 'confirmed')"
            " AND (s.tutor_user_id IS NULL OR s.tutor_user_id = f.tutor_user_id)",
            now=SIM_NOW,
        )
        == 0
    ), "every reassignment flag is resolved"


def test_the_small_counts_and_minimums(es_url):
    minimums(es_url, "small")


def test_the_full_counts_and_minimums(full_url):
    minimums(full_url, "full")
    found = states(full_url, "starts_at < :anchor", anchor=ANCHOR_START)
    assert set(found) <= {"completed", "cancelled", "skipped_closure"}
    classes = Counter(
        dict(
            rows(
                full_url,
                "SELECT final_classification, count(*) FROM session_change_requests"
                " WHERE kind = 'cancellation' AND status = 'approved' GROUP BY 1",
            )
        )
    )
    assert classes["on_time"] >= 2 and classes["late"] >= 2 and classes["excused"] >= 2


def test_pending_items_were_made_on_17_or_18_november_and_none_is_expired(es_url):
    changes = rows(
        es_url,
        "SELECT created_at FROM session_change_requests WHERE status = 'pending'",
    )
    offers = rows(
        es_url,
        "SELECT offered_at, respond_by FROM assignment_offers WHERE status = 'pending'",
    )
    assert all(at.astimezone(ZONE).date() in PENDING_DAYS for (at,) in changes)
    assert all(o.offered_at.astimezone(ZONE).date() in PENDING_DAYS for o in offers)
    assert all(o.respond_by > SIM_NOW for o in offers)
    # Session and availability requests store no business instant (only the
    # database-set created_at, E7): their dates are the plan's (unit test).
    assert (
        scalar(es_url, "SELECT count(*) FROM session_requests WHERE status = 'pending'")
        >= 3
    )
    assert (
        scalar(
            es_url,
            "SELECT min(starts_at) FROM session_requests WHERE status = 'pending'",
        )
        > SIM_NOW
    )


# --- B7, B8: the run date and reruns --------------------------------------------------

# B7 (run-date independence) is #543 C4's test, whose canonical dump now
# covers this issue's tables (test_simulation_seed.py).


def test_a_rerun_writes_nothing(es_url):
    before = table_counts(es_url)

    code, out, _ = run_command(es_url, "es")

    assert code == 0
    assert out[-8:-2] == [
        "schedule: 0 rows",
        "courses: 0 rows",
        "academics: 0 rows",
        "billing: 0 rows",
        "payroll: 0 rows",
        "finance: 0 rows",
    ]
    assert table_counts(es_url) == before


def test_a_database_seeded_by_hf01_only_skips_the_schedule(database_url):
    for url in _fresh(database_url):
        assert run_command(url, "es", modules=SIMULATION_MODULES[:4])[0] == 0
        before = table_counts(url)

        code, out, _ = run_command(url, "es")

        assert code == 0
        assert out[-8:-2] == [
            f"schedule: skipped ({SEEDED_EARLIER})",
            f"courses: skipped ({SEEDED_EARLIER})",
            f"academics: skipped ({SEEDED_EARLIER})",
            f"billing: skipped ({SEEDED_EARLIER})",
            f"payroll: skipped ({SEEDED_EARLIER})",
            f"finance: skipped ({SEEDED_EARLIER})",
        ]
        assert table_counts(url) == before


# --- B10: language --------------------------------------------------------------------


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


def test_free_text_comes_from_the_spanish_pool(es_url):
    pool = pool_values("es")
    found = free_text(es_url)

    assert {column for column, _ in found} >= {
        "session_change_requests.note",
        "session_requests.note",
        "session_closeouts.topics",
        "course_template_modules.title",
        "class_groups.name",
    }
    # Service-set cancellation reasons are codes; the center's are the pool's.
    # English (and its accent check) is #543 D4's test, which reads these
    # columns too (test_simulation_seed.py).
    assert [
        (column, value)
        for column, value in found
        if value not in pool and value not in SERVICE_CANCELLATION_REASONS
    ] == []


# --- C: per account, read-only through the API ----------------------------------------


def _get(client: Any, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_tutor_sees_only_their_classes_and_their_pending_offers(es_url):
    plan = plan_simulation("es", "small", 1)
    t = plan.timetable
    offered = next(c for c in t.classes if c.status == "offered")
    tutor = plan.tutor(offered.placed.tutor or "")
    user_id = scalar(
        es_url, "SELECT id FROM users WHERE username = :u", u=tutor.account.username
    )
    with auth_client(es_url) as client:
        assert (
            login(client, tutor.account.username, tutor.account.password).status_code
            == 200
        )
        offers = _get(client, "/api/v1/assignment-offers", status="pending")
        mine = scalar(
            es_url,
            "SELECT count(*) FROM assignment_offers WHERE status = 'pending'"
            " AND tutor_user_id = :t",
            t=user_id,
        )
        assert offers["total"] == mine >= 1
        schedule = _get(
            client,
            f"/api/v1/tutors/{user_id}/schedule",
            **{"from": "2026-11-16T06:00:00Z", "to": "2026-11-30T06:00:00Z"},
        )
        ids = [item["id"] for item in schedule["items"]]
        assert ids
        theirs = {
            row[0]
            for row in rows(
                es_url,
                "SELECT id::text FROM class_sessions WHERE tutor_user_id = :t",
                t=user_id,
            )
        }
        assert set(ids) <= theirs


def test_a_guardian_sees_only_the_familys_classes_and_requests(es_url):
    plan = plan_simulation("es", "small", 1)
    family = next(
        f for f in plan.families if f.guardians and f.guardians[0].account is not None
    )
    guardian = family.guardians[0].account
    assert guardian is not None
    own = {s.key for s in family.students}
    student = family.students[0]
    student_id = scalar(
        es_url,
        "SELECT s.id FROM students s WHERE s.given_names = :g AND s.family_names = :f",
        g=student.given_names,
        f=student.family_names,
    )
    other = next(s for s in plan.students if s.key not in own)
    other_id = scalar(
        es_url,
        "SELECT s.id FROM students s WHERE s.given_names = :g AND s.family_names = :f",
        g=other.given_names,
        f=other.family_names,
    )
    window = {"from": "2026-11-02T06:00:00Z", "to": "2026-11-30T06:00:00Z"}
    with auth_client(es_url) as client:
        assert login(client, guardian.username, guardian.password).status_code == 200
        schedule = _get(client, f"/api/v1/students/{student_id}/schedule", **window)
        assert schedule["items"]
        assert (
            client.get(
                f"/api/v1/students/{other_id}/schedule", params=window
            ).status_code
            == 404
        )
        requests = _get(client, "/api/v1/session-requests")
        visible = {item["student"]["id"] for item in requests["items"]}
        family_ids = {
            str(row[0])
            for row in rows(
                es_url,
                "SELECT fs.student_id FROM family_students fs JOIN families f"
                " ON f.id = fs.family_id WHERE f.name = :n",
                n=family.name,
            )
        }
        assert visible <= family_ids


def test_the_administrators_approvals_list_the_pending_requests_only(es_url):
    plan = plan_simulation("es", "small", 1)
    admin = plan.administrator
    pending = Counter(
        dict(
            rows(
                es_url,
                "SELECT request_type, count(*) FROM approval_requests"
                " WHERE status = 'pending' GROUP BY 1",
            )
        )
    )
    with auth_client(es_url) as client:
        assert login(client, admin.username, admin.password).status_code == 200
        page = _get(client, "/api/v1/approval-requests", status="pending", limit=100)
        listed = Counter(item["request_type"] for item in page["items"])
        decided = _get(
            client, "/api/v1/approval-requests", status="approved", limit=100
        )
    assert (
        listed["scheduling.change_request"] == pending["scheduling.change_request"] >= 3
    )
    assert (
        listed["scheduling.session_request"]
        == pending["scheduling.session_request"]
        >= 3
    )
    assert (
        listed["staff.availability_change"] == pending["staff.availability_change"] >= 2
    )
    assert all(item["status"] == "pending" for item in page["items"])
    assert all(item["status"] == "approved" for item in decided["items"])


# --- B9: worker parity at the anchor (last: it would change the shared database) ------


def test_zz_the_worker_has_nothing_to_do_at_the_anchor(es_url):
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
    before = table_counts(es_url)

    with simulation_env(es_url), sim_clock(SIM_NOW):
        settings = get_settings()
        materialized = asyncio.run(
            scheduling_tasks.run_materialization(settings, now=SIM_NOW)
        )
        expired = asyncio.run(scheduling_tasks.run_expire_offers(settings))

    assert (materialized.created, materialized.conflicts) == (0, 0)
    assert expired.expired == 0
    assert table_counts(es_url) == before
