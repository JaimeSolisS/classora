"""Creating and reading one-off individual sessions (#48 C, D, E, G1, G2) with
real logins through the real app.

The tests share one seeded database per module; each test makes its own
students, tutors, subjects and rooms, so only closures (global) need their
own dates (``unique_year``). Sessions are scheduled in the fixed week of
Monday 2031-01-06 in the default center zone (America/Mexico_City, UTC-6,
no DST), far beyond the booking notice; the notice tests freeze the clock.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import (
    Classroom,
    ClassroomAvailabilityWindow,
    Closure,
    EducationLevel,
    Subject,
)
from app.modules.families.models import Student
from app.modules.identity.permissions import ADMINISTRATOR, OWNER, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.staff.models import (
    TutorAvailabilityException,
    TutorAvailabilityWindow,
    TutorQualification,
)
from tests.conftest import frozen_clock
from tests.factories.families import persist_student
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_classroom,
    persist_subject,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    count_statements,
    get,
    patch,
    post,
    unique_year,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

SESSIONS = "/api/v1/class-sessions"
MX = ZoneInfo("America/Mexico_City")
MONDAY = date(2031, 1, 6)
SATURDAY = MONDAY + timedelta(days=5)
SUNDAY = MONDAY + timedelta(days=6)
MONDAY_10 = datetime(2031, 1, 6, 10, 0, tzinfo=MX)
MONDAY_11 = datetime(2031, 1, 6, 11, 0, tzinfo=MX)
ALL_DAY = [(weekday, time(0), time(23, 59)) for weekday in range(1, 8)]

CREATED = (201, None)
CONFLICT = (409, "scheduling_conflict")
INVALID = (422, "validation_error")


def at(day: date, hhmm: str, zone: ZoneInfo = MX) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=zone)


# --- Data (committed through the ORM) ------------------------------------------------


async def change(session: AsyncSession, model: Any, row_id: Any, **values: Any) -> None:
    """Set columns of one row through the ORM (audited tables refuse bulk
    statements)."""
    row = await session.get(model, row_id)
    for key, value in values.items():
        setattr(row, key, value)
    await session.flush()


def new_student(url: str, *, is_active: bool = True) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_student(session, contacts=0, is_active=is_active)).id

    return in_uow(url, _make)


def new_subject(
    url: str, *, is_active: bool = True, level_active: bool = True
) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        subject = await persist_subject(session)
        subject.is_active = is_active
        if not level_active:
            await change(
                session, EducationLevel, subject.education_level_id, is_active=False
            )
        await session.flush()
        return subject.id

    return in_uow(url, _make)


def new_room(
    url: str,
    *,
    capacity: int = 10,
    windows: Iterable[tuple[int, time, time]] = (),
    is_active: bool = True,
) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        room = await persist_classroom(session, capacity=capacity)
        room.is_active = is_active
        session.add_all(
            ClassroomAvailabilityWindow(
                classroom_id=room.id, weekday=day, starts_at=start, ends_at=end
            )
            for day, start, end in windows
        )
        await session.flush()
        return room.id

    return in_uow(url, _make)


def new_tutor(
    url: str,
    *,
    qualified_for: Iterable[uuid.UUID] = (),
    windows: Iterable[tuple[int, time, time]] = ALL_DAY,
    **overrides: Any,
) -> uuid.UUID:
    """A user with the tutor role (active unless overridden), with weekly
    windows (all day, every day by default) and approved qualifications."""
    tutor = create_user(url, roles=(TUTOR,), **overrides)
    subjects = list(qualified_for)
    weekly = list(windows)

    async def _make(session: AsyncSession) -> None:
        session.add_all(
            TutorAvailabilityWindow(
                tutor_user_id=tutor.id, weekday=day, starts_at=start, ends_at=end
            )
            for day, start, end in weekly
        )
        session.add_all(
            TutorQualification(
                tutor_user_id=tutor.id,
                subject_id=subject_id,
                status="approved",
                decided_at=datetime(2026, 1, 5, tzinfo=MX),
                decided_by_user_id=tutor.id,
            )
            for subject_id in subjects
        )
        await session.flush()

    in_uow(url, _make)
    return tutor.id


def revoke_all(url: str, tutor_id: uuid.UUID) -> None:
    async def _revoke(session: AsyncSession) -> None:
        rows = await session.scalars(
            select(TutorQualification).where(
                TutorQualification.tutor_user_id == tutor_id
            )
        )
        for row in rows:
            row.status = "revoked"
            row.revoked_at = datetime(2026, 2, 2, tzinfo=MX)
            row.revoked_by_user_id = tutor_id
            row.revocation_reason = "Ya no imparte la materia"
        await session.flush()

    in_uow(url, _revoke)


def time_off(url: str, tutor_id: uuid.UUID, day: date) -> None:
    async def _add(session: AsyncSession) -> None:
        session.add(
            TutorAvailabilityException(
                tutor_user_id=tutor_id, kind="unavailable", starts_on=day, ends_on=day
            )
        )

    in_uow(url, _add)


def close_center(url: str, day: date) -> None:
    async def _add(session: AsyncSession) -> None:
        session.add(
            Closure(
                name="Día festivo", closure_type="holiday", starts_on=day, ends_on=day
            )
        )

    in_uow(url, _add)


def existing_session(
    url: str,
    *,
    starts_at: datetime,
    minutes: int = 60,
    state: str = "confirmed",
    student_id: uuid.UUID | None = None,
    **overrides: Any,
) -> uuid.UUID:
    """A stored session (``persist_class_session``), with ``student_id`` as
    its participant when given."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await persist_class_session(
            session,
            state=state,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            scheduled_minutes=minutes,
            **overrides,
        )
        if student_id is not None:
            await add_participant(session, made, student_id)
        return made.id

    return in_uow(url, _make)


def session_count(url: str) -> tuple[int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_sessions) AS s,"
        " (SELECT count(*) FROM class_session_participants) AS p",
    )
    return (int(row.s), int(row.p))


# --- Requests ------------------------------------------------------------------------


@dataclass(frozen=True)
class Setup:
    """A clean request's references: an active student, a subject, a tutor
    available all week and qualified for it, and a room without windows."""

    student: uuid.UUID
    subject: uuid.UUID
    tutor: uuid.UUID
    room: uuid.UUID


def body(
    setup: Setup | None = None,
    *,
    starts_at: datetime | str = MONDAY_10,
    ends_at: datetime | str = MONDAY_11,
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": "individual"}
    if setup is not None:
        payload |= {
            "student_id": str(setup.student),
            "subject_id": str(setup.subject),
            "tutor_user_id": str(setup.tutor),
            "classroom_id": str(setup.room),
        }
    payload["starts_at"] = (
        starts_at if isinstance(starts_at, str) else starts_at.isoformat()
    )
    payload["ends_at"] = ends_at if isinstance(ends_at, str) else ends_at.isoformat()
    for key, value in fields.items():
        payload[key] = str(value) if isinstance(value, uuid.UUID) else value
    return payload


def create(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Response:
    return post(client, cookie, SESSIONS, payload)


def conflicts_of(response: Response) -> list[dict[str, Any]]:
    assert code_of(response) == CONFLICT, response.text
    found: list[dict[str, Any]] = response.json()["conflicts"]
    return found


def codes_of(response: Response) -> list[tuple[str, str]]:
    return [(c["code"], c["severity"]) for c in conflicts_of(response)]


def field_errors(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"][-1], e["code"]) for e in response.json()["errors"]]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture
def setup(module_seeded_url: str) -> Setup:
    subject = new_subject(module_seeded_url)
    return Setup(
        student=new_student(module_seeded_url),
        subject=subject,
        tutor=new_tutor(module_seeded_url, qualified_for=[subject]),
        room=new_room(module_seeded_url),
    )


def assert_refused(
    url: str, client: TestClient, cookie: Any, payload: dict[str, Any]
) -> list[tuple[str, str]]:
    """The create is a 409; nothing is stored. Returns ``(code, severity)``."""
    before = session_count(url)
    response = create(client, cookie, payload)
    assert session_count(url) == before
    return codes_of(response)


# --- G1: one conflict at a time ------------------------------------------------------


@pytest.mark.parametrize("state", ["unassigned", "confirmed"])
def test_student_overlap(module_seeded_url, client, cookie, setup, state):
    other = existing_session(
        module_seeded_url,
        state=state,
        student_id=setup.student,
        starts_at=at(MONDAY, "10:30"),
    )
    before = session_count(module_seeded_url)

    response = create(client, cookie, body(setup))

    assert conflicts_of(response) == [
        {
            "code": "student_overlap",
            "severity": "hard",
            "details": {
                "student_id": str(setup.student),
                "class_session_ids": [str(other)],
            },
        }
    ]
    assert session_count(module_seeded_url) == before


def test_tutor_overlap(module_seeded_url, client, cookie, setup):
    other = existing_session(
        module_seeded_url,
        state="offered",
        tutor_user_id=setup.tutor,
        student_id=new_student(module_seeded_url),
        starts_at=at(MONDAY, "09:30"),
    )

    response = create(client, cookie, body(setup))

    assert conflicts_of(response) == [
        {
            "code": "tutor_overlap",
            "severity": "hard",
            "details": {"class_session_ids": [str(other)]},
        }
    ]


def test_room_overlap(module_seeded_url, client, cookie, setup):
    other = existing_session(
        module_seeded_url,
        state="unassigned",
        classroom_id=setup.room,
        starts_at=at(MONDAY, "10:00"),
        minutes=15,
    )

    codes = assert_refused(module_seeded_url, client, cookie, body(setup))

    assert codes == [("room_overlap", "hard")]
    assert conflicts_of(create(client, cookie, body(setup)))[0]["details"] == {
        "classroom_id": str(setup.room),
        "class_session_ids": [str(other)],
    }


def test_tutor_unavailable_outside_the_weekly_windows(
    module_seeded_url, client, cookie, setup
):
    tutor = new_tutor(
        module_seeded_url,
        qualified_for=[setup.subject],
        windows=[(1, time(16), time(20))],
    )

    response = create(client, cookie, body(setup, tutor_user_id=tutor))

    assert conflicts_of(response) == [
        {
            "code": "tutor_unavailable",
            "severity": "soft",
            "details": {"reasons": ["outside_weekly_availability"]},
        }
    ]


def test_tutor_unavailable_on_time_off(module_seeded_url, client, cookie, setup):
    time_off(module_seeded_url, setup.tutor, MONDAY)

    response = create(client, cookie, body(setup))

    assert conflicts_of(response) == [
        {
            "code": "tutor_unavailable",
            "severity": "soft",
            "details": {"reasons": ["time_off"]},
        }
    ]


def test_room_unavailable(module_seeded_url, client, cookie, setup):
    room = new_room(module_seeded_url, windows=[(1, time(16), time(20))])

    codes = assert_refused(
        module_seeded_url, client, cookie, body(setup, classroom_id=room)
    )

    assert codes == [("room_unavailable", "soft")]
    response = create(client, cookie, body(setup, classroom_id=room))
    assert conflicts_of(response)[0]["details"] == {"classroom_id": str(room)}


def test_center_closed_is_reported_once(module_seeded_url, client, cookie, setup):
    year = unique_year()
    day = date(year, 3, 1)
    while day.isoweekday() != 2:  # a Tuesday: open 09:00-20:00
        day += timedelta(days=1)
    close_center(module_seeded_url, day)
    payload = body(setup, starts_at=at(day, "10:00"), ends_at=at(day, "11:00"))

    response = create(client, cookie, payload)

    # The tutor's only reason is the closure: no tutor_unavailable as well.
    assert conflicts_of(response) == [
        {
            "code": "center_closed",
            "severity": "soft",
            "details": {"date": day.isoformat()},
        }
    ]


@pytest.mark.parametrize(
    ("day", "start", "end", "hours"),
    [
        (SUNDAY, "10:00", "11:00", (None, None)),
        (SATURDAY, "13:30", "14:30", ("09:00", "14:00")),
    ],
)
def test_outside_operating_hours(
    module_seeded_url, client, cookie, setup, day, start, end, hours
):
    payload = body(setup, starts_at=at(day, start), ends_at=at(day, end))

    response = create(client, cookie, payload)

    assert conflicts_of(response) == [
        {
            "code": "outside_operating_hours",
            "severity": "soft",
            "details": {
                "date": day.isoformat(),
                "opens_at": hours[0],
                "closes_at": hours[1],
            },
        }
    ]


def test_saturday_up_to_closing_time_is_inside(
    module_seeded_url, client, cookie, setup
):
    payload = body(
        setup, starts_at=at(SATURDAY, "13:00"), ends_at=at(SATURDAY, "14:00")
    )

    assert code_of(create(client, cookie, payload)) == CREATED


def test_tutor_not_qualified(module_seeded_url, client, cookie, setup):
    unqualified = new_tutor(module_seeded_url)

    response = create(client, cookie, body(setup, tutor_user_id=unqualified))

    assert conflicts_of(response) == [
        {
            "code": "tutor_not_qualified",
            "severity": "soft",
            "details": {"subject_id": str(setup.subject)},
        }
    ]


def test_a_revoked_qualification_no_longer_counts(
    module_seeded_url, client, cookie, setup
):
    revoke_all(module_seeded_url, setup.tutor)

    codes = assert_refused(module_seeded_url, client, cookie, body(setup))

    assert codes == [("tutor_not_qualified", "soft")]


def test_notice_window(module_seeded_url, client, setup):
    # The default policy (#61): 24 hours of booking notice.
    now = at(date(2031, 2, 3), "10:00")
    tuesday = date(2031, 2, 4)
    owner = create_user(module_seeded_url, roles=(OWNER,))

    def at_minute(start: datetime) -> dict[str, Any]:
        return body(
            setup,
            student_id=new_student(module_seeded_url),
            starts_at=start,
            ends_at=start + timedelta(hours=1),
        )

    with frozen_clock(now):
        cookie = sign_in(client, owner.username)
        too_soon = create(client, cookie, at_minute(at(tuesday, "09:59")))
        exactly = create(client, cookie, at_minute(at(tuesday, "10:00")))
        past = create(
            client,
            cookie,
            body(
                setup,
                student_id=new_student(module_seeded_url),
                starts_at=at(date(2031, 2, 3), "09:00"),
                ends_at=at(date(2031, 2, 3), "09:30"),
            ),
        )

    assert conflicts_of(too_soon) == [
        {
            "code": "notice_window",
            "severity": "soft",
            "details": {"booking_notice_hours": 24},
        }
    ]
    assert code_of(exactly) == CREATED, exactly.text
    assert codes_of(past) == [("notice_window", "soft")]


# --- G2: several conflicts, non-overlapping states, successes ------------------------


def test_every_conflict_is_listed_in_order(module_seeded_url, client, cookie, setup):
    existing_session(
        module_seeded_url,
        state="offered",
        student_id=setup.student,
        starts_at=at(SUNDAY, "10:00"),
    )
    unqualified = new_tutor(module_seeded_url)
    payload = body(
        setup,
        tutor_user_id=unqualified,
        starts_at=at(SUNDAY, "10:00"),
        ends_at=at(SUNDAY, "11:00"),
    )

    codes = assert_refused(module_seeded_url, client, cookie, payload)

    assert codes == [
        ("student_overlap", "hard"),
        ("outside_operating_hours", "soft"),
        ("tutor_not_qualified", "soft"),
    ]


@pytest.mark.parametrize("state", ["cancelled", "skipped_closure", "conflict"])
def test_sessions_that_hold_nothing_do_not_overlap(
    module_seeded_url, client, cookie, setup, state
):
    existing_session(
        module_seeded_url,
        state=state,
        student_id=setup.student,
        tutor_user_id=setup.tutor,
        classroom_id=setup.room,
        starts_at=at(MONDAY, "10:00"),
    )

    assert code_of(create(client, cookie, body(setup))) == CREATED


def test_back_to_back_sessions_are_accepted(module_seeded_url, client, cookie, setup):
    existing_session(
        module_seeded_url,
        state="confirmed",
        student_id=setup.student,
        tutor_user_id=setup.tutor,
        classroom_id=setup.room,
        starts_at=at(MONDAY, "09:00"),
    )
    after = body(setup, starts_at=at(MONDAY, "11:00"), ends_at=at(MONDAY, "12:00"))

    assert code_of(create(client, cookie, body(setup))) == CREATED
    assert code_of(create(client, cookie, after)) == CREATED


def test_a_clean_session_with_a_tutor_is_offered(
    module_seeded_url, client, cookie, setup
):
    before = session_count(module_seeded_url)

    response = create(client, cookie, body(setup))

    assert code_of(response) == CREATED, response.text
    created = response.json()
    assert session_count(module_seeded_url) == (before[0] + 1, before[1] + 1)
    assert created["kind"] == "individual"
    assert created["state"] == "offered"
    assert created["tutor"]["id"] == str(setup.tutor)
    assert created["tutor"]["display_name"]
    assert created["classroom"]["id"] == str(setup.room)
    assert created["classroom"]["is_active"] is True
    assert created["subject"]["id"] == str(setup.subject)
    assert created["subject"]["is_active"] is True
    assert [p["student_id"] for p in created["participants"]] == [str(setup.student)]
    assert created["scheduled_minutes"] == 60
    assert created["version"] == 1
    assert created["warnings"] == []
    assert datetime.fromisoformat(created["starts_at"]) == at(MONDAY, "10:00")
    assert datetime.fromisoformat(created["ends_at"]) == at(MONDAY, "11:00")
    [row] = run_sql(
        module_seeded_url,
        "SELECT state, tutor_user_id, kind FROM class_sessions WHERE id = :id",
        {"id": created["id"]},
    )
    assert (row.state, row.tutor_user_id, row.kind) == (
        "offered",
        setup.tutor,
        "individual",
    )
    # The detail of the GET is the same, without warnings.
    detail = get(client, cookie, f"{SESSIONS}/{created['id']}").json()
    assert detail == {k: v for k, v in created.items() if k != "warnings"}


def test_without_a_tutor_the_session_is_unassigned_and_tutor_checks_skip(
    module_seeded_url, client, cookie
):
    # A subject nobody is qualified for, on a time no tutor is free.
    payload = body(
        student_id=new_student(module_seeded_url),
        subject_id=new_subject(module_seeded_url),
        tutor_user_id=None,
    )

    response = create(client, cookie, payload)

    assert code_of(response) == CREATED, response.text
    assert response.json()["state"] == "unassigned"
    assert response.json()["tutor"] is None
    assert response.json()["classroom"] is None


def test_without_a_room_the_room_checks_skip(module_seeded_url, client, cookie, setup):
    existing_session(
        module_seeded_url,
        state="confirmed",
        classroom_id=setup.room,
        starts_at=at(MONDAY, "10:00"),
    )
    payload = body(setup)
    del payload["classroom_id"]

    response = create(client, cookie, payload)

    assert code_of(response) == CREATED, response.text
    assert response.json()["classroom"] is None


def test_a_capacity_1_room_takes_an_individual_session(
    module_seeded_url, client, cookie, setup
):
    room = new_room(module_seeded_url, capacity=1)

    response = create(client, cookie, body(setup, classroom_id=room))

    assert code_of(response) == CREATED, response.text


def test_an_administrator_creates_too(module_seeded_url, client, setup):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    response = create(client, sign_in(client, admin.username), body(setup))

    assert code_of(response) == CREATED


# --- G2: 422 -------------------------------------------------------------------------


def test_every_reference_error_comes_in_one_response(module_seeded_url, client, cookie):
    before = session_count(module_seeded_url)
    payload = body(
        student_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        tutor_user_id=uuid.uuid4(),
        classroom_id=uuid.uuid4(),
    )

    response = create(client, cookie, payload)

    assert field_errors(response) == [
        ("student_id", "student_unknown"),
        ("subject_id", "subject_unknown"),
        ("tutor_user_id", "tutor_unknown"),
        ("classroom_id", "classroom_unknown"),
    ]
    assert session_count(module_seeded_url) == before


def test_inactive_references_are_422(module_seeded_url, client, cookie):
    before = session_count(module_seeded_url)
    payload = body(
        student_id=new_student(module_seeded_url, is_active=False),
        subject_id=new_subject(module_seeded_url, is_active=False),
        tutor_user_id=new_tutor(module_seeded_url, state="suspended"),
        classroom_id=new_room(module_seeded_url, is_active=False),
    )

    assert field_errors(create(client, cookie, payload)) == [
        ("student_id", "student_inactive"),
        ("subject_id", "subject_inactive"),
        ("tutor_user_id", "tutor_inactive"),
        ("classroom_id", "classroom_inactive"),
    ]
    assert session_count(module_seeded_url) == before


def test_an_inactive_level_makes_the_subject_inactive(
    module_seeded_url, client, cookie, setup
):
    payload = body(setup, subject_id=new_subject(module_seeded_url, level_active=False))

    assert field_errors(create(client, cookie, payload)) == [
        ("subject_id", "subject_inactive")
    ]


def test_a_pending_tutor_is_inactive_and_a_non_tutor_is_unknown(
    module_seeded_url, client, cookie, setup
):
    pending = new_tutor(module_seeded_url, state="pending", password_hash=None)
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    assert field_errors(create(client, cookie, body(setup, tutor_user_id=pending))) == [
        ("tutor_user_id", "tutor_inactive")
    ]
    assert field_errors(
        create(client, cookie, body(setup, tutor_user_id=admin.id))
    ) == [("tutor_user_id", "tutor_unknown")]


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "error"),
    [
        (at(MONDAY, "11:00"), at(MONDAY, "10:00"), ("ends_at", "ends_before_start")),
        (
            at(MONDAY, "10:00"),
            at(MONDAY, "10:14"),
            ("ends_at", "duration_out_of_range"),
        ),
        (
            at(MONDAY, "08:00"),
            at(MONDAY, "16:01"),
            ("ends_at", "duration_out_of_range"),
        ),
        (
            at(MONDAY, "10:00:30"),
            at(MONDAY, "11:00"),
            ("starts_at", "not_whole_minutes"),
        ),
        (
            at(MONDAY, "10:00"),
            at(MONDAY, "11:00:00.5"),
            ("ends_at", "not_whole_minutes"),
        ),
        ("2031-01-06T10:00:00", at(MONDAY, "11:00"), ("starts_at", "naive_datetime")),
        (at(MONDAY, "10:00"), "2031-01-06T11:00:00", ("ends_at", "naive_datetime")),
        (
            at(MONDAY, "23:00"),
            at(MONDAY + timedelta(days=1), "01:00"),
            ("ends_at", "crosses_local_date"),
        ),
    ],
)
def test_bounds_are_422(
    module_seeded_url, client, cookie, setup, starts_at, ends_at, error
):
    before = session_count(module_seeded_url)

    response = create(client, cookie, body(setup, starts_at=starts_at, ends_at=ends_at))

    assert field_errors(response) == [error]
    assert session_count(module_seeded_url) == before


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "group"},
        {"override": {"codes": ["tutor_unavailable"], "reason": "Acordado"}},
        {"student_id": None},
    ],
)
def test_the_schema_rejects_other_kinds_and_overrides(
    module_seeded_url, client, cookie, setup, change
):
    response = create(client, cookie, body(setup) | change)

    assert code_of(response) == INVALID


# --- E: the read ---------------------------------------------------------------------


def test_the_read_takes_at_most_six_statements(
    module_seeded_url, client, cookie, setup
):
    created = create(client, cookie, body(setup)).json()

    count, detail = count_statements(
        module_seeded_url,
        lambda session: scheduling.get_class_session(session, uuid.UUID(created["id"])),
    )

    assert count <= 6
    assert detail.model_dump(mode="json") == {
        k: v for k, v in created.items() if k != "warnings"
    }


def test_an_unknown_session_is_404(client, cookie):
    response = get(client, cookie, f"{SESSIONS}/{uuid.uuid4()}")

    assert code_of(response) == (404, "not_found")


def test_the_read_shows_inactive_references_as_such(
    module_seeded_url, client, cookie, setup
):
    created = create(client, cookie, body(setup)).json()

    async def _deactivate(session: AsyncSession) -> None:
        await change(session, Classroom, setup.room, is_active=False)
        await change(session, Subject, setup.subject, is_active=False)
        await change(session, Student, setup.student, is_active=False)

    in_uow(module_seeded_url, _deactivate)
    detail = get(client, cookie, f"{SESSIONS}/{created['id']}").json()

    assert detail["classroom"]["is_active"] is False
    assert detail["subject"]["is_active"] is False
    assert detail["participants"][0]["is_active"] is False


# --- C6: the audit -------------------------------------------------------------------


def test_the_create_writes_only_the_automatic_create_events(
    module_seeded_url, client, cookie, owner, setup
):
    response = create(client, cookie, body(setup))
    session_id = uuid.UUID(response.json()["id"])

    [session_event] = events(module_seeded_url, record_id=session_id)
    participant_events = [
        event
        for event in events(module_seeded_url, record_type="class_session_participants")
        if event.changes
        and event.changes.get("class_session_id", {}).get("new") == str(session_id)
    ]

    assert (session_event.action, session_event.record_type) == (
        "create",
        "class_sessions",
    )
    assert session_event.actor_user_id == owner.id
    assert session_event.request_id
    [participant_event] = participant_events
    assert participant_event.action == "create"
    assert participant_event.actor_user_id == owner.id
    assert participant_event.request_id == session_event.request_id


# --- The zone: Tijuana and the time-zone guard (own database) ------------------------


def test_bounds_use_the_center_zone_and_a_session_blocks_a_zone_change(seeded_url):
    owner = create_user(seeded_url, roles=(OWNER,))
    subject = new_subject(seeded_url)
    tijuana = ZoneInfo("America/Tijuana")
    with auth_client(seeded_url) as client:
        cookie = sign_in(client, owner.username)

        def change_zone(version: int, zone: str) -> Response:
            reauthenticate(client, cookie)
            return patch(
                client,
                cookie,
                "/api/v1/business-settings",
                {"version": version, "time_zone": zone},
            )

        assert code_of(change_zone(1, "America/Tijuana")) == (200, None)
        # 23:30-00:30 in Tijuana (UTC-8) crosses its local date; in Mexico
        # City the same instants are 01:30-02:30 of one date.
        crossing = body(
            student_id=new_student(seeded_url),
            subject_id=subject,
            starts_at=at(MONDAY, "23:30", tijuana),
            ends_at=at(MONDAY + timedelta(days=1), "00:30", tijuana),
        )
        assert field_errors(create(client, cookie, crossing)) == [
            ("ends_at", "crosses_local_date")
        ]
        clean = body(
            student_id=new_student(seeded_url),
            subject_id=subject,
            starts_at=at(MONDAY, "10:00", tijuana),
            ends_at=at(MONDAY, "11:00", tijuana),
        )
        assert code_of(create(client, cookie, clean)) == CREATED

        blocked = change_zone(2, "America/Mexico_City")

        assert code_of(blocked) == (409, "tz_change_requires_migration")
