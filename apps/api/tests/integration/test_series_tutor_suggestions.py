"""Tutor suggestions for a whole weekly series (HF-05 C) with real logins
through the real app.

Each test takes its own decade (closures are global in the module database)
and freezes the clock at its :class:`~tests.integration.series_support.Timeline`
"now" (a Sunday 12:00, Mexico City); the series starts the next Monday at
16:00 for 60 minutes, so the default 12-week horizon holds 12 Mondays. Each
test makes its own subject, so only its tutors are candidates.
"""

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import SeriesTutorSuggestionQuery
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, post
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    series_body,
    timeline,
)
from tests.integration.test_class_session_create import (
    INVALID,
    close_center,
    new_room,
    new_student,
    new_subject,
    new_tutor,
    time_off,
)
from tests.integration.test_tutor_suggestions import (
    prefer_room,
    row_counts,
    set_limits,
    teaching,
)

PATH = "/api/v1/tutor-suggestions/series"


def body(
    subject: uuid.UUID,
    tl: Timeline,
    *,
    weekdays: list[int] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject_id": str(subject),
        "starts_on": tl.monday.isoformat(),
        "start_time": "16:00",
        "duration_minutes": 60,
        "recurrence": {"weekdays": [1] if weekdays is None else weekdays},
    }
    return payload | changes


def suggest(
    url: str, client: TestClient, tl: Timeline, payload: dict[str, Any], role: str
) -> Response:
    """Sign in a new user with ``role`` and ask, all at ``tl.now``."""
    user = create_user(url, roles=(role,))
    with frozen_clock(tl.now):
        cookie = sign_in(client, user.username)
        return post(client, cookie, PATH, payload)


def ok(response: Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def listed(result: dict[str, Any]) -> list[uuid.UUID]:
    return [uuid.UUID(item["tutor"]["id"]) for item in result["items"]]


def counts(result: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """``(occurrences, qualified, eligible, busy, unavailable)``."""
    return (
        result["occurrence_count"],
        result["qualified_count"],
        result["eligible_count"],
        result["busy_count"],
        result["unavailable_count"],
    )


def errors(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [
        (".".join(map(str, e["path"])), e["code"]) for e in response.json()["errors"]
    ]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


@pytest.fixture
def ask(module_seeded_url: str, client: TestClient) -> Any:
    """``ask(tl, payload)``: an Academic Coordinator asks at ``tl.now``."""

    def _ask(tl: Timeline, payload: dict[str, Any]) -> Response:
        return suggest(module_seeded_url, client, tl, payload, ACADEMIC_COORDINATOR)

    return _ask


# --- C4: eligibility over every occurrence ------------------------------------------


def test_a_tutor_available_every_week_is_eligible(module_seeded_url, ask, subject):
    tl = timeline()
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])

    result = ok(ask(tl, body(subject, tl)))

    assert listed(result) == [tutor]
    assert counts(result) == (12, 1, 1, 0, 0)
    [item] = result["items"]
    assert item["rank"] == 1
    assert item["warnings"] == []
    assert (item["weekly_minutes"], item["daily_minutes"]) == (0, 0)


def test_one_day_off_on_the_third_occurrence_is_unavailable(
    module_seeded_url, ask, subject
):
    tl = timeline()
    control = new_tutor(module_seeded_url, qualified_for=[subject])
    away = new_tutor(module_seeded_url, qualified_for=[subject])
    time_off(module_seeded_url, away, tl.week(3))

    result = ok(ask(tl, body(subject, tl)))

    assert listed(result) == [control]
    assert counts(result) == (12, 2, 1, 0, 1)


def test_an_overlap_on_one_occurrence_is_busy(module_seeded_url, ask, subject):
    tl = timeline()
    control = new_tutor(module_seeded_url, qualified_for=[subject])
    busy = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, busy, tl.at(tl.week(7), "16:30"), 60, "confirmed")
    # Back-to-back and other days never block.
    teaching(module_seeded_url, control, tl.at(tl.week(2), "15:00"), 60, "confirmed")
    teaching(module_seeded_url, control, tl.at(tl.week(4), "17:00"), 60, "offered")
    teaching(module_seeded_url, control, tl.at(tl.week(5, 2), "16:00"), 60, "completed")

    result = ok(ask(tl, body(subject, tl)))

    assert listed(result) == [control]
    assert counts(result) == (12, 2, 1, 1, 0)


def test_busy_and_unavailable_counts_as_busy(module_seeded_url, ask, subject):
    tl = timeline()
    both = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, both, tl.at(tl.week(2), "16:00"), 60, "offered")
    time_off(module_seeded_url, both, tl.week(9))
    new_tutor(module_seeded_url, qualified_for=[subject], windows=[])

    result = ok(ask(tl, body(subject, tl)))

    assert counts(result) == (12, 2, 0, 1, 1)
    assert result["items"] == []


def test_a_closure_occurrence_is_not_checked(module_seeded_url, ask, subject):
    tl = timeline()
    close_center(module_seeded_url, tl.week(4))
    # Unavailable only on the closed Monday: still eligible.
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    time_off(module_seeded_url, tutor, tl.week(4))

    result = ok(ask(tl, body(subject, tl)))

    assert listed(result) == [tutor]
    assert counts(result) == (11, 1, 1, 0, 0)


def test_inactive_or_unqualified_tutors_are_not_counted(
    module_seeded_url, ask, subject
):
    tl = timeline()
    new_tutor(module_seeded_url, qualified_for=[subject], state="suspended")
    new_tutor(module_seeded_url, qualified_for=[new_subject(module_seeded_url)])

    result = ok(ask(tl, body(subject, tl)))

    assert counts(result) == (12, 0, 0, 0, 0)
    assert result["items"] == []


# --- C5: ranking at the first occurrence --------------------------------------------


def test_the_ranking_is_the_first_occurrences(module_seeded_url, ask, subject):
    tl = timeline()
    ana = new_tutor(module_seeded_url, qualified_for=[subject], display_name="Ana")
    beto = new_tutor(module_seeded_url, qualified_for=[subject], display_name="Beto")
    room = new_room(module_seeded_url)
    prefer_room(module_seeded_url, beto, room)
    # Ana teaches in the first week (Tuesday); Beto only in the second one,
    # which doesn't count.
    teaching(module_seeded_url, ana, tl.at(tl.week(1, 2), "10:00"), 90, "confirmed")
    teaching(module_seeded_url, beto, tl.at(tl.week(2, 2), "10:00"), 120, "confirmed")
    set_limits(module_seeded_url, ana, daily=30)

    result = ok(ask(tl, body(subject, tl, classroom_id=str(room))))

    assert listed(result) == [beto, ana]
    first, second = result["items"]
    assert (first["weekly_minutes"], first["prefers_classroom"]) == (0, True)
    assert (second["weekly_minutes"], second["daily_limit_minutes"]) == (90, 30)
    # Ana would exceed her daily limit; a series never returns warnings.
    assert second["warnings"] == []


def test_the_limit(module_seeded_url, ask, subject):
    tl = timeline()
    for _ in range(3):
        new_tutor(module_seeded_url, qualified_for=[subject])

    result = ok(ask(tl, body(subject, tl, limit=2)))

    assert (len(result["items"]), result["eligible_count"]) == (2, 3)


# --- C3: the occurrences ------------------------------------------------------------


def service_call(
    url: str, tl: Timeline, payload: dict[str, Any], horizon_weeks: int
) -> Any:
    query = SeriesTutorSuggestionQuery.model_validate(payload)

    async def call(session: AsyncSession) -> Any:
        return await scheduling.suggest_for_series(
            session, query, horizon_weeks=horizon_weeks
        )

    with frozen_clock(tl.now):
        return in_uow(url, call)


@pytest.mark.parametrize("weeks", [4, 6, 26])
def test_the_horizon_follows_the_setting(module_seeded_url, subject, weeks):
    tl = timeline()
    new_tutor(module_seeded_url, qualified_for=[subject])

    result = service_call(
        module_seeded_url, tl, body(subject, tl, weekdays=[1, 3]), weeks
    )

    assert result.occurrence_count == 2 * weeks
    assert result.eligible_count == 1


def test_a_count_rule_ends_inside_the_horizon(module_seeded_url, ask, subject):
    tl = timeline()

    payload = body(subject, tl)
    payload["recurrence"]["count"] = 5

    assert ok(ask(tl, payload))["occurrence_count"] == 5


def test_occurrences_before_now_are_dropped(module_seeded_url, ask, subject):
    tl = timeline()
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    sunday = tl.monday - timedelta(days=1)
    # Today (Sunday) at 10:00 already started at "now" (12:00); the next
    # Sundays count. The busy session today is never checked.
    teaching(module_seeded_url, tutor, tl.at(sunday, "10:00"), 60, "confirmed")
    payload = body(
        subject,
        tl,
        weekdays=[7],
        starts_on=sunday.isoformat(),
        start_time="10:00",
    )

    result = ok(ask(tl, payload))

    assert listed(result) == [tutor]
    assert result["occurrence_count"] == 11


def test_nothing_left_to_check_is_an_empty_200(module_seeded_url, ask, subject):
    tl = timeline()
    new_tutor(module_seeded_url, qualified_for=[subject])
    close_center(module_seeded_url, tl.monday)
    payload = body(subject, tl)
    payload["recurrence"]["count"] = 1

    result = ok(ask(tl, payload))

    assert result == {
        "items": [],
        "eligible_count": 0,
        "qualified_count": 0,
        "busy_count": 0,
        "unavailable_count": 0,
        "occurrence_count": 0,
    }


# --- C2: validation, as POST /class-series -----------------------------------------


def as_series(module_seeded_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The same timing and references as a ``POST /class-series`` body."""
    recurrence = {"weekdays": [], "interval": 1, "until": None, "count": None}
    recurrence |= payload["recurrence"]
    return series_body(
        student=new_student(module_seeded_url),
        subject=uuid.UUID(payload["subject_id"]),
        starts_on=date.fromisoformat(payload["starts_on"]),
        room=(
            None
            if payload.get("classroom_id") is None
            else uuid.UUID(payload["classroom_id"])
        ),
        start_time=payload["start_time"],
        duration_minutes=payload["duration_minutes"],
        weekdays=recurrence["weekdays"],
        interval=recurrence["interval"],
        until=(
            None
            if recurrence["until"] is None
            else date.fromisoformat(recurrence["until"])
        ),
        count=recurrence["count"],
    )


def invalid_cases(url: str, tl: Timeline) -> dict[str, dict[str, Any]]:
    subject = new_subject(url)
    inactive = new_subject(url, is_active=False)
    room = new_room(url, is_active=False)
    good = body(subject, tl)
    return {
        "weekdays-empty": good | {"recurrence": {"weekdays": []}},
        "weekday-range": good | {"recurrence": {"weekdays": [8]}},
        "interval": good | {"recurrence": {"weekdays": [1], "interval": 5}},
        "until-and-count": good
        | {
            "recurrence": {
                "weekdays": [1],
                "until": tl.week(3).isoformat(),
                "count": 2,
            }
        },
        "until-before-start": good
        | {
            "recurrence": {
                "weekdays": [1],
                "until": (tl.monday - timedelta(days=1)).isoformat(),
            }
        },
        "no-occurrences": good
        | {"recurrence": {"weekdays": [2], "until": tl.monday.isoformat()}},
        "in-the-past": good
        | {"starts_on": (tl.monday - timedelta(days=2)).isoformat()},
        "crosses-midnight": good | {"start_time": "23:30"},
        "references": body(inactive, tl, classroom_id=str(room)),
        "unknown-references": body(uuid.uuid4(), tl, classroom_id=str(uuid.uuid4())),
    }


def test_422_codes_and_paths_match_post_class_series(module_seeded_url, client):
    tl = timeline()
    owner = create_user(module_seeded_url, roles=(OWNER,))
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        for name, payload in invalid_cases(module_seeded_url, tl).items():
            mine = post(client, cookie, PATH, payload)
            series = create_series(
                client,
                cookie,
                as_series(module_seeded_url, payload),
            )
            assert errors(mine) == errors(series), name
            assert errors(mine), name


def test_schema_errors(module_seeded_url, ask, subject):
    tl = timeline()
    good = body(subject, tl)
    for bad in (
        good | {"tutor_user_id": str(uuid.uuid4())},
        good | {"start_time": "16:00:30"},
        good | {"duration_minutes": 481},
        good | {"limit": 51},
        {k: v for k, v in good.items() if k != "recurrence"},
    ):
        assert code_of(ask(tl, bad)) == INVALID, bad


def test_the_rule_comes_before_the_references(module_seeded_url, ask):
    tl = timeline()
    payload = body(uuid.uuid4(), tl) | {"recurrence": {"weekdays": []}}

    assert errors(ask(tl, payload)) == [("body.recurrence.weekdays", "weekdays_empty")]


# --- E1: access ---------------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, FINANCE_STAFF, TUTOR])
def test_who_may_ask(module_seeded_url, client, subject, role):
    tl = timeline()

    response = suggest(module_seeded_url, client, tl, body(subject, tl), role)

    assert response.status_code == (200 if role == OWNER else 403), response.text


# --- C6: statement budget and no writes ---------------------------------------------


@pytest.mark.parametrize("count", [3, 5])
def test_statement_budget(module_seeded_url, subject, count):
    tl = timeline()
    room = new_room(module_seeded_url)
    for _ in range(count):
        tutor = new_tutor(module_seeded_url, qualified_for=[subject])
        prefer_room(module_seeded_url, tutor, room)
        set_limits(module_seeded_url, tutor, weekly=600)
        teaching(module_seeded_url, tutor, tl.at(tl.week(3, 2), "10:00"), 60, "offered")
    # One busy and one unavailable tutor are candidates too.
    busy = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, busy, tl.at(tl.week(5), "16:00"), 60, "confirmed")
    away = new_tutor(module_seeded_url, qualified_for=[subject])
    time_off(module_seeded_url, away, tl.week(8))
    close_center(module_seeded_url, tl.week(10))
    query = SeriesTutorSuggestionQuery.model_validate(
        body(subject, tl, weekdays=[1, 3, 5], classroom_id=str(room))
    )

    async def call(session: AsyncSession) -> Any:
        return await scheduling.suggest_for_series(session, query, horizon_weeks=26)

    with frozen_clock(tl.now):
        statements, result = count_statements(module_seeded_url, call)

    assert result.occurrence_count == 26 * 3 - 1
    assert (result.eligible_count, result.busy_count, result.unavailable_count) == (
        count,
        1,
        1,
    )
    candidates = count + 2
    assert statements <= 10 + 6 * candidates


def test_nothing_is_written(module_seeded_url, client, subject):
    tl = timeline()
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    user = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    with frozen_clock(tl.now):
        cookie = sign_in(client, user.username)
        before = row_counts(module_seeded_url)
        response = post(client, cookie, PATH, body(subject, tl))

    assert listed(ok(response)) == [tutor]
    assert row_counts(module_seeded_url) == before
