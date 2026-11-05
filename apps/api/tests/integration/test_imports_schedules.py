"""The ``schedules`` import kind end to end (#123 B1-B6; E1, E3-E5, E10).

Each test takes its own decade (closures are global in the module database)
and freezes the clock at its :class:`~tests.integration.series_support.
Timeline` "now" (a Sunday 12:00, Mexico City). The task runs in-process
through :func:`run_job`, under the same frozen clock.
"""

import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration.imports import service
from app.modules.administration.imports.registry import (
    RUN_SETTINGS_KEY,
    ImportRowRef,
)
from app.modules.identity.permissions import OWNER
from app.modules.scheduling.service import SCHEDULES_IMPORT_KIND
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql, settings_for
from tests.integration.import_support import (
    action,
    clear_queue,
    codes,
    csv_text,
    job_path,
    post_import,
    principal_of,
    rows_of,
    run_job,
    unique,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.series_support import Timeline, timeline
from tests.integration.test_class_session_create import (
    close_center,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

HEADER = (
    "student_given_names",
    "student_family_names",
    "student_birthdate",
    "subject",
    "education_level",
    "starts_on",
    "start_time",
    "duration_minutes",
    "weekdays",
    "interval",
    "until",
    "count",
    "tutor_username",
    "classroom",
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


# --- Data -----------------------------------------------------------------------


def fresh_student(url: str, **options: Any) -> uuid.UUID:
    """A new student with a family name no other test uses (so the natural
    key names exactly one student)."""
    student = new_student(url, **options)
    run_sql(
        url,
        "UPDATE students SET family_names = :f WHERE id = :i",
        {"f": unique("Alumno"), "i": student},
    )
    return student


def student_key(url: str, student_id: uuid.UUID) -> dict[str, str]:
    [row] = run_sql(
        url,
        "SELECT given_names, family_names, birthdate FROM students WHERE id = :i",
        {"i": student_id},
    )
    return {
        "student_given_names": row.given_names.upper(),
        "student_family_names": row.family_names,
        "student_birthdate": row.birthdate.isoformat(),
    }


def subject_names(url: str, subject_id: uuid.UUID) -> dict[str, str]:
    [row] = run_sql(
        url,
        "SELECT s.name AS subject, l.name AS level FROM subjects s"
        " JOIN education_levels l ON l.id = s.education_level_id WHERE s.id = :i",
        {"i": subject_id},
    )
    return {"subject": row.subject.lower(), "education_level": row.level.upper()}


def username(url: str, user_id: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT username FROM users WHERE id = :i", {"i": user_id})
    return str(row.username)


def room_name(url: str, room_id: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT name FROM classrooms WHERE id = :i", {"i": room_id})
    return str(row.name)


def row(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    tl: Timeline,
    **change: str,
) -> dict[str, str]:
    values = {
        **student_key(url, student),
        **subject_names(url, subject),
        "starts_on": tl.monday.isoformat(),
        "start_time": "16:00",
        "duration_minutes": "60",
        "weekdays": "1 3",
        "count": "4",
    }
    values.update(change)
    return values


def stage(
    client: TestClient, cookie: Any, rows: Sequence[Mapping[str, str]], **options: Any
) -> dict[str, Any]:
    response = post_import(
        client, cookie, "schedules", csv_text(HEADER, rows, **options), "horarios.csv"
    )
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()
    return job


def commit_and_run(client: TestClient, cookie: Any, url: str, job_id: Any) -> None:
    response = action(client, cookie, job_id, "commit")
    assert response.status_code == 202, [
        (r["status"], r["errors"]) for r in items(client, cookie, job_id)
    ]
    run_job(url)


def items(client: TestClient, cookie: Any, job_id: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = rows_of(client, cookie, job_id)["items"]
    return found


def series_of(url: str, student: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT cs.* FROM class_series cs JOIN class_series_participants p"
        " ON p.class_series_id = cs.id WHERE p.student_id = :s ORDER BY cs.created_at",
        {"s": student},
    )


def states(url: str, series_id: Any) -> list[str]:
    return [
        r.state
        for r in run_sql(
            url,
            "SELECT state FROM class_sessions WHERE series_id = :i ORDER BY starts_at",
            {"i": series_id},
        )
    ]


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, uuid.UUID]:
    subject = new_subject(module_seeded_url)
    return {
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url),
    }


# --- B1, B4, B6: a good file ------------------------------------------------------


def test_b4_b6_a_good_file_creates_series_like_by_hand(
    client, owner, module_seeded_url, refs
):
    url = module_seeded_url
    tl = timeline()
    students = [fresh_student(url) for _ in range(3)]
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        rows = [
            row(
                url,
                students[0],
                refs["subject"],
                tl,
                tutor_username=username(url, refs["tutor"]).upper(),
            ),
            row(
                url,
                students[1],
                refs["subject"],
                tl,
                start_time="9:00",
                weekdays="2",
                count="",
                until=(tl.monday + timedelta(weeks=3)).strftime("%d/%m/%Y"),
                classroom=room_name(url, refs["room"]).upper(),
            ),
            row(url, students[2], refs["subject"], tl, weekdays="5", count="1"),
        ]
        job = stage(client, cookie, rows, delimiter=";", bom=True)
        assert job["counts"]["ready"] == 3, items(client, cookie, job["id"])
        commit_and_run(client, cookie, url, job["id"])
        done = get(client, cookie, job_path(job["id"])).json()
        report = items(client, cookie, job["id"])

    assert done["status"] == "committed"
    assert done["counts"]["created"] == 3
    with_tutor, with_room, one_off = (series_of(url, s) for s in students)
    assert [r["record_id"] for r in report] == [
        str(with_tutor[0].id),
        str(with_room[0].id),
        str(one_off[0].id),
    ]
    # E5: a tutor gets the pending series offer and one notification.
    assert with_tutor[0].tutor_user_id == refs["tutor"]
    assert with_tutor[0].assignment_status == "pending"
    assert set(states(url, with_tutor[0].id)) == {"offered"}
    offers = run_sql(
        url,
        "SELECT status FROM assignment_offers WHERE class_series_id = :i",
        {"i": with_tutor[0].id},
    )
    assert [o.status for o in offers] == ["pending"]
    notified = run_sql(
        url,
        "SELECT count(*) AS n FROM notifications WHERE recipient_user_id = :u",
        {"u": refs["tutor"]},
    )
    assert notified[0].n == 1
    # Without a tutor the occurrences are unassigned.
    assert set(states(url, with_room[0].id)) == {"unassigned"}
    assert with_room[0].classroom_id == refs["room"]
    assert str(with_room[0].start_time) == "09:00:00"
    assert len(states(url, with_room[0].id)) == 3
    assert states(url, one_off[0].id) == ["unassigned"]
    # Audit: the usual create events, acting as the committer.
    for series in (with_tutor[0], with_room[0], one_off[0]):
        [created] = events(
            url, record_type="class_series", record_id=series.id, action="create"
        )
        assert created.actor_user_id == owner.id


# --- B2: staging errors ---------------------------------------------------------


def test_b2_every_staging_code(client, owner, module_seeded_url):
    url = module_seeded_url
    tl = timeline()
    subject_id = new_subject(url)
    good = fresh_student(url)
    inactive_student = fresh_student(url, is_active=False)
    twin_a, twin_b = fresh_student(url), fresh_student(url)
    run_sql(
        url,
        "UPDATE students SET given_names = :g, family_names = :f, birthdate = :b"
        " WHERE id IN (:a, :c)",
        {
            "g": "Gemela",
            "f": unique("Igual"),
            "b": tl.monday - timedelta(days=4000),
            "a": twin_a,
            "c": twin_b,
        },
    )
    inactive_subject = new_subject(url, is_active=False)
    inactive_tutor = new_tutor(url, state="suspended")
    inactive_room = new_room(url, is_active=False)

    def bad(student: uuid.UUID = good, **change: str) -> dict[str, str]:
        values = row(url, student, subject_id, tl)
        values.update(change)
        return values

    cases: list[tuple[dict[str, str], set[tuple[Any, str]]]] = [
        (bad(student_given_names="Nadie"), {(None, "student_unknown")}),
        (bad(twin_a), {(None, "student_ambiguous")}),
        (bad(inactive_student), {(None, "student_inactive")}),
        (bad(subject="No existe"), {("subject", "subject_unknown")}),
        (
            {**bad(), **subject_names(url, inactive_subject)},
            {("subject", "subject_inactive")},
        ),
        (bad(tutor_username="nadie.aqui"), {("tutor_username", "tutor_unknown")}),
        (
            bad(tutor_username=username(url, inactive_tutor)),
            {("tutor_username", "tutor_inactive")},
        ),
        (bad(classroom="Sala fantasma"), {("classroom", "classroom_unknown")}),
        (
            bad(classroom=room_name(url, inactive_room)),
            {("classroom", "classroom_inactive")},
        ),
        (bad(duration_minutes="sesenta"), {("duration_minutes", "invalid")}),
        (bad(starts_on="2031-02-30"), {("starts_on", "invalid_date")}),
        (bad(start_time="4pm"), {("start_time", "time_format")}),
        (bad(duration_minutes="10"), {("duration_minutes", "duration_out_of_range")}),
        (bad(weekdays="lunes"), {("weekdays", "invalid")}),
        (bad(weekdays="1 8"), {("weekdays", "weekday_out_of_range")}),
        (bad(weekdays="1 1"), {("weekdays", "weekdays_duplicate")}),
        (bad(interval="5"), {("interval", "interval_out_of_range")}),
        (
            bad(until=(tl.monday + timedelta(weeks=2)).isoformat()),
            {("until", "until_and_count")},
        ),
        (bad(count="201"), {("count", "count_out_of_range")}),
        (
            bad(count="", until=(tl.monday - timedelta(days=7)).isoformat()),
            {("until", "until_before_start")},
        ),
        (
            bad(count="", until=tl.monday.isoformat(), weekdays="2"),
            {(None, "no_occurrences")},
        ),
        (
            bad(starts_on=(tl.monday - timedelta(days=2)).isoformat()),
            {("starts_on", "starts_on_in_past")},
        ),
        (bad(start_time="23:30"), {("start_time", "crosses_local_date")}),
        (bad(subject=""), {("subject", "required")}),
    ]
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        job = stage(client, cookie, [case for case, _ in cases])
        report = items(client, cookie, job["id"])

    assert job["counts"]["error"] == len(cases)
    for item, (_, expected) in zip(report, cases, strict=True):
        assert item["status"] == "error"
        assert codes(item["errors"]) == expected, (item["row_number"], item["errors"])
    assert series_of(url, good) == []


# --- B3: duplicates and bounded staging ---------------------------------------------


def test_b3_duplicates_existing_and_in_file(client, owner, module_seeded_url, refs):
    url = module_seeded_url
    tl = timeline()
    student = fresh_student(url)
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        first = stage(client, cookie, [row(url, student, refs["subject"], tl)])
        commit_and_run(client, cookie, url, first["id"])
        again = row(url, student, refs["subject"], tl, weekdays="3 1", count="8")
        other = row(url, student, refs["subject"], tl, start_time="18:00")
        job = stage(client, cookie, [again, other, other])
        report = items(client, cookie, job["id"])

    assert [(r["status"], codes(r["warnings"])) for r in report] == [
        ("duplicate", {(None, "duplicate_existing")}),
        ("ready", set()),
        ("duplicate", {(None, "duplicate_in_file")}),
    ]


def test_b3_staging_runs_a_fixed_number_of_statements(module_seeded_url, owner, refs):
    url = module_seeded_url
    tl = timeline()
    student = fresh_student(url)
    importer = principal_of(owner, OWNER)
    tutor = username(url, refs["tutor"])
    room = room_name(url, refs["room"])

    def staged(count: int) -> int:
        csv = csv_text(
            HEADER,
            [
                row(
                    url,
                    student,
                    refs["subject"],
                    tl,
                    start_time=f"{8 + i % 10:02d}:00",
                    tutor_username=tutor,
                    classroom=room,
                )
                for i in range(count)
            ],
        )

        async def call(session: AsyncSession) -> object:
            return await service.create_import_job(
                session,
                principal=importer,
                kind_code="schedules",
                file_name=None,
                csv_text=csv,
            )

        statements, _ = count_statements(url, call)
        return statements

    with frozen_clock(tl.now):
        assert staged(3) == staged(300)


# --- B4, B5: conflicts at commit ----------------------------------------------------


def test_b5_conflicts_are_checked_at_commit_across_rows(
    client, owner, module_seeded_url, refs
):
    url = module_seeded_url
    tl = timeline()
    first, second, busy, soft, closed = (fresh_student(url) for _ in range(5))
    tutor = username(url, refs["tutor"])
    unqualified = new_subject(url)
    close_center(url, tl.week(1, 3))
    existing_session(
        url, starts_at=tl.at(tl.week(1, 1), "16:30"), minutes=60, student_id=busy
    )
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        rows = [
            row(url, first, refs["subject"], tl, tutor_username=tutor),
            row(url, second, refs["subject"], tl, tutor_username=tutor),
            row(url, busy, refs["subject"], tl, start_time="16:00"),
            row(url, soft, unqualified, tl, start_time="12:00", tutor_username=tutor),
            row(url, closed, refs["subject"], tl, start_time="17:00", count="3"),
        ]
        job = stage(client, cookie, rows)
        assert job["counts"]["ready"] == 5, [
            (r["status"], r["errors"]) for r in items(client, cookie, job["id"])
        ]
        commit_and_run(client, cookie, url, job["id"])
        done = get(client, cookie, job_path(job["id"])).json()
        report = items(client, cookie, job["id"])

    assert done["status"] == "committed"
    assert [(r["status"], [e["code"] for e in r["errors"]]) for r in report] == [
        ("created", []),
        ("failed", ["scheduling_conflict", "tutor_overlap"]),
        ("failed", ["scheduling_conflict", "student_overlap"]),
        ("failed", ["scheduling_conflict", "tutor_not_qualified"]),
        ("created", []),
    ]
    assert all(e["column"] is None for r in report for e in r["errors"])
    # A failed row writes nothing (its savepoint rolled back).
    assert series_of(url, second) == series_of(url, busy) == series_of(url, soft) == []
    [kept] = series_of(url, closed)
    assert states(url, kept.id) == ["unassigned", "skipped_closure", "unassigned"]
    assert done["counts"] == {**done["counts"], "created": 2, "failed": 3}


def test_b4_a_start_date_that_passes_before_commit_fails(
    client, owner, module_seeded_url, refs
):
    url = module_seeded_url
    tl = timeline()
    student = fresh_student(url)
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        job = stage(client, cookie, [row(url, student, refs["subject"], tl)])
    with frozen_clock(tl.now + timedelta(days=2)):
        commit_and_run(client, sign_in(client, owner.username), url, job["id"])
        [item] = items(client, sign_in(client, owner.username), job["id"])

    assert item["status"] == "failed"
    assert codes(item["errors"]) == {("starts_on", "starts_on_in_past")}
    assert series_of(url, student) == []


def test_b4_commit_row_skips_a_duplicate_found_only_now(module_seeded_url, owner, refs):
    url = module_seeded_url
    tl = timeline()
    student = fresh_student(url)
    values = row(url, student, refs["subject"], tl)
    ref = ImportRowRef(uuid.uuid4(), 2)

    async def commit(session: AsyncSession) -> Any:
        session.info[RUN_SETTINGS_KEY] = settings_for(url)
        return await SCHEDULES_IMPORT_KIND.commit_row(session, values, ref, owner.id)

    with frozen_clock(tl.now):
        created = in_uow(url, commit, actor_id=owner.id)
        again = in_uow(url, commit, actor_id=owner.id)

    assert created.status == "created"
    assert (again.status, again.code) == ("skipped", "duplicate_existing")
    assert len(series_of(url, student)) == 1
