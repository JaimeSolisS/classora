"""Available slots for families (#59 B1-B7; H2) with real logins through the
real app (helpers: ``session_request_support``).

Each test makes its own subject (so only its tutors are candidates), its own
student and family; closures are global, so they use their own dates.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.identity.models import User
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.family_slots import family_slot_ok
from app.modules.scheduling.request_schemas import AvailableSlotsQuery
from app.modules.scheduling.schemas import ClassSessionSlot
from app.modules.scheduling.suggestions import Candidate, OperatingDay
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import count_statements
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.session_request_support import (
    MONDAY_WINDOW,
    NOW,
    TUESDAY,
    Family,
    get_slots,
    guardian_with_child,
    slot_starts,
    slots_params,
)
from tests.integration.test_class_session_create import (
    MONDAY,
    MX,
    at,
    change,
    close_center,
    existing_session,
    new_student,
    new_subject,
    new_tutor,
    revoke_all,
    time_off,
)

INVALID = (422, "validation_error")


@pytest.fixture(scope="module", autouse=True)
def frozen() -> Iterator[None]:
    with frozen_clock(NOW):
        yield


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def family(module_seeded_url: str) -> Family:
    return guardian_with_child(module_seeded_url)


@pytest.fixture
def cookie(client: TestClient, family: Family) -> Any:
    return sign_in(client, family.user.username)


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


def field_errors(response: Any) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"][-1], e["code"]) for e in response.json()["errors"]]


def guardian_principal(family: Family) -> Principal:
    return Principal(
        user_id=family.user.id, roles=frozenset({"guardian"}), permissions=frozenset()
    )


# --- B6: the response ----------------------------------------------------------------


def test_a_guardian_sees_the_free_times_of_a_linked_child(
    module_seeded_url, client, cookie, family, subject
):
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)

    response = get_slots(
        client, cookie, slots_params(family.student_id, subject, MONDAY, TUESDAY)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "time_zone",
        "student_id",
        "subject_id",
        "duration_minutes",
        "from",
        "to",
        "days",
    }
    assert body["time_zone"] == "America/Mexico_City"
    assert body["from"] == MONDAY.isoformat() and body["to"] == TUESDAY.isoformat()
    assert [day["date"] for day in body["days"]] == [
        MONDAY.isoformat(),
        TUESDAY.isoformat(),
    ]
    assert slot_starts(response, MONDAY) == ["10:00", "10:30", "11:00"]
    assert slot_starts(response, TUESDAY) == []
    first = body["days"][0]["slots"][0]
    assert set(first) == {"starts_at", "ends_at", "already_requested"}
    assert datetime.fromisoformat(first["starts_at"]) == at(MONDAY, "10:00")
    assert datetime.fromisoformat(first["ends_at"]) == at(MONDAY, "11:00")
    assert first["already_requested"] is False


def test_lengths_change_the_candidates(
    module_seeded_url, client, cookie, family, subject
):
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)

    def starts(minutes: int) -> list[str]:
        return slot_starts(
            get_slots(
                client,
                cookie,
                slots_params(family.student_id, subject, duration=minutes),
            )
        )

    assert starts(30) == ["10:00", "10:30", "11:00", "11:30"]
    assert starts(90) == ["10:00", "10:30"]
    assert starts(120) == ["10:00"]


def test_a_student_sees_their_own_slots(module_seeded_url, client, subject):
    from tests.integration.session_request_support import student_account

    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    account, student = student_account(module_seeded_url)
    cookie = sign_in(client, account.username)

    response = get_slots(client, cookie, slots_params(student, subject))

    assert slot_starts(response) == ["10:00", "10:30", "11:00"]


# --- B1, B2: validation and order ----------------------------------------------------


def test_anonymous_is_401(client, family, subject):
    client.cookies.clear()
    response = client.get(
        "/api/v1/available-slots", params=slots_params(family.student_id, subject)
    )

    assert code_of(response) == (401, "authentication_required")


@pytest.mark.parametrize(
    ("change_params", "expected"),
    [
        ({"duration": 50}, "literal_error"),
        ({"duration": "sixty"}, "literal_error"),
        ({"student_id": "nope"}, "uuid_parsing"),
        ({"extra": "1"}, "extra_forbidden"),
    ],
)
def test_the_schema_is_422(client, cookie, family, subject, change_params, expected):
    params = slots_params(family.student_id, subject)
    if "duration" in change_params:
        params["duration_minutes"] = change_params["duration"]
    elif "student_id" in change_params:
        params["student_id"] = change_params["student_id"]
    else:
        params.update(change_params)

    response = get_slots(client, cookie, params)

    assert code_of(response) == INVALID, response.text
    assert expected in {e["code"] for e in response.json()["errors"]}


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        (TUESDAY, MONDAY, [("to", "range_invalid")]),
        (MONDAY, MONDAY + timedelta(days=14), [("to", "range_too_long")]),
        (NOW.date() - timedelta(days=1), NOW.date(), [("from", "range_in_past")]),
        (
            NOW.date() + timedelta(days=55),
            NOW.date() + timedelta(days=61),
            [("to", "range_too_far")],
        ),
    ],
    ids=["invalid", "too-long", "in-past", "too-far"],
)
def test_range_rules_are_422(client, cookie, family, subject, first, last, expected):
    response = get_slots(
        client, cookie, slots_params(family.student_id, subject, first, last)
    )

    assert field_errors(response) == expected


def test_today_up_to_60_days_and_14_dates_are_accepted(client, cookie, family, subject):
    today = NOW.date()

    assert (
        get_slots(
            client,
            cookie,
            slots_params(family.student_id, subject, today, today + timedelta(days=13)),
        ).status_code
        == 200
    )
    assert (
        get_slots(
            client,
            cookie,
            slots_params(
                family.student_id,
                subject,
                today + timedelta(days=60),
                today + timedelta(days=60),
            ),
        ).status_code
        == 200
    )


def test_the_range_comes_before_the_record_policy(client, cookie, subject):
    response = get_slots(
        client, cookie, slots_params(uuid.uuid4(), subject, TUESDAY, MONDAY)
    )

    assert field_errors(response) == [("to", "range_invalid")]


def test_the_record_policy_comes_before_the_references(client, cookie):
    response = get_slots(client, cookie, slots_params(uuid.uuid4(), uuid.uuid4()))

    assert code_of(response) == (404, "not_found")


def test_reference_errors_come_together(module_seeded_url, client):
    family = guardian_with_child(module_seeded_url, student_active=False)
    cookie = sign_in(client, family.user.username)

    response = get_slots(client, cookie, slots_params(family.student_id, uuid.uuid4()))

    assert field_errors(response) == [
        ("student_id", "student_inactive"),
        ("subject_id", "subject_unknown"),
    ]


def test_an_inactive_subject_is_422(module_seeded_url, client, cookie, family):
    subject = new_subject(module_seeded_url, is_active=False)

    response = get_slots(client, cookie, slots_params(family.student_id, subject))

    assert field_errors(response) == [("subject_id", "subject_inactive")]


def test_nothing_is_written(module_seeded_url, client, cookie, family, subject):
    from tests.integration.conftest import run_sql

    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    query = (
        "SELECT (SELECT count(*) FROM audit_events) AS a,"
        " (SELECT count(*) FROM session_requests) AS r,"
        " (SELECT count(*) FROM class_sessions) AS s"
    )
    [before] = run_sql(module_seeded_url, query)

    assert (
        get_slots(client, cookie, slots_params(family.student_id, subject)).status_code
        == 200
    )
    [after] = run_sql(module_seeded_url, query)
    assert tuple(after) == tuple(before)


# --- B4: each reason -----------------------------------------------------------------


def test_the_students_sessions_hide_overlapping_slots(
    module_seeded_url, client, cookie, family, subject
):
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        minutes=30,
        state="unassigned",
        student_id=family.student_id,
    )

    assert slot_starts(
        get_slots(client, cookie, slots_params(family.student_id, subject))
    ) == ["10:30", "11:00"]


def test_sessions_that_hold_nothing_hide_nothing(
    module_seeded_url, client, cookie, family, subject
):
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    for state in ("cancelled", "conflict"):
        existing_session(
            module_seeded_url,
            starts_at=at(MONDAY, "10:00"),
            state=state,
            student_id=family.student_id,
            **(
                {"cancelled_at": NOW, "cancellation_reason": "Motivo"}
                if state == "cancelled"
                else {}
            ),
        )

    assert slot_starts(
        get_slots(client, cookie, slots_params(family.student_id, subject))
    ) == ["10:00", "10:30", "11:00"]


def test_one_free_tutor_is_enough_and_none_is_named(
    module_seeded_url, client, cookie, family, subject
):
    booked = new_tutor(
        module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW
    )
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        minutes=120,
        state="confirmed",
        tutor_user_id=booked,
    )
    free = new_tutor(
        module_seeded_url,
        qualified_for=[subject],
        windows=[(1, time(11, 0), time(12, 0))],
    )

    response = get_slots(client, cookie, slots_params(family.student_id, subject))

    assert slot_starts(response) == ["11:00"]
    assert str(free) not in response.text and str(booked) not in response.text


def test_a_closure_date_has_no_slots(
    module_seeded_url, client, cookie, family, subject
):
    day = date(2031, 2, 3)  # a Monday, used only here
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    close_center(module_seeded_url, day)

    response = get_slots(
        client,
        cookie,
        slots_params(family.student_id, subject, day, day + timedelta(days=7)),
    )

    assert slot_starts(response, day) == []
    assert slot_starts(response, day + timedelta(days=7)) == ["10:00", "10:30", "11:00"]


# --- B5: the statement budget --------------------------------------------------------


@pytest.mark.parametrize("tutors", [1, 6])
@pytest.mark.parametrize("days", [1, 14])
def test_the_search_takes_at_most_14_statements(
    module_seeded_url, family, tutors, days
):
    subject = new_subject(module_seeded_url)
    for _ in range(tutors):
        new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    query = AvailableSlotsQuery.model_validate(
        slots_params(
            family.student_id, subject, MONDAY, MONDAY + timedelta(days=days - 1)
        )
    )

    async def call(session: AsyncSession) -> Any:
        return await scheduling.available_slots(
            session, guardian_principal(family), query
        )

    statements, result = count_statements(module_seeded_url, call)

    assert statements <= 14
    assert len(result.days) == days
    assert [s.starts_at for s in result.days[0].slots] == [
        at(MONDAY, "10:00"),
        at(MONDAY, "10:30"),
        at(MONDAY, "11:00"),
    ]


# --- B7: the drift guard -------------------------------------------------------------


def _check(
    url: str, student: uuid.UUID, subject: uuid.UUID, starts_at: datetime
) -> tuple[bool, set[str], int]:
    """``(family_slot_ok, the collector's codes without tutor or room, the
    number of suggest_tutors items)`` for a 60-minute slot, in one Unit of
    Work."""
    ends_at = starts_at + timedelta(hours=1)
    local_date = starts_at.astimezone(MX).date()

    async def _run(session: AsyncSession) -> tuple[bool, set[str], int]:
        settings = await administration.get_business_settings(session)
        context = await scheduling.load_family_context(
            session,
            zone=settings.time_zone,
            operating_hours=[
                OperatingDay(i.weekday, i.opens_at, i.closes_at)
                for i in settings.operating_hours
            ],
            student_id=student,
            subject_id=subject,
            first=local_date,
            last=local_date,
        )
        ok = family_slot_ok(Candidate(starts_at, ends_at), context)
        body = ClassSessionSlot(
            kind="individual",
            student_id=student,
            subject_id=subject,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        codes = {
            c.code
            for c in await scheduling.find_conflicts(session, body, settings.time_zone)
        }
        tutors = await scheduling.suggest_tutors(
            session, subject_id=subject, starts_at=starts_at, ends_at=ends_at
        )
        return ok, codes, len(tutors.items)

    return in_uow(url, _run)


SLOT_CODES = {
    "student_overlap",
    "center_closed",
    "outside_operating_hours",
    "notice_window",
}


def test_every_returned_slot_passes_the_collector_and_has_a_tutor(
    module_seeded_url, client, cookie, family, subject
):
    new_tutor(
        module_seeded_url,
        qualified_for=[subject],
        windows=[(day, time(9, 0), time(20, 0)) for day in range(1, 7)],
    )
    response = get_slots(
        client,
        cookie,
        slots_params(family.student_id, subject, MONDAY, MONDAY + timedelta(days=6)),
    )
    assert response.status_code == 200
    slots = [
        datetime.fromisoformat(slot["starts_at"])
        for day in response.json()["days"]
        for slot in day["slots"]
    ]
    assert len(slots) > 50

    for starts_at in slots[:: max(1, len(slots) // 12)]:
        ok, codes, tutors = _check(
            module_seeded_url, family.student_id, subject, starts_at
        )
        assert ok and not (codes & SLOT_CODES) and tutors >= 1, starts_at


def _setup(url: str, **tutor: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    subject = new_subject(url)
    student = new_student(url)
    tutor_id = new_tutor(url, qualified_for=[subject], **tutor)
    return subject, student, tutor_id


def test_rejections_agree_with_the_collector(module_seeded_url):
    # notice: 10:00 tomorrow is 22 hours ahead.
    subject, student, _ = _setup(module_seeded_url)
    soon = at(NOW.date() + timedelta(days=1), "10:00")
    ok, codes, _ = _check(module_seeded_url, student, subject, soon)
    assert not ok and "notice_window" in codes
    # hours: Monday 08:00 is before opening; Sunday is closed.
    for starts_at in (at(MONDAY, "08:00"), at(MONDAY + timedelta(days=6), "10:00")):
        ok, codes, _ = _check(module_seeded_url, student, subject, starts_at)
        assert not ok and "outside_operating_hours" in codes
    # closure
    day = date(2031, 2, 10)
    close_center(module_seeded_url, day)
    ok, codes, _ = _check(module_seeded_url, student, subject, at(day, "10:00"))
    assert not ok and "center_closed" in codes
    # student overlap
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:30"),
        state="confirmed",
        tutor_user_id=new_tutor(module_seeded_url),
        student_id=student,
    )
    ok, codes, _ = _check(module_seeded_url, student, subject, at(MONDAY, "10:00"))
    assert not ok and "student_overlap" in codes


def _inactive(url: str, user_id: uuid.UUID) -> None:
    async def _change(session: AsyncSession) -> None:
        await change(session, User, user_id, state="suspended")

    in_uow(url, _change)


@pytest.mark.parametrize(
    "reason",
    ["unqualified", "revoked", "no_windows", "time_off", "inactive", "booked"],
)
def test_no_eligible_tutor_agrees_with_suggest_tutors(module_seeded_url, reason):
    subject, student, tutor = _setup(
        module_seeded_url, **({"windows": []} if reason == "no_windows" else {})
    )
    starts_at = at(MONDAY, "10:00")
    if reason == "unqualified":
        subject = new_subject(module_seeded_url)
    elif reason == "revoked":
        revoke_all(module_seeded_url, tutor)
    elif reason == "time_off":
        time_off(module_seeded_url, tutor, MONDAY)
    elif reason == "inactive":
        _inactive(module_seeded_url, tutor)
    elif reason == "booked":
        existing_session(
            module_seeded_url, starts_at=starts_at, state="offered", tutor_user_id=tutor
        )

    ok, codes, tutors = _check(module_seeded_url, student, subject, starts_at)

    assert not ok
    assert tutors == 0
    assert not (codes & SLOT_CODES)


def test_a_slot_reappears_when_the_reason_goes(
    module_seeded_url, client, cookie, family, subject
):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    booked = existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        state="offered",
        tutor_user_id=tutor,
    )
    assert slot_starts(
        get_slots(client, cookie, slots_params(family.student_id, subject))
    ) == ["11:00"]

    from app.modules.scheduling.models import ClassSession

    async def _cancel(session: AsyncSession) -> None:
        await change(
            session,
            ClassSession,
            booked,
            state="cancelled",
            cancelled_at=NOW,
            cancellation_reason="Motivo",
        )

    in_uow(module_seeded_url, _cancel)

    assert slot_starts(
        get_slots(client, cookie, slots_params(family.student_id, subject))
    ) == ["10:00", "10:30", "11:00"]
