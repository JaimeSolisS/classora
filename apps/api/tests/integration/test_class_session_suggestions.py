"""``POST /api/v1/class-sessions/suggestions`` (#50 B, D4) with real logins
through the real app: every check excludes its slots, the drift guard
against the create path, the 422s, the statement budget and that nothing is
written.

The data helpers and the fixed week (Monday 2031-01-06,
America/Mexico_City, no DST; open Monday-Friday 09:00-20:00, Saturday
09:00-14:00, Sunday closed) come from ``test_class_session_create``.
Closures are global, so the tests that add one use a ``unique_year`` week.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSessionCreate, SlotSuggestionRequest
from app.modules.scheduling.suggestions import Candidate
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    count_statements,
    get,
    patch,
    post,
    unique_year,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.test_class_session_create import (
    CREATED,
    MONDAY,
    MX,
    SATURDAY,
    SESSIONS,
    SUNDAY,
    Setup,
    at,
    body,
    close_center,
    create,
    existing_session,
    field_errors,
    new_room,
    new_student,
    new_subject,
    new_tutor,
    revoke_all,
    time_off,
)

SUGGESTIONS = f"{SESSIONS}/suggestions"
OK = (200, None)
MX_ZONE = "America/Mexico_City"
TUESDAY = MONDAY + timedelta(days=1)
SEARCH_FIELDS = {"limit", "search_from", "search_to"}


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


def request(
    setup: Setup | None, day: date, start: str, end: str, **fields: Any
) -> dict[str, Any]:
    payload = body(setup, starts_at=at(day, start), ends_at=at(day, end))
    for key, value in fields.items():
        payload[key] = (
            str(value)
            if isinstance(value, uuid.UUID | date) and value is not None
            else value
        )
    return payload


def suggest(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Response:
    return post(client, cookie, SUGGESTIONS, payload)


def found(response: Response) -> dict[str, Any]:
    assert code_of(response) == OK, response.text
    result: dict[str, Any] = response.json()
    return result


def local(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(MX).strftime("%Y-%m-%d %H:%M")


def starts(response: Response) -> list[str]:
    return [local(slot["starts_at"]) for slot in found(response)["slots"]]


def at_day(day: date, *times: str) -> list[str]:
    return [f"{day.isoformat()} {hhmm}" for hhmm in times]


def week_of(year: int) -> date:
    """The first Monday of March of ``year``."""
    day = date(year, 3, 1)
    while day.isoweekday() != 1:
        day += timedelta(days=1)
    return day


def counts(url: str) -> tuple[int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_sessions) AS s,"
        " (SELECT count(*) FROM class_session_participants) AS p,"
        " (SELECT count(*) FROM audit_events) AS a",
    )
    return (int(row.s), int(row.p), int(row.a))


# --- B3-B5: the nearest free times, the defaults ------------------------------------


def test_the_nearest_free_times_come_first_and_the_defaults_are_echoed(
    module_seeded_url, client, cookie, setup
):
    # An unassigned session holds the student 10:00-11:00.
    existing_session(
        module_seeded_url,
        state="unassigned",
        student_id=setup.student,
        starts_at=at(MONDAY, "10:00"),
    )
    before = counts(module_seeded_url)

    response = suggest(client, cookie, request(setup, MONDAY, "10:30", "11:30"))

    result = found(response)
    assert (result["search_from"], result["search_to"], result["limit"]) == (
        "2031-01-06",
        "2031-01-12",
        5,
    )
    # 11:00 is 30 minutes away; 09:00 and 12:00 tie at 90: the earlier wins.
    assert starts(response) == at_day(
        MONDAY, "11:00", "11:15", "11:30", "11:45", "09:00"
    )
    assert all(
        datetime.fromisoformat(s["ends_at"]) - datetime.fromisoformat(s["starts_at"])
        == timedelta(hours=1)
        for s in result["slots"]
    )
    assert all(slot["warnings"] == [] for slot in result["slots"])
    # B2: nothing is written, audited or notified.
    assert counts(module_seeded_url) == before


def test_tutor_overlap_excludes_its_slots(module_seeded_url, client, cookie, setup):
    existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=setup.tutor,
        student_id=new_student(module_seeded_url),
        starts_at=at(MONDAY, "10:00"),
    )
    payload = request(setup, MONDAY, "10:00", "11:00", limit=3, search_to=MONDAY)

    assert starts(suggest(client, cookie, payload)) == at_day(
        MONDAY, "09:00", "11:00", "11:15"
    )


def test_room_overlap_with_an_unassigned_session(
    module_seeded_url, client, cookie, setup
):
    existing_session(
        module_seeded_url,
        state="unassigned",
        classroom_id=setup.room,
        starts_at=at(MONDAY, "10:00"),
    )
    payload = request(setup, MONDAY, "10:00", "11:00", limit=3, search_to=MONDAY)

    assert starts(suggest(client, cookie, payload)) == at_day(
        MONDAY, "09:00", "11:00", "11:15"
    )


def test_the_tutors_weekly_windows(module_seeded_url, client, cookie, setup):
    tutor = new_tutor(
        module_seeded_url,
        qualified_for=[setup.subject],
        windows=[(1, time(14), time(16))],
    )
    payload = request(setup, MONDAY, "10:00", "11:00", tutor_user_id=tutor, limit=10)

    # Monday 14:00-16:00 holds three one-hour starts; no other day has a window.
    assert starts(suggest(client, cookie, payload)) == at_day(
        MONDAY, "14:00", "14:15", "14:30", "14:45", "15:00"
    )


def test_a_time_off_day_has_no_slot(module_seeded_url, client, cookie, setup):
    time_off(module_seeded_url, setup.tutor, TUESDAY)
    payload = request(
        setup,
        TUESDAY,
        "10:00",
        "11:00",
        search_from=MONDAY,
        search_to=TUESDAY,
        limit=10,
    )

    slots = starts(suggest(client, cookie, payload))

    assert slots and all(s.startswith("2031-01-06") for s in slots)


def test_the_room_windows(module_seeded_url, client, cookie, setup):
    room = new_room(module_seeded_url, windows=[(1, time(15), time(17))])
    payload = request(
        setup, MONDAY, "10:00", "11:00", classroom_id=room, search_to=MONDAY, limit=10
    )

    assert starts(suggest(client, cookie, payload)) == at_day(
        MONDAY, "15:00", "15:15", "15:30", "15:45", "16:00"
    )


def test_a_closure_day_gives_no_slot(module_seeded_url, client, cookie, setup):
    monday = week_of(unique_year())
    tuesday = monday + timedelta(days=1)
    close_center(module_seeded_url, tuesday)
    payload = request(
        setup,
        tuesday,
        "10:00",
        "11:00",
        search_from=monday,
        search_to=monday + timedelta(days=2),
        limit=10,
    )

    slots = starts(suggest(client, cookie, payload))

    assert len(slots) == 10
    assert not any(s.startswith(tuesday.isoformat()) for s in slots)


def test_the_operating_hours(module_seeded_url, client, cookie, setup):
    payload = request(setup, SATURDAY, "13:00", "14:00", search_to=SUNDAY, limit=10)

    slots = starts(suggest(client, cookie, payload))

    # Saturday 09:00-14:00 holds starts up to 13:00 (the requested one is
    # left out); Sunday is closed.
    assert slots == at_day(
        SATURDAY,
        "12:45",
        "12:30",
        "12:15",
        "12:00",
        "11:45",
        "11:30",
        "11:15",
        "11:00",
        "10:45",
        "10:30",
    )


def test_the_notice_window(module_seeded_url, client, setup):
    # The default policy: 24 hours. Now is Sunday 10:00: Monday 10:00 is
    # exactly the notice.
    owner = create_user(module_seeded_url, roles=(OWNER,))
    payload = request(setup, MONDAY, "10:30", "11:30", search_to=MONDAY, limit=3)

    with frozen_clock(at(SUNDAY - timedelta(days=7), "10:00")):
        cookie = sign_in(client, owner.username)
        response = suggest(client, cookie, payload)

    assert starts(response) == at_day(MONDAY, "10:15", "10:45", "10:00")


def test_an_unqualified_or_revoked_tutor_gives_no_slot(
    module_seeded_url, client, cookie, setup
):
    unqualified = new_tutor(module_seeded_url)
    payload = request(setup, MONDAY, "10:00", "11:00")

    assert (
        found(suggest(client, cookie, payload | {"tutor_user_id": str(unqualified)}))[
            "slots"
        ]
        == []
    )
    revoke_all(module_seeded_url, setup.tutor)
    assert found(suggest(client, cookie, payload))["slots"] == []


def test_each_slot_carries_its_warnings(module_seeded_url, client, cookie, setup):
    response = patch(
        client,
        cookie,
        f"/api/v1/tutors/{setup.tutor}/availability/settings",
        {"version": 0, "daily_limit_minutes": 60},
    )
    assert code_of(response) == OK, response.text
    other = new_student(module_seeded_url)
    existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=setup.tutor,
        student_id=other,
        starts_at=at(MONDAY, "09:00"),
    )
    payload = request(setup, MONDAY, "10:00", "11:00", limit=1)

    [slot] = found(suggest(client, cookie, payload))["slots"]

    assert local(slot["starts_at"]) == "2031-01-06 10:15"
    assert slot["warnings"] == [
        {
            "code": "daily_limit_exceeded",
            "details": {
                "date": "2031-01-06",
                "limit_minutes": 60,
                "scheduled_minutes": 120,
            },
        }
    ]


# --- B10: the drift guard -------------------------------------------------------------


def test_every_slot_passes_the_create_path_and_the_codes_agree(
    module_seeded_url, client, cookie
):
    monday = week_of(unique_year())
    day = {n: monday + timedelta(days=n) for n in range(7)}
    subject = new_subject(module_seeded_url)
    student = new_student(module_seeded_url)
    # The tutor works every day but Tuesday; the room every day but Wednesday.
    every_day_but = lambda skip: [  # noqa: E731
        (weekday, time(0), time(23, 59)) for weekday in range(1, 8) if weekday != skip
    ]
    tutor = new_tutor(
        module_seeded_url, qualified_for=[subject], windows=every_day_but(2)
    )
    room = new_room(module_seeded_url, windows=every_day_but(3))
    setup = Setup(student=student, subject=subject, tutor=tutor, room=room)
    existing_session(
        module_seeded_url,
        state="confirmed",
        student_id=student,
        starts_at=at(day[0], "10:00"),
    )
    existing_session(
        module_seeded_url,
        state="offered",
        tutor_user_id=tutor,
        student_id=new_student(module_seeded_url),
        starts_at=at(day[0], "12:00"),
    )
    existing_session(
        module_seeded_url,
        state="unassigned",
        classroom_id=room,
        starts_at=at(day[0], "14:00"),
    )
    close_center(module_seeded_url, day[3])
    response = patch(
        client,
        cookie,
        f"/api/v1/tutors/{tutor}/availability/settings",
        {"version": 0, "daily_limit_minutes": 120, "required_break_minutes": 30},
    )
    assert code_of(response) == OK, response.text
    payload = request(setup, day[0], "11:00", "12:00", search_to=day[6], limit=10)

    slots = found(suggest(client, cookie, payload))["slots"]

    assert len(slots) == 10

    def as_create(starts_at: datetime, ends_at: datetime) -> ClassSessionCreate:
        members = {k: v for k, v in payload.items() if k not in SEARCH_FIELDS}
        return ClassSessionCreate.model_validate(
            members
            | {"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()}
        )

    async def collector(session: AsyncSession, model: ClassSessionCreate) -> Any:
        conflicts = await scheduling.find_conflicts(session, model, MX_ZONE)
        warnings = await scheduling.session_warnings(
            session, tutor, model.starts_at, model.ends_at, MX_ZONE
        )
        return [c.code for c in conflicts], [w.model_dump() for w in warnings]

    for slot in slots:
        model = as_create(
            datetime.fromisoformat(slot["starts_at"]),
            datetime.fromisoformat(slot["ends_at"]),
        )
        codes, warnings = in_uow(module_seeded_url, lambda s, m=model: collector(s, m))
        assert codes == [], slot
        assert warnings == slot["warnings"]

    # Rejected candidates, one per code: the in-memory codes equal the
    # create path's.
    samples = {
        "student_overlap": at(day[0], "10:30"),
        "tutor_overlap": at(day[0], "12:30"),
        "room_overlap": at(day[0], "14:30"),
        "tutor_unavailable": at(day[1], "10:00"),
        "room_unavailable": at(day[2], "10:00"),
        "center_closed": at(day[3], "10:00"),
        "outside_operating_hours": at(day[6], "10:00"),
    }
    search = SlotSuggestionRequest.model_validate(payload)

    async def compare(session: AsyncSession) -> list[tuple[str, list[str], list[str]]]:
        context = await scheduling.load_slot_context(
            session, search, MX_ZONE, day[0], day[6], tutor_qualified=True
        )
        rows = []
        for code, starts_at in samples.items():
            ends_at = starts_at + timedelta(hours=1)
            pure = [
                c.code
                for c in scheduling.slot_conflicts(
                    Candidate(starts_at, ends_at), context
                )
            ]
            real = [
                c.code
                for c in await scheduling.find_conflicts(
                    session, as_create(starts_at, ends_at), MX_ZONE
                )
            ]
            rows.append((code, pure, real))
        return rows

    for code, pure, real in in_uow(module_seeded_url, compare):
        assert code in pure, (code, pure)
        assert pure == real, code

    # The time-independent and the notice codes, each in its own context.
    unqualified = new_tutor(module_seeded_url, windows=every_day_but(0))
    past = date(2020, 3, 2)  # a Monday long gone: the notice is broken

    async def compare_rest(session: AsyncSession) -> list[tuple[list[str], list[str]]]:
        cases = [
            (search.model_copy(update={"tutor_user_id": unqualified}), day[0], False),
            (search, past, True),
        ]
        rows = []
        for model, on, qualified in cases:
            context = await scheduling.load_slot_context(
                session, model, MX_ZONE, on, on, tutor_qualified=qualified
            )
            starts_at = at(on, "16:00")
            ends_at = starts_at + timedelta(hours=1)
            pure = [
                c.code
                for c in scheduling.slot_conflicts(
                    Candidate(starts_at, ends_at), context
                )
            ]
            real = [
                c.code
                for c in await scheduling.find_conflicts(
                    session,
                    ClassSessionCreate.model_validate(
                        model.model_dump(mode="json", exclude=SEARCH_FIELDS)
                        | {
                            "starts_at": starts_at.isoformat(),
                            "ends_at": ends_at.isoformat(),
                        }
                    ),
                    MX_ZONE,
                )
            ]
            rows.append((pure, real))
        return rows

    [(not_qualified, not_qualified_real), (notice, notice_real)] = in_uow(
        module_seeded_url, compare_rest
    )
    assert not_qualified == not_qualified_real == ["tutor_not_qualified"]
    assert notice == notice_real == ["notice_window"]


def test_the_first_slot_can_be_created(module_seeded_url, client, cookie, setup):
    existing_session(
        module_seeded_url,
        state="confirmed",
        student_id=setup.student,
        starts_at=at(MONDAY, "10:00"),
    )
    payload = request(setup, MONDAY, "10:30", "11:30")
    [first, *_] = found(suggest(client, cookie, payload))["slots"]

    response = create(
        client,
        cookie,
        body(setup, starts_at=first["starts_at"], ends_at=first["ends_at"]),
    )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    assert local(created["starts_at"]) == "2031-01-06 11:00"
    # The route doesn't shadow the read of one session.
    assert code_of(get(client, cookie, f"{SESSIONS}/{created['id']}")) == OK


# --- B1, B2: the 422s -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"limit": 0}, [("limit", "greater_than_equal")]),
        ({"limit": 11}, [("limit", "less_than_equal")]),
        (
            {"search_from": "2031-01-08", "search_to": "2031-01-07"},
            [("search_to", "search_range_invalid")],
        ),
        (
            {"search_from": "2031-01-01", "search_to": "2031-01-15"},
            [("search_to", "search_range_too_long")],
        ),
        # A defaulted search_from (the requested date) is checked as well.
        ({"search_to": "2031-01-05"}, [("search_to", "search_range_invalid")]),
        ({"search_to": "2031-01-20"}, [("search_to", "search_range_too_long")]),
        (
            {"override": {"codes": ["notice_window"], "reason": "No"}},
            [("override", "extra_forbidden")],
        ),
    ],
)
def test_invalid_searches_are_422(client, cookie, setup, fields, expected):
    payload = request(setup, MONDAY, "10:00", "11:00") | fields

    assert field_errors(suggest(client, cookie, payload)) == expected


def test_fourteen_dates_are_allowed(client, cookie, setup):
    payload = request(
        setup,
        MONDAY,
        "10:00",
        "11:00",
        search_from="2031-01-01",
        search_to="2031-01-14",
    )

    result = found(suggest(client, cookie, payload))

    assert (result["search_from"], result["search_to"]) == ("2031-01-01", "2031-01-14")


def test_the_bounds_and_references_are_the_creates(client, cookie, setup):
    backwards = request(setup, MONDAY, "11:00", "10:00")
    unknown = request(
        None,
        MONDAY,
        "10:00",
        "11:00",
        student_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        tutor_user_id=uuid.uuid4(),
        classroom_id=uuid.uuid4(),
    )

    assert field_errors(suggest(client, cookie, backwards)) == [
        ("ends_at", "ends_before_start")
    ]
    assert field_errors(suggest(client, cookie, unknown)) == [
        ("student_id", "student_unknown"),
        ("subject_id", "subject_unknown"),
        ("tutor_user_id", "tutor_unknown"),
        ("classroom_id", "classroom_unknown"),
    ]


def test_a_get_is_405(client, cookie):
    response = get(client, cookie, SUGGESTIONS)

    assert response.status_code == 405


# --- B8: the statement budget ---------------------------------------------------------


def test_at_most_20_statements_whatever_the_window(module_seeded_url, setup):
    existing_session(
        module_seeded_url,
        state="confirmed",
        student_id=setup.student,
        starts_at=at(MONDAY, "10:00"),
    )

    def run(last: date) -> tuple[int, Any]:
        model = SlotSuggestionRequest.model_validate(
            request(setup, MONDAY, "10:30", "11:30", search_to=last, limit=10)
        )
        return count_statements(
            module_seeded_url, lambda session: scheduling.suggest_slots(session, model)
        )

    one_day, short = run(MONDAY)
    two_weeks, long = run(MONDAY + timedelta(days=13))

    assert one_day == two_weeks <= 20
    assert len(short.slots) == len(long.slots) == 10
