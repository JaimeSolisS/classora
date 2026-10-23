"""The calendar's range read (#57 A, E1): ``GET /api/v1/class-sessions``
with real logins through the real app.

The tests share one seeded database per module. Each test reads its own
week (the first Monday of a ``unique_year()``), so the sessions of other
tests and the global closures never show up. The center zone is the default
America/Mexico_City (UTC-6 all year): a session at 20:00 local is on the
next UTC date, which is what the local-date marker cases need.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Closure
from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.schemas import ClassSessionRangeQuery
from tests.conftest import frozen_clock
from tests.factories.families import persist_student
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_classroom,
    persist_subject,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post, unique_year
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import create_series, series_body, timeline
from tests.integration.test_class_session_create import (
    new_student,
    new_subject,
    new_tutor,
    revoke_all,
)

SESSIONS = "/api/v1/class-sessions"
MX = ZoneInfo("America/Mexico_City")
INVALID = (422, "validation_error")


def first_monday() -> date:
    """The first Monday of a year no other test uses."""
    day = date(unique_year(), 1, 1)
    return day + timedelta(days=(7 - day.weekday()) % 7)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=MX)


def z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def stored(
    url: str,
    starts_at: datetime,
    *,
    minutes: int = 60,
    state: str = "confirmed",
    students: tuple[uuid.UUID, ...] = (),
    **overrides: Any,
) -> uuid.UUID:
    """A stored session (``persist_class_session``) with these participants."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await persist_class_session(
            session,
            state=state,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            scheduled_minutes=minutes,
            **overrides,
        )
        for student_id in students:
            await add_participant(session, made, student_id)
        return made.id

    return in_uow(url, _make)


def new_room(url: str) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_classroom(session)).id

    return in_uow(url, _make)


def close(url: str, starts_on: date, ends_on: date | None = None) -> None:
    async def _add(session: AsyncSession) -> None:
        session.add(
            Closure(
                name="Día festivo",
                closure_type="holiday",
                starts_on=starts_on,
                ends_on=ends_on or starts_on,
            )
        )

    in_uow(url, _add)


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


def read(
    client: TestClient, cookie: Any, start: datetime, end: datetime, **params: Any
) -> Response:
    query = {"from": z(start), "to": z(end)}
    for key, value in params.items():
        query[key] = str(value).lower() if isinstance(value, bool) else str(value)
    return get(client, cookie, SESSIONS, **query)


def ids_of(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def week_of(day: date) -> tuple[datetime, datetime]:
    return at(day, "00:00"), at(day + timedelta(days=7), "00:00")


# --- A3: the overlap rule ------------------------------------------------------------


def test_the_overlap_boundaries(module_seeded_url, client, cookie):
    monday = first_monday()
    start, end = at(monday, "10:00"), at(monday, "18:00")
    ends_at_from = stored(module_seeded_url, at(monday, "09:00"))
    starts_at_to = stored(module_seeded_url, at(monday, "18:00"))
    crosses_from = stored(module_seeded_url, at(monday, "09:30"))
    crosses_to = stored(module_seeded_url, at(monday, "17:30"))
    inside = stored(module_seeded_url, at(monday, "12:00"))

    found = ids_of(read(client, cookie, start, end))

    assert found == [str(crosses_from), str(inside), str(crosses_to)]
    assert str(ends_at_from) not in found and str(starts_at_to) not in found


def test_a_480_minute_session_starting_before_from_is_found(
    module_seeded_url, client, cookie
):
    monday = first_monday()
    long_one = stored(module_seeded_url, at(monday, "06:00"), minutes=480)

    found = ids_of(read(client, cookie, at(monday, "13:59"), at(monday, "15:00")))

    assert found == [str(long_one)]


def test_items_are_ordered_by_start_then_id(module_seeded_url, client, cookie):
    monday = first_monday()
    later = stored(module_seeded_url, at(monday + timedelta(days=1), "08:00"))
    same = sorted(str(stored(module_seeded_url, at(monday, "08:00"))) for _ in range(3))

    found = ids_of(read(client, cookie, *week_of(monday)))

    assert found == [*same, str(later)]


def test_cancelled_sessions_only_with_include_cancelled(
    module_seeded_url, client, cookie
):
    monday = first_monday()
    kept = stored(module_seeded_url, at(monday, "08:00"))
    cancelled = stored(module_seeded_url, at(monday, "10:00"), state="cancelled")
    skipped = stored(
        module_seeded_url, at(monday, "12:00"), state="skipped_closure", tutor=False
    )
    conflict = stored(module_seeded_url, at(monday, "14:00"), state="conflict")

    default = ids_of(read(client, cookie, *week_of(monday)))
    with_cancelled = ids_of(
        read(client, cookie, *week_of(monday), include_cancelled=True)
    )
    without = ids_of(read(client, cookie, *week_of(monday), include_cancelled=False))

    assert default == without == [str(kept), str(skipped), str(conflict)]
    assert with_cancelled == [str(kept), str(cancelled), str(skipped), str(conflict)]


# --- A3: filters ---------------------------------------------------------------------


def test_each_filter_alone_and_combined(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = first_monday()
    student, other_student = new_student(url), new_student(url)
    tutor = new_tutor(url)
    room = new_room(url)
    match = stored(
        url,
        at(monday, "08:00"),
        students=(student,),
        tutor_user_id=tutor,
        classroom_id=room,
        state="offered",
    )
    other_tutor = stored(url, at(monday, "10:00"), students=(student,))
    other_room = stored(
        url, at(monday, "12:00"), students=(other_student,), tutor_user_id=tutor
    )
    group = stored(url, at(monday, "14:00"), kind="group", classroom_id=room)
    week = week_of(monday)

    assert ids_of(read(client, cookie, *week, tutor_user_id=tutor)) == [
        str(match),
        str(other_room),
    ]
    assert ids_of(read(client, cookie, *week, student_id=student)) == [
        str(match),
        str(other_tutor),
    ]
    assert ids_of(read(client, cookie, *week, student_id=other_student)) == [
        str(other_room)
    ]
    assert ids_of(read(client, cookie, *week, classroom_id=room)) == [
        str(match),
        str(group),
    ]
    assert ids_of(read(client, cookie, *week, kind="group")) == [str(group)]
    assert ids_of(read(client, cookie, *week, kind="individual")) == [
        str(match),
        str(other_tutor),
        str(other_room),
    ]
    assert ids_of(
        read(
            client,
            cookie,
            *week,
            tutor_user_id=tutor,
            student_id=student,
            classroom_id=room,
            kind="individual",
        )
    ) == [str(match)]


@pytest.mark.parametrize("field", ["tutor_user_id", "student_id", "classroom_id"])
def test_an_unknown_filter_id_matches_nothing(module_seeded_url, client, cookie, field):
    monday = first_monday()
    stored(module_seeded_url, at(monday, "08:00"))

    response = read(client, cookie, *week_of(monday), **{field: uuid.uuid4()})

    assert response.status_code == 200
    assert response.json() == {"items": [], "truncated": False}


# --- A1, A2: 422 ---------------------------------------------------------------------


def field_errors(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(str(e["path"][-1]), e["code"]) for e in response.json()["errors"]]


def test_from_and_to_are_required(client, cookie):
    assert code_of(get(client, cookie, SESSIONS)) == INVALID
    assert code_of(get(client, cookie, SESSIONS, to="2031-01-06T00:00:00Z")) == INVALID
    assert (
        code_of(get(client, cookie, SESSIONS, **{"from": "2031-01-06T00:00:00Z"}))
        == INVALID
    )


@pytest.mark.parametrize("field", ["from", "to"])
def test_a_naive_instant_is_refused_on_its_field(client, cookie, field):
    params = {"from": "2031-01-06T00:00:00Z", "to": "2031-01-07T00:00:00Z"}
    params[field] = params[field].removesuffix("Z")

    response = get(client, cookie, SESSIONS, **params)

    assert field_errors(response) == [(field, "naive_datetime")]


@pytest.mark.parametrize(
    "to", ["2031-01-06T00:00:00Z", "2031-01-05T23:59:00Z", "2031-01-05T18:00:00-06:00"]
)
def test_to_must_be_after_from(client, cookie, to):
    response = get(
        client, cookie, SESSIONS, **{"from": "2031-01-06T00:00:00Z", "to": to}
    )

    assert field_errors(response) == [("to", "range_invalid")]


def test_the_range_is_at_most_45_days(client, cookie):
    start = datetime(2031, 1, 6, tzinfo=UTC)

    ok = read(client, cookie, start, start + timedelta(days=45))
    too_long = read(client, cookie, start, start + timedelta(days=45, minutes=1))

    assert ok.status_code == 200
    assert field_errors(too_long) == [("to", "range_too_long")]


@pytest.mark.parametrize(
    "extra",
    [
        {"state": "confirmed"},
        {"tutor_user_id": "not-a-uuid"},
        {"student_id": "1"},
        {"classroom_id": "x"},
        {"kind": "workshop"},
        {"include_cancelled": "maybe"},
        {"from": "tomorrow"},
    ],
)
def test_schema_errors_are_validation_errors(client, cookie, extra):
    params = {"from": "2031-01-06T00:00:00Z", "to": "2031-01-07T00:00:00Z"} | extra

    assert code_of(get(client, cookie, SESSIONS, **params)) == INVALID


# --- A4: the item and truncation -----------------------------------------------------


def test_the_item_shape(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = first_monday()
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject], display_name="Ana Tutora")
    room = new_room(url)
    zoe, bea = new_student(url), new_student(url)
    session_id = stored(
        url,
        at(monday, "10:00"),
        state="offered",
        subject_id=subject,
        tutor_user_id=tutor,
        classroom_id=room,
        students=(zoe, bea),
    )
    unassigned = stored(url, at(monday, "12:00"), state="unassigned", tutor=False)

    [item, bare] = read(client, cookie, *week_of(monday)).json()["items"]
    detail = get(client, cookie, f"{SESSIONS}/{session_id}").json()

    assert set(item) == {
        "id",
        "kind",
        "state",
        "version",
        "series_id",
        "starts_at",
        "ends_at",
        "subject",
        "tutor",
        "classroom",
        "participants",
        "markers",
    }
    assert item["id"] == str(session_id)
    assert (item["kind"], item["state"]) == ("individual", "offered")
    assert (item["version"], item["series_id"]) == (detail["version"], None)
    assert item["starts_at"] == z(at(monday, "10:00"))
    assert item["ends_at"] == z(at(monday, "11:00"))
    assert item["subject"] == {
        k: detail["subject"][k] for k in ("id", "name", "education_level_name")
    }
    assert item["tutor"] == {"id": str(tutor), "display_name": "Ana Tutora"}
    assert item["classroom"] == {
        "id": str(room),
        "name": detail["classroom"]["name"],
    }
    assert item["participants"] == [
        {"student_id": p["student_id"], "display_name": p["display_name"]}
        for p in detail["participants"]
    ]
    assert len(item["participants"]) == 2
    assert item["markers"] == []
    assert bare["id"] == str(unassigned)
    assert (bare["tutor"], bare["classroom"], bare["participants"]) == (None, None, [])


def test_more_than_1000_matches_are_truncated(module_seeded_url, client, cookie):
    monday = first_monday()
    starts_at = at(monday, "08:00")

    async def _bulk(session: AsyncSession) -> None:
        subject = await persist_subject(session)
        session.add_all(
            ClassSession(
                kind="individual",
                state="unassigned",
                subject_id=subject.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=60),
                scheduled_minutes=60,
            )
            for _ in range(1001)
        )
        await session.flush()

    in_uow(module_seeded_url, _bulk)

    body = read(client, cookie, *week_of(monday)).json()

    assert len(body["items"]) == 1000
    assert body["truncated"] is True
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids)


# --- A5: markers ---------------------------------------------------------------------


def markers(client: TestClient, cookie: Any, day: date) -> dict[str, list[str]]:
    body = read(client, cookie, *week_of(day), include_cancelled=True).json()
    return {item["id"]: item["markers"] for item in body["items"]}


def test_center_closed_on_the_local_date(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = first_monday()
    tuesday = monday + timedelta(days=1)
    close(url, tuesday)
    states = ["unassigned", "offered", "confirmed"]
    marked = [
        stored(url, at(tuesday, f"{8 + i:02d}:00"), state=state)
        for i, state in enumerate(states)
    ]
    unmarked = [
        stored(url, at(tuesday, f"{12 + i:02d}:00"), state=state)
        for i, state in enumerate(["cancelled", "completed", "conflict"])
    ]
    # Monday 20:00 local is Tuesday 02:00 UTC: not on the closure.
    monday_evening = stored(url, at(monday, "20:00"))
    # Tuesday 20:00 local is Wednesday 02:00 UTC: on the closure.
    tuesday_evening = stored(url, at(tuesday, "20:00"))

    found = markers(client, cookie, monday)

    # The stored tutors hold no qualification: only the closure marker counts.
    for session_id in [*marked, tuesday_evening]:
        assert "center_closed" in found[str(session_id)]
    for session_id in [*unmarked, monday_evening]:
        assert "center_closed" not in found[str(session_id)]


def test_a_multi_day_closure_marks_each_of_its_dates(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = first_monday()
    close(url, monday + timedelta(days=2), monday + timedelta(days=3))
    days = [
        stored(url, at(monday + timedelta(days=d), "09:00"), state="unassigned")
        for d in range(5)
    ]

    found = markers(client, cookie, monday)

    assert [found[str(d)] for d in days] == [
        [],
        [],
        ["center_closed"],
        ["center_closed"],
        [],
    ]


def test_tutor_not_qualified_for_future_offered_or_confirmed(
    module_seeded_url, client, cookie
):
    url = module_seeded_url
    monday = first_monday()
    subject = new_subject(url)
    revoked = new_tutor(url, qualified_for=[subject])
    revoke_all(url, revoked)
    qualified = new_tutor(url, qualified_for=[subject])
    never = new_tutor(url)

    confirmed = stored(
        url, at(monday, "08:00"), subject_id=subject, tutor_user_id=revoked
    )
    offered = stored(
        url,
        at(monday, "10:00"),
        state="offered",
        subject_id=subject,
        tutor_user_id=never,
    )
    fine = stored(url, at(monday, "12:00"), subject_id=subject, tutor_user_id=qualified)
    completed = stored(
        url,
        at(monday, "14:00"),
        state="completed",
        subject_id=subject,
        tutor_user_id=revoked,
    )
    unassigned = stored(
        url, at(monday, "16:00"), state="unassigned", subject_id=subject, tutor=False
    )
    conflict = stored(
        url,
        at(monday, "18:00"),
        state="conflict",
        subject_id=subject,
        tutor_user_id=never,
    )

    found = markers(client, cookie, monday)

    assert found[str(confirmed)] == ["tutor_not_qualified"]
    assert found[str(offered)] == ["tutor_not_qualified"]
    for session_id in (fine, completed, unassigned, conflict):
        assert found[str(session_id)] == []


def test_no_tutor_marker_in_the_past(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = date(2020, 1, 6)
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject])
    revoke_all(url, tutor)
    past = stored(url, at(monday, "08:00"), subject_id=subject, tutor_user_id=tutor)

    assert markers(client, cookie, monday)[str(past)] == []


def test_both_markers_come_in_the_fixed_order(module_seeded_url, client, cookie):
    url = module_seeded_url
    monday = first_monday()
    close(url, monday)
    both = stored(url, at(monday, "09:00"), tutor_user_id=new_tutor(url))

    assert markers(client, cookie, monday)[str(both)] == [
        "center_closed",
        "tutor_not_qualified",
    ]


# --- #58 B2: the overridden marker ---------------------------------------------------


def add_override(url: str, session_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """One #49 override row (and its event) on the session."""
    conflict = scheduling.Conflict(
        "outside_operating_hours", {"date": "2031-01-12", "opens_at": None}
    )

    async def _add(session: AsyncSession) -> None:
        await scheduling.record_session_override(
            session, session_id, [conflict], "Lo pidió la familia", actor_id=actor_id
        )

    in_uow(url, _add, actor_id=actor_id)


@pytest.mark.parametrize(
    "state",
    ["unassigned", "offered", "confirmed", "completed", "cancelled", "conflict"],
)
def test_overridden_in_any_state(module_seeded_url, client, cookie, owner, state):
    url = module_seeded_url
    monday = first_monday()
    marked = stored(url, at(monday, "10:00"), state=state)
    plain = stored(url, at(monday, "12:00"), state=state)
    add_override(url, marked, owner.id)

    found = markers(client, cookie, monday)

    # Offered and confirmed ones also get tutor_not_qualified (the factory's
    # tutor holds no qualification); overridden always comes last.
    assert found[str(marked)][-1] == "overridden"
    assert found[str(marked)][:-1] == found[str(plain)]
    assert "overridden" not in found[str(plain)]


def test_overridden_comes_after_the_other_markers(
    module_seeded_url, client, cookie, owner
):
    url = module_seeded_url
    monday = first_monday()
    close(url, monday)
    every = stored(url, at(monday, "09:00"), tutor_user_id=new_tutor(url))
    add_override(url, every, owner.id)
    add_override(url, every, owner.id)

    assert markers(client, cookie, monday)[str(every)] == [
        "center_closed",
        "tutor_not_qualified",
        "overridden",
    ]


def test_an_occurrence_has_its_series_id(module_seeded_url, client):
    # "Now" is the Sunday before the series' first Monday, so the horizon
    # holds the occurrence (series_support's timeline).
    url = module_seeded_url
    tl = timeline()
    payload = series_body(
        student=new_student(url),
        subject=new_subject(url),
        starts_on=tl.monday,
        start_time="16:00",
        weekdays=[1],
        count=1,
    )
    with frozen_clock(tl.now):
        owner_cookie = sign_in(client, create_user(url, roles=(OWNER,)).username)
        created = create_series(client, owner_cookie, payload)
        assert code_of(created) == (201, None), created.text
        [item] = read(client, owner_cookie, *week_of(tl.monday)).json()["items"]

    assert item["series_id"] == created.json()["id"]
    assert item["version"] == 1


# --- A6: the statement budget --------------------------------------------------------


def populate(url: str, monday: date, count: int) -> None:
    actor_id = create_user(url, roles=(OWNER,)).id
    subject = new_subject(url)
    tutors = [
        new_tutor(url, qualified_for=[subject] if i % 2 else []) for i in range(3)
    ]
    close(url, monday + timedelta(days=1))

    async def _make(session: AsyncSession) -> None:
        rooms = [await persist_classroom(session) for _ in range(3)]
        for i in range(count):
            made = await persist_class_session(
                session,
                state=["confirmed", "offered", "unassigned"][i % 3],
                subject_id=subject,
                tutor_user_id=None if i % 3 == 2 else tutors[i % 3],
                classroom_id=rooms[i % 3].id,
                starts_at=at(monday + timedelta(days=i % 5), f"{7 + i // 5:02d}:00"),
                ends_at=at(monday + timedelta(days=i % 5), f"{7 + i // 5:02d}:45"),
                scheduled_minutes=45,
            )
            for _ in range(2):
                student = await persist_student(session, contacts=0)
                await add_participant(session, made, student.id)
            if i % 2 == 0:
                # #58 B2: the overridden marker costs no statement.
                await scheduling.record_session_override(
                    session,
                    made.id,
                    [scheduling.Conflict("center_closed", {"date": "2031-01-07"})],
                    "Presupuesto",
                    actor_id=actor_id,
                )

    in_uow(url, _make, actor_id=actor_id)


@pytest.mark.parametrize("count", [1, 30])
def test_the_read_takes_at_most_ten_statements(module_seeded_url, count):
    monday = first_monday()
    populate(module_seeded_url, monday, count)
    start, end = week_of(monday)
    query = ClassSessionRangeQuery.model_validate({"from": start, "to": end})

    statements, calendar = count_statements(
        module_seeded_url,
        lambda session: scheduling.list_calendar_sessions(session, query),
    )

    assert len(calendar.items) == count
    assert statements <= 10
    marked = [item for item in calendar.items if "overridden" in item.markers]
    assert len(marked) == (count + 1) // 2


def test_an_empty_range_takes_one_statement(module_seeded_url):
    start, end = week_of(first_monday())
    query = ClassSessionRangeQuery.model_validate({"from": start, "to": end})

    statements, calendar = count_statements(
        module_seeded_url,
        lambda session: scheduling.list_calendar_sessions(session, query),
    )

    assert calendar.items == []
    assert statements == 1


# --- A7, A8: access and routing ------------------------------------------------------


def test_the_collection_route_shadows_no_other_route(module_seeded_url, client, cookie):
    monday = first_monday()
    session_id = stored(module_seeded_url, at(monday, "09:00"))

    listed = read(client, cookie, *week_of(monday))
    one = get(client, cookie, f"{SESSIONS}/{session_id}")
    missing = get(client, cookie, f"{SESSIONS}/{uuid.uuid4()}")
    created = post(client, cookie, SESSIONS, {})

    assert ids_of(listed) == [str(session_id)]
    assert (one.status_code, one.json()["id"]) == (200, str(session_id))
    assert code_of(missing) == (404, "not_found")
    assert code_of(created) == INVALID


def test_a_tutor_is_denied(module_seeded_url, client):
    tutor = create_user(module_seeded_url, roles=("tutor",))
    tutor_cookie = sign_in(client, tutor.username)

    response = read(
        client,
        tutor_cookie,
        datetime(2031, 1, 6, tzinfo=UTC),
        datetime(2031, 1, 7, tzinfo=UTC),
    )

    assert code_of(response) == (403, "permission_denied")
