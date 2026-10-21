"""The nightly series materialization (#53 J2; ADR-0005 D4, D6).

``run_materialization`` runs against the module database with an explicit
``now`` (the clock is frozen too, for the booking notice inside the checks).
Every series of the module is extended on every run, so the assertions are
about each test's own series; each test takes its own decade.
"""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.modules.scheduling import materialize as materialize_module
from app.modules.scheduling import tasks
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_support import (
    Timeline,
    create_series,
    owner_cookie,
    series_body,
    series_rows,
    timeline,
)
from tests.integration.test_class_session_create import (
    CREATED,
    close_center,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

APPROVALS = (
    "SELECT id, status, requester_user_id, subject_id FROM approval_requests"
    " WHERE request_type = 'scheduling.occurrence_conflict' AND subject_id = ANY(:ids)"
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def run(url: str, tl_now) -> tasks.MaterializationResult:
    with frozen_clock(tl_now):
        return asyncio.run(tasks.run_materialization(settings_for(url), now=tl_now))


def make_series(
    url: str, client, tl: Timeline, *, room: bool = False, **changes: Any
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A Monday/Wednesday 16:00 series created at ``tl.now``."""
    subject = new_subject(url)
    refs = {
        "student": new_student(url),
        "subject": subject,
        "tutor": new_tutor(url, qualified_for=[subject]),
    }
    if room:
        refs["room"] = new_room(url)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_series(
            client,
            cookie,
            series_body(
                student=refs["student"],
                subject=refs["subject"],
                tutor=refs["tutor"],
                room=refs.get("room"),
                starts_on=tl.monday,
                **changes,
            ),
        )
    assert code_of(response) == CREATED, response.text
    return uuid.UUID(response.json()["id"]), refs


def audit_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    return int(row.n)


def test_a_rerun_creates_nothing(module_seeded_url, client):
    tl = timeline()
    series_id, _ = make_series(module_seeded_url, client, tl)
    later = tl.now + timedelta(weeks=1)

    first = run(module_seeded_url, later)
    rows = len(series_rows(module_seeded_url, series_id))
    audits = audit_count(module_seeded_url)
    second = run(module_seeded_url, later)

    assert first.created >= 2
    assert (second.created, second.skipped_closure, second.conflicts) == (0, 0, 0)
    assert second.failed == 0
    assert len(series_rows(module_seeded_url, series_id)) == rows == 26
    assert audit_count(module_seeded_url) == audits


def test_the_horizon_advances_one_week_at_a_time(module_seeded_url, client):
    tl = timeline()
    series_id, _ = make_series(module_seeded_url, client, tl)
    before = series_rows(module_seeded_url, series_id)

    run(module_seeded_url, tl.now + timedelta(weeks=1))
    after = series_rows(module_seeded_url, series_id)

    assert len(before) == 24
    new = [r for r in after if r.id not in {b.id for b in before}]
    assert [r.starts_at for r in new] == [
        tl.at(tl.week(13), "16:00"),
        tl.at(tl.week(13, 3), "16:00"),
    ]
    assert {r.state for r in new} == {"offered"}
    # Nothing earlier was touched.
    assert [(r.id, r.state) for r in after if r.id in {b.id for b in before}] == [
        (b.id, b.state) for b in before
    ]


def test_a_closure_on_a_future_date_is_skipped(module_seeded_url, client):
    tl = timeline()
    series_id, _ = make_series(module_seeded_url, client, tl)
    close_center(module_seeded_url, tl.week(13, 3))

    result = run(module_seeded_url, tl.now + timedelta(weeks=1))

    rows = {r.starts_at: r for r in series_rows(module_seeded_url, series_id)}
    assert rows[tl.at(tl.week(13, 3), "16:00")].state == "skipped_closure"
    assert rows[tl.at(tl.week(13), "16:00")].state == "offered"
    assert result.skipped_closure >= 1


def test_a_conflict_is_stored_with_an_inbox_item(module_seeded_url, client):
    tl = timeline()
    series_id, refs = make_series(module_seeded_url, client, tl, room=True)
    existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=refs["tutor"],
        student_id=new_student(module_seeded_url),
        starts_at=tl.at(tl.week(13), "16:00"),
    )
    week13 = tl.at(tl.week(13), "16:00")

    run(module_seeded_url, tl.now + timedelta(weeks=1))
    run(module_seeded_url, tl.now + timedelta(weeks=1))

    rows = [
        r for r in series_rows(module_seeded_url, series_id) if r.starts_at == week13
    ]
    [row] = rows
    assert row.state == "conflict"
    assert list(row.conflict_codes) == ["tutor_overlap"]
    assert row.tutor_user_id == refs["tutor"]
    assert row.classroom_id == refs["room"]
    [request] = run_sql(module_seeded_url, APPROVALS, {"ids": [row.id]})
    assert request.status == "pending"
    assert request.requester_user_id is None
    # It holds nothing: no room-occupying row in its room at that time.
    [held] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM class_sessions WHERE classroom_id = :room"
        " AND state IN ('unassigned', 'offered', 'confirmed', 'completed')"
        " AND starts_at = :at",
        {"room": str(refs["room"]), "at": week13},
    )
    assert held.n == 0
    # The system raised it: the audit actor is NULL.
    [created] = [
        e
        for e in events(module_seeded_url, record_type="class_sessions")
        if e.record_id == row.id
    ]
    assert created.action == "create" and created.actor_user_id is None


def test_an_inactive_tutor_makes_new_rows_conflicts(module_seeded_url, client):
    tl = timeline()
    series_id, refs = make_series(module_seeded_url, client, tl)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'suspended' WHERE id = :id",
        {"id": str(refs["tutor"])},
    )

    run(module_seeded_url, tl.now + timedelta(weeks=1))

    new = [
        r
        for r in series_rows(module_seeded_url, series_id)
        if r.starts_at >= tl.at(tl.week(13), "00:00")
    ]
    assert len(new) == 2
    assert {r.state for r in new} == {"conflict"}
    assert {tuple(r.conflict_codes) for r in new} == {("tutor_inactive",)}
    requests = run_sql(module_seeded_url, APPROVALS, {"ids": [r.id for r in new]})
    assert len(requests) == 2


def test_a_finished_series_is_not_selected(module_seeded_url, client):
    tl = timeline()
    finished, _ = make_series(module_seeded_url, client, tl, count=2)
    open_ended, _ = make_series(module_seeded_url, client, tl)
    later = tl.now + timedelta(weeks=3)

    async def _selected() -> list[uuid.UUID]:
        from app.core.db import create_sessionmaker
        from app.core.jobs import create_worker_engine
        from app.core.uow import UnitOfWork

        engine = create_worker_engine(settings_for(module_seeded_url))
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                return await tasks.series_to_materialize(uow.session, later)
        finally:
            await engine.dispose()

    selected = asyncio.run(_selected())

    assert finished not in selected
    assert open_ended in selected


def test_one_failing_series_does_not_block_the_others(
    module_seeded_url, client, monkeypatch
):
    tl = timeline()
    failing, failing_refs = make_series(module_seeded_url, client, tl)
    healthy, _ = make_series(module_seeded_url, client, tl)
    original = materialize_module.collect_conflicts

    async def _collect(session, candidate, **kwargs):
        if candidate.tutor_user_id == failing_refs["tutor"]:
            raise RuntimeError("boom")
        return await original(session, candidate, **kwargs)

    monkeypatch.setattr(materialize_module, "collect_conflicts", _collect)

    result = run(module_seeded_url, tl.now + timedelta(weeks=1))

    assert result.failed == 1
    assert len(series_rows(module_seeded_url, failing)) == 24
    assert len(series_rows(module_seeded_url, healthy)) == 26


def test_the_job_logs_counts_only(module_seeded_url, client, caplog):
    tl = timeline()
    make_series(module_seeded_url, client, tl)

    with caplog.at_level("INFO", logger=tasks.__name__):
        result = run(module_seeded_url, tl.now + timedelta(weeks=1))

    [record] = [r for r in caplog.records if r.message == "Materialized class series"]
    assert {
        key: getattr(record, f"{key}_count")
        for key in ("series", "created", "skipped_closure", "conflict", "failed")
    } == {
        "series": result.series,
        "created": result.created,
        "skipped_closure": result.skipped_closure,
        "conflict": result.conflicts,
        "failed": result.failed,
    }


def test_existing_rows_are_never_updated(module_seeded_url, client):
    tl = timeline()
    series_id, _ = make_series(module_seeded_url, client, tl)
    # A closure added after the rows exist changes nothing here (#232's).
    close_center(module_seeded_url, tl.week(2))
    before = [(r.id, r.state) for r in series_rows(module_seeded_url, series_id)]

    run(module_seeded_url, tl.now + timedelta(days=1))

    after = [(r.id, r.state) for r in series_rows(module_seeded_url, series_id)]
    assert after == before
    assert "skipped_closure" not in {state for _, state in after}
