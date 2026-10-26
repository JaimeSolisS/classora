"""The progress report placeholder job (#316 D, F3) with a frozen ``now``.

``run_progress_report_placeholders`` runs against the module database; every
due schedule of the module is filled on every run, so the assertions are
about each test's own schedules, reports and notifications.

Weekly and monthly placeholders for the right center-local period (Mexico
City, a Sunday night; Tijuana on the DST change), with ``schedule_id``,
``draft`` and no narrative; the author reads it (R2, R3) and a reviewer gets
``404`` on R3; the ``due`` notification to the author only, with the exact
params; the audit event without actor; every skip (inactive, not yet due,
each eligibility reason) creates nothing and notifies nobody; a second run
nothing; a deleted or retracted placeholder is never created again; a report
the tutor wrote skips the period, a retracted one doesn't; after a gap only
the latest period is filled; a failing schedule doesn't block the others;
the statement budgets.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import ACADEMIC_COORDINATOR
from app.workflows import progress_report_schedule_tasks as tasks
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.report_schedule_support import (
    FEBRUARY,
    LAST_MONDAY,
    MONDAY,
    NEXT_MONDAY_RUN,
    NOW,
    due_notifications,
    insert_report,
    insert_schedule,
    placeholders,
    set_zone,
    teaching,
)
from tests.integration.test_class_session_create import new_subject

DUE_KEYS = {"student_id", "report_id", "kind", "period_start", "period_end"}


@pytest.fixture(scope="module")
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Any:
    frozen.set(NOW)
    yield frozen
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def run(url: str, frozen: FrozenClock, now: datetime) -> tasks.PlaceholderResult:
    frozen.set(now)
    return asyncio.run(
        tasks.run_progress_report_placeholders(settings_for(url), now=now)
    )


def notified(url: str, user: CreatedUser) -> list[Any]:
    return run_sql(
        url,
        "SELECT params FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'academics.progress_report_due'",
        {"u": user.id},
    )


def scheduled(
    url: str, **values: Any
) -> tuple[CreatedUser, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A tutor teaching a new student in a new subject, with a schedule
    (weekly from 3 March by default): ``(tutor, student, subject, schedule)``."""
    tutor, student, subject = teaching(url)
    values.setdefault("starts_on", MONDAY)
    schedule = insert_schedule(url, student=student, author=tutor.id, **values)
    return tutor, student, subject, schedule


# --- What a run creates ---------------------------------------------------------------


def test_a_weekly_placeholder_for_the_week_that_ended(
    module_seeded_url, client, frozen
):
    url = module_seeded_url
    tutor, student, subject, schedule = scheduled(url, subject=None)
    coordinator = create_user(url, roles=(ACADEMIC_COORDINATOR,))

    result = run(url, frozen, NEXT_MONDAY_RUN)

    assert result.created >= 1
    assert result.failed == 0
    [made] = placeholders(url, schedule)
    assert (made.student_id, made.author_user_id, made.subject_id) == (
        student,
        tutor.id,
        None,
    )
    assert (made.kind, made.period_start, made.period_end) == (
        "weekly",
        MONDAY,
        date(2031, 3, 9),
    )
    assert made.status == "draft"
    assert (made.summary, made.strengths, made.areas_for_improvement) == (
        None,
        None,
        None,
    )
    assert (made.next_steps, made.facts, made.deleted_at) == (None, None, None)
    # The due notification: the author only, ids, kind and period only.
    [due] = due_notifications(url, [made.id])
    assert due.recipient_user_id == tutor.id
    assert set(due.params) == DUE_KEYS
    assert due.params == {
        "student_id": str(student),
        "report_id": str(made.id),
        "kind": "weekly",
        "period_start": "2031-03-03",
        "period_end": "2031-03-09",
    }
    # The audit event has no actor.
    [event] = events(
        url, action="academics.progress_report_placeholder_created", record_id=made.id
    )
    assert event.actor_user_id is None
    assert event.record_type == "progress_reports"
    assert event.details == {
        "report_id": str(made.id),
        "schedule_id": str(schedule),
        "period_start": "2031-03-03",
    }
    # The author reads it in R2 and R3 (an ordinary draft); a reviewer can't.
    cookie = sign_in(client, tutor.username)
    view = get(client, cookie, f"/api/v1/progress-reports/{made.id}")
    assert code_of(view) == (200, None)
    assert (view.json()["status"], view.json()["summary"]) == ("draft", None)
    assert view.json()["can_edit"] is True
    working = get(client, cookie, "/api/v1/progress-reports")
    assert str(made.id) in {item["id"] for item in working.json()["items"]}
    staff = sign_in(client, coordinator.username)
    assert code_of(get(client, staff, f"/api/v1/progress-reports/{made.id}")) == (
        404,
        "not_found",
    )
    assert subject is not None


def test_a_monthly_placeholder_with_its_subject(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    schedule = insert_schedule(
        url,
        student=student,
        author=tutor.id,
        cadence="monthly",
        starts_on=FEBRUARY,
        subject=subject,
    )

    run(url, frozen, NOW)

    [made] = placeholders(url, schedule)
    assert (made.kind, made.period_start, made.period_end, made.subject_id) == (
        "monthly",
        FEBRUARY,
        date(2031, 2, 28),
        subject,
    )
    [due] = notified(url, tutor)
    assert due.params["kind"] == "monthly"


def test_a_sunday_night_in_mexico_city_is_still_last_week(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url, starts_on=LAST_MONDAY)

    # Sunday 9 March, 23:30 in Mexico City (05:30 UTC on Monday).
    run(url, frozen, datetime(2031, 3, 10, 5, 30, tzinfo=UTC))
    assert [r.period_start for r in placeholders(url, schedule)] == [LAST_MONDAY]

    # Monday 00:30 there: the week of 3-9 March ended.
    run(url, frozen, datetime(2031, 3, 10, 6, 30, tzinfo=UTC))
    assert [r.period_start for r in placeholders(url, schedule)] == [
        LAST_MONDAY,
        MONDAY,
    ]
    assert len(notified(url, tutor)) == 2


def test_tijuana_on_the_dst_sunday_night(module_seeded_url, frozen):
    url = module_seeded_url
    _, _, _, schedule = scheduled(url, starts_on=LAST_MONDAY)
    set_zone(url, "America/Tijuana")
    try:
        # DST starts on Sunday 9 March 2031: 23:30 PDT is 06:30 UTC.
        run(url, frozen, datetime(2031, 3, 10, 6, 30, tzinfo=UTC))
        assert [r.period_start for r in placeholders(url, schedule)] == [LAST_MONDAY]
        run(url, frozen, datetime(2031, 3, 10, 7, 30, tzinfo=UTC))
        assert [r.period_start for r in placeholders(url, schedule)] == [
            LAST_MONDAY,
            MONDAY,
        ]
    finally:
        set_zone(url, "America/Mexico_City")


# --- Idempotency and E2 ---------------------------------------------------------------


def test_a_second_run_creates_nothing(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url)

    run(url, frozen, NEXT_MONDAY_RUN)
    run(url, frozen, NEXT_MONDAY_RUN + timedelta(days=1))

    assert len(placeholders(url, schedule)) == 1
    assert len(notified(url, tutor)) == 1


def test_a_deleted_placeholder_is_never_created_again(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url)
    run(url, frozen, NEXT_MONDAY_RUN)
    [made] = placeholders(url, schedule)
    run_sql(
        url,
        "UPDATE progress_reports SET deleted_at = now(), deleted_by_user_id ="
        " author_user_id WHERE id = :id",
        {"id": made.id},
    )

    run(url, frozen, NEXT_MONDAY_RUN + timedelta(days=1))

    assert [r.id for r in placeholders(url, schedule)] == [made.id]
    assert len(notified(url, tutor)) == 1


def test_a_retracted_placeholder_is_never_created_again(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url)
    run(url, frozen, NEXT_MONDAY_RUN)
    [made] = placeholders(url, schedule)
    run_sql(
        url,
        "UPDATE progress_reports SET status = 'retracted', summary = 'Hecho',"
        " facts = '{\"schema_version\": 1}'::jsonb, facts_computed_at = now(),"
        " submitted_at = now(), retracted_at = now(), retracted_by_user_id ="
        " author_user_id, retract_reason = 'Equivocado' WHERE id = :id",
        {"id": made.id},
    )

    run(url, frozen, NEXT_MONDAY_RUN + timedelta(days=1))

    assert [r.id for r in placeholders(url, schedule)] == [made.id]
    assert len(notified(url, tutor)) == 1


def test_a_report_the_tutor_wrote_skips_the_period(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, student, _, schedule = scheduled(url)
    insert_report(
        url, student=student, author=tutor.id, period_start=MONDAY, status="submitted"
    )

    run(url, frozen, NEXT_MONDAY_RUN)

    assert placeholders(url, schedule) == []
    assert notified(url, tutor) == []


def test_a_retracted_report_the_tutor_wrote_doesnt(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, student, _, schedule = scheduled(url)
    insert_report(
        url, student=student, author=tutor.id, period_start=MONDAY, status="retracted"
    )

    run(url, frozen, NEXT_MONDAY_RUN)

    assert [r.period_start for r in placeholders(url, schedule)] == [MONDAY]
    assert len(notified(url, tutor)) == 1


def test_after_a_gap_only_the_latest_period_is_filled(module_seeded_url, frozen):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url, starts_on=date(2031, 2, 3))

    # The worker was down for weeks: only the week of 3-9 March.
    run(url, frozen, NEXT_MONDAY_RUN)

    assert [r.period_start for r in placeholders(url, schedule)] == [MONDAY]
    assert len(notified(url, tutor)) == 1


# --- Skips ----------------------------------------------------------------------------


def _expect_nothing(url: str, tutor: CreatedUser, schedule: uuid.UUID) -> None:
    assert placeholders(url, schedule) == []
    assert notified(url, tutor) == []


def test_an_inactive_or_not_yet_due_schedule_is_skipped(module_seeded_url, frozen):
    url = module_seeded_url
    inactive = scheduled(url, active=False)
    # Created on 5 March for the week of 10 March: nothing on 10 March.
    later = scheduled(url, starts_on=date(2031, 3, 10))

    run(url, frozen, NEXT_MONDAY_RUN)

    _expect_nothing(url, inactive[0], inactive[3])
    _expect_nothing(url, later[0], later[3])


@pytest.mark.parametrize(
    "reason",
    [
        "student_inactive",
        "author_not_tutor",
        "author_not_current",
        "subject_inactive",
        "subject_not_assigned",
    ],
)
def test_an_ineligible_schedule_is_skipped(module_seeded_url, frozen, reason):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    schedule_subject = new_subject(url) if reason == "subject_not_assigned" else subject
    schedule = insert_schedule(
        url,
        student=student,
        author=tutor.id,
        starts_on=MONDAY,
        subject=schedule_subject,
    )
    changes = {
        "student_inactive": (
            "UPDATE students SET is_active = false WHERE id = :x",
            student,
        ),
        "author_not_tutor": (
            "UPDATE users SET state = 'suspended' WHERE id = :x",
            tutor.id,
        ),
        # The tutor's sessions ended more than 30 days ago.
        "author_not_current": (
            "UPDATE class_sessions SET starts_at = starts_at - interval '40 days',"
            " ends_at = ends_at - interval '40 days', state = 'completed'"
            " WHERE tutor_user_id = :x",
            tutor.id,
        ),
        "subject_inactive": (
            "UPDATE subjects SET is_active = false WHERE id = :x",
            subject,
        ),
    }
    if reason in changes:
        sql, target = changes[reason]
        run_sql(url, sql, {"x": target})

    result = run(url, frozen, NEXT_MONDAY_RUN)

    assert result.ineligible.get(reason, 0) >= 1
    _expect_nothing(url, tutor, schedule)


def test_a_failing_schedule_does_not_block_the_others(
    module_seeded_url, frozen, monkeypatch, caplog
):
    url = module_seeded_url
    broken = scheduled(url)
    fine = scheduled(url)
    real = tasks.place_one

    async def place(session: Any, schedule_id: uuid.UUID, **kwargs: Any) -> Any:
        if schedule_id == broken[3]:
            raise RuntimeError("boom")
        return await real(session, schedule_id, **kwargs)

    monkeypatch.setattr(tasks, "place_one", place)

    result = run(url, frozen, NEXT_MONDAY_RUN)

    assert result.failed == 1
    _expect_nothing(url, broken[0], broken[3])
    assert len(placeholders(url, fine[3])) == 1
    [record] = [
        r
        for r in caplog.records
        if r.getMessage() == "Progress report placeholder failed"
    ]
    assert record.schedule_id == str(broken[3])
    assert record.error == "RuntimeError"
    # The next run retries it.
    monkeypatch.setattr(tasks, "place_one", real)
    run(url, frozen, NEXT_MONDAY_RUN + timedelta(hours=1))
    assert len(placeholders(url, broken[3])) == 1


def test_the_run_logs_counts_only(module_seeded_url, frozen, caplog):
    url = module_seeded_url
    tutor, _, _, schedule = scheduled(url)
    caplog.set_level("INFO", logger="app.workflows.progress_report_schedule_tasks")

    result = run(url, frozen, NEXT_MONDAY_RUN)

    [record] = [
        r
        for r in caplog.records
        if r.getMessage() == "Created progress report placeholders"
    ]
    assert record.created_count == result.created >= 1
    assert record.schedule_count == result.schedules
    assert record.failed_count == 0
    assert str(schedule) not in str(record.__dict__)
    assert str(tutor.id) not in str(record.__dict__)


# --- Budgets --------------------------------------------------------------------------


def test_one_schedule_takes_at_most_ten_statements(module_seeded_url):
    url = module_seeded_url
    _, _, _, whole = scheduled(url)
    tutor, student, subject = teaching(url)
    with_subject = insert_schedule(
        url, student=student, author=tutor.id, starts_on=MONDAY, subject=subject
    )
    today = date(2031, 3, 10)

    for target in (whole, with_subject):
        with frozen_clock(NEXT_MONDAY_RUN):
            counted, outcome = count_statements(
                url,
                lambda s, target=target: tasks.place_one(
                    s, target, now=NEXT_MONDAY_RUN, today=today
                ),
            )
        assert outcome == "created"
        # The audit event is flushed at the commit: one more.
        assert counted + 1 <= 10


def test_the_selection_is_one_statement_for_1_and_50(module_seeded_url):
    url = module_seeded_url
    scheduled(url)
    with frozen_clock(NEXT_MONDAY_RUN):
        one, _ = count_statements(
            url, lambda s: tasks.due_schedules(s, NEXT_MONDAY_RUN)
        )
    tutor, student, _ = teaching(url)
    for n in range(49):
        insert_schedule(
            url,
            student=student,
            author=tutor.id,
            starts_on=MONDAY,
            subject=new_subject(url) if n else None,
        )
    with frozen_clock(NEXT_MONDAY_RUN):
        fifty, (_, ids) = count_statements(
            url, lambda s: tasks.due_schedules(s, NEXT_MONDAY_RUN)
        )

    assert len(ids) >= 50
    # The zone, then the one selection.
    assert one == fifty == 2
