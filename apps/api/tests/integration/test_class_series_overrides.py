"""Overriding the soft conflicts of a series create, occurrence by occurrence
(#236 I1) with real logins through the real app.

Each test takes its own decade (:func:`~tests.integration.series_support.
timeline`): "now" is a Sunday 12:00 (Mexico City) and the seeded operating
hours close on Sundays, so a weekly Sunday 10:00 series has exactly one
soft conflict per occurrence, ``outside_operating_hours``, unless a test
adds more.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.identity.permissions import ADMINISTRATOR, OWNER
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_support import (
    SERIES,
    Timeline,
    iso_z,
    series_body,
    series_rows,
    timeline,
)
from tests.integration.test_class_session_create import (
    CREATED,
    INVALID,
    close_center,
    existing_session,
    new_student,
    new_subject,
    new_tutor,
)

CONFLICT = (409, "scheduling_conflict")
FORBIDDEN = (403, "permission_denied")
EVENT = "scheduling.series_override"
REASON = "Clases de regularización acordadas con la familia."
HOURS = "outside_operating_hours"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def sign_in_at(client: TestClient, user: CreatedUser, at: datetime) -> Any:
    client.cookies.clear()
    with frozen_clock(at):
        return sign_in(client, user.username)


def create_at(client: TestClient, cookie: Any, at: datetime, body: Any) -> Response:
    with frozen_clock(at):
        return post(client, cookie, SERIES, body)


def sunday_body(
    url: str, tl: Timeline, *, count: int | None = 3, **changes: Any
) -> dict[str, Any]:
    """A weekly Sunday 10:00-11:00 series from the first Sunday after now,
    with no tutor and no room."""
    return series_body(
        student=changes.pop("student", None) or new_student(url),
        subject=changes.pop("subject", None) or new_subject(url),
        starts_on=changes.pop("starts_on", tl.week(1, 7)),
        start_time="10:00",
        weekdays=[7],
        count=count,
        **changes,
    )


def with_pairs(
    body: dict[str, Any], pairs: list[tuple[str, str]], reason: str = REASON
) -> dict[str, Any]:
    return body | {
        "override": {
            "pairs": [{"occurrence_start": s, "code": c} for s, c in pairs],
            "reason": reason,
        }
    }


def soft_pairs(response: Response) -> list[tuple[str, str]]:
    """Every (details.occurrence_start, code) pair of a 409, as sent."""
    assert code_of(response) == CONFLICT, response.text
    return [
        (c["details"]["occurrence_start"], c["code"])
        for c in response.json()["conflicts"]
        if c["severity"] == "soft"
    ]


def everything(url: str) -> tuple[int, ...]:
    """Series, series participants, sessions, session participants,
    overrides and audit events."""
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_series) AS a,"
        " (SELECT count(*) FROM class_series_participants) AS b,"
        " (SELECT count(*) FROM class_sessions) AS c,"
        " (SELECT count(*) FROM class_session_participants) AS d,"
        " (SELECT count(*) FROM class_session_overrides) AS e,"
        " (SELECT count(*) FROM audit_events) AS f",
    )
    return tuple(int(value) for value in row)


def override_rows(url: str, series_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT o.*, s.original_start FROM class_session_overrides o"
        " JOIN class_sessions s ON s.id = o.class_session_id"
        " WHERE o.class_series_id = :id ORDER BY s.original_start",
        {"id": str(series_id)},
    )


def series_events(url: str, series_id: Any) -> list[Any]:
    return events(url, record_id=uuid.UUID(str(series_id)), action=EVENT)


def delegate(url: str, owner: CreatedUser, admin: CreatedUser, grant: bool) -> None:
    async def _change(session: AsyncSession) -> None:
        operation = identity.grant_delegation if grant else identity.revoke_delegation
        await operation(
            session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="schedule.override",
        )

    in_uow(url, _change, actor_id=owner.id)


# --- The Sunday series ---------------------------------------------------------------


def test_the_owner_overrides_three_sundays(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = sunday_body(url, tl)

    refused = create_at(client, cookie, tl.now, body)
    pairs = soft_pairs(refused)
    created = create_at(
        client, cookie, tl.now, with_pairs(body, pairs, reason=f"  {REASON}  ")
    )

    sundays = [tl.at(tl.week(n, 7), "10:00") for n in (1, 2, 3)]
    assert pairs == [(iso_z(s), HOURS) for s in sundays]
    assert code_of(created) == CREATED, created.text
    detail = created.json()
    assert [o["overridden"] for o in detail["occurrences"]] == [True] * 3
    assert [o["state"] for o in detail["occurrences"]] == ["unassigned"] * 3
    rows = series_rows(url, detail["id"])
    assert {tuple(r.conflict_codes) for r in rows} == {()}
    stored = override_rows(url, detail["id"])
    assert [o.class_session_id for o in stored] == [r.id for r in rows]
    for row, start in zip(stored, sundays, strict=True):
        assert row.class_series_id == uuid.UUID(detail["id"])
        assert list(row.codes) == [HOURS]
        assert row.conflicts == [
            {
                "code": HOURS,
                "details": {
                    "date": start.date().isoformat(),
                    "opens_at": None,
                    "closes_at": None,
                },
            }
        ]
        assert row.reason == REASON
        assert row.actor_user_id == owner.id
    [event] = series_events(url, detail["id"])
    assert event.record_type == "class_series"
    assert event.actor_user_id == owner.id
    assert event.details == {
        "write": "create",
        "occurrence_count": 3,
        "occurrences": [
            {
                "override_id": str(row.id),
                "class_session_id": str(row.class_session_id),
                "occurrence_start": iso_z(start),
                "codes": [HOURS],
            }
            for row, start in zip(stored, sundays, strict=True)
        ],
        "reason": REASON,
    }
    [create_event] = events(
        url,
        record_type="class_series",
        record_id=uuid.UUID(detail["id"]),
        action="create",
    )
    assert event.request_id == create_event.request_id
    # No one-off override event: the series event is the record.
    assert all(
        events(url, record_id=r.id, action="scheduling.session_override") == []
        for r in rows
    )


def test_a_partial_override_refuses_everything(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = sunday_body(url, tl)
    pairs = soft_pairs(create_at(client, cookie, tl.now, body))
    before = everything(url)

    response = create_at(client, cookie, tl.now, with_pairs(body, pairs[:2]))

    assert soft_pairs(response) == pairs
    assert everything(url) == before


def test_mixed_codes_need_every_pair(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    # Sunday 20:00: the first Monday 16:00 is inside the 24-hour notice.
    at = tl.now + timedelta(hours=8)
    cookie = sign_in_at(client, owner, at)
    body = series_body(
        student=new_student(url),
        subject=new_subject(url),
        tutor=new_tutor(url),  # qualified for nothing
        starts_on=tl.monday,
        weekdays=[1],
        count=3,
    )

    pairs = soft_pairs(create_at(client, cookie, at, body))
    first = iso_z(tl.at(tl.monday, "16:00"))
    without_notice = [p for p in pairs if p != (first, "notice_window")]
    missing = create_at(client, cookie, at, with_pairs(body, without_notice))
    created = create_at(client, cookie, at, with_pairs(body, pairs))

    assert sorted(pairs) == sorted(
        [(first, "notice_window")]
        + [
            (iso_z(tl.at(tl.week(n), "16:00")), "tutor_not_qualified")
            for n in (1, 2, 3)
        ]
    )
    assert soft_pairs(missing) == pairs
    assert code_of(created) == CREATED, created.text
    stored = override_rows(url, created.json()["id"])
    assert [list(r.codes) for r in stored] == [
        ["tutor_not_qualified", "notice_window"],
        ["tutor_not_qualified"],
        ["tutor_not_qualified"],
    ]
    assert [o["state"] for o in created.json()["occurrences"]] == ["offered"] * 3


def test_a_hard_conflict_refuses_whatever_the_pairs_say(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject])
    blocker = existing_session(
        url,
        state="confirmed",
        tutor_user_id=tutor,
        student_id=new_student(url),
        starts_at=tl.at(tl.week(2, 7), "10:30"),
    )
    body = sunday_body(url, tl, subject=subject, tutor_user_id=str(tutor))
    first = create_at(client, cookie, tl.now, body)
    pairs = soft_pairs(first)
    before = everything(url)

    response = create_at(client, cookie, tl.now, with_pairs(body, pairs))
    hard = create_at(
        client,
        cookie,
        tl.now,
        with_pairs(
            body, [*pairs, (iso_z(tl.at(tl.week(2, 7), "10:00")), "tutor_overlap")]
        ),
    )

    assert code_of(response) == CONFLICT
    found = response.json()["conflicts"]
    assert found == first.json()["conflicts"]
    assert ("tutor_overlap", [str(blocker)]) in [
        (c["code"], c["details"].get("class_session_ids")) for c in found
    ]
    assert len(pairs) == 3
    assert everything(url) == before
    assert code_of(hard) == INVALID
    assert [(e["path"], e["code"]) for e in hard.json()["errors"]] == [
        (["body", "override", "pairs", 3, "code"], "code_not_overridable")
    ]


def test_extra_pairs_are_ignored_and_only_real_ones_stored(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = sunday_body(url, tl)
    pairs = soft_pairs(create_at(client, cookie, tl.now, body))
    extra = [
        *pairs,
        (pairs[0][0], "center_closed"),
        (pairs[1][0], "notice_window"),
        (iso_z(tl.at(tl.week(1, 7), "12:00")), HOURS),
        (iso_z(tl.at(tl.week(9, 7), "10:00")), HOURS),
    ]

    created = create_at(client, cookie, tl.now, with_pairs(body, extra))

    assert code_of(created) == CREATED, created.text
    stored = override_rows(url, created.json()["id"])
    assert [list(r.codes) for r in stored] == [[HOURS]] * 3
    [event] = series_events(url, created.json()["id"])
    assert event.details["occurrence_count"] == 3
    assert [o["codes"] for o in event.details["occurrences"]] == [[HOURS]] * 3


def test_an_override_on_a_clean_series_stores_nothing(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = series_body(
        student=new_student(url), subject=new_subject(url), starts_on=tl.monday, count=2
    )

    created = create_at(
        client,
        cookie,
        tl.now,
        with_pairs(body, [(iso_z(tl.at(tl.monday, "16:00")), "notice_window")]),
    )

    assert code_of(created) == CREATED, created.text
    assert override_rows(url, created.json()["id"]) == []
    assert series_events(url, created.json()["id"]) == []
    assert [o["overridden"] for o in created.json()["occurrences"]] == [False] * 2


def test_a_skipped_closure_date_gets_no_row_even_when_named(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = sunday_body(url, tl)
    pairs = soft_pairs(create_at(client, cookie, tl.now, body))
    close_center(url, tl.week(2, 7))

    created = create_at(client, cookie, tl.now, with_pairs(body, pairs))

    assert code_of(created) == CREATED, created.text
    rows = series_rows(url, created.json()["id"])
    assert [r.state for r in rows] == ["unassigned", "skipped_closure", "unassigned"]
    stored = override_rows(url, created.json()["id"])
    assert [r.class_session_id for r in stored] == [rows[0].id, rows[2].id]
    assert [o["overridden"] for o in created.json()["occurrences"]] == [
        True,
        False,
        True,
    ]


def test_the_horizon_moving_brings_a_new_occurrence_to_list(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    body = sunday_body(url, tl, count=None)
    cookie = sign_in_at(client, owner, tl.now)
    pairs = soft_pairs(create_at(client, cookie, tl.now, body))
    later = tl.now + timedelta(weeks=1)
    cookie = sign_in_at(client, owner, later)
    before = everything(url)

    response = create_at(client, cookie, later, with_pairs(body, pairs))

    resent = soft_pairs(response)
    new = [p for p in resent if p not in pairs]
    assert new == [(iso_z(tl.at(tl.week(13, 7), "10:00")), HOURS)]
    assert everything(url) == before


def test_an_administrator_needs_the_delegation(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    admin = create_user(url, roles=(ADMINISTRATOR,))
    body = sunday_body(url, tl)
    cookie = sign_in_at(client, owner, tl.now)
    pairs = soft_pairs(create_at(client, cookie, tl.now, body))
    clean = series_body(
        student=new_student(url), subject=new_subject(url), starts_on=tl.monday, count=1
    )
    admin_cookie = sign_in_at(client, admin, tl.now)
    before = everything(url)

    denied = create_at(client, admin_cookie, tl.now, with_pairs(body, pairs))
    denied_clean = create_at(client, admin_cookie, tl.now, with_pairs(clean, pairs))
    assert everything(url) == before
    delegate(url, owner, admin, grant=True)
    allowed = create_at(client, admin_cookie, tl.now, with_pairs(body, pairs))
    delegate(url, owner, admin, grant=False)
    other = sunday_body(url, tl)
    revoked = create_at(client, admin_cookie, tl.now, with_pairs(other, pairs))

    assert code_of(denied) == FORBIDDEN
    assert code_of(denied_clean) == FORBIDDEN
    assert code_of(allowed) == CREATED, allowed.text
    assert {r.actor_user_id for r in override_rows(url, allowed.json()["id"])} == {
        admin.id
    }
    assert code_of(revoked) == FORBIDDEN


def test_the_403_comes_before_the_body_is_read(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    admin = create_user(url, roles=(ADMINISTRATOR,))
    cookie = sign_in_at(client, admin, tl.now)
    # Unknown references: the 403 comes before their 422.
    body = series_body(
        student=uuid.uuid4(), subject=uuid.uuid4(), starts_on=tl.monday, count=1
    )

    response = create_at(
        client, cookie, tl.now, with_pairs(body, [(iso_z(tl.now), HOURS)])
    )

    assert code_of(response) == FORBIDDEN


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"pairs": [], "reason": REASON}, (["pairs"], "pairs_required")),
        ({"reason": REASON}, (["pairs"], "pairs_required")),
        (
            {"pairs": [{"occurrence_start": "2031-01-12T16:00:00Z", "code": HOURS}]},
            (["reason"], "reason_required"),
        ),
        (
            {
                "pairs": [{"occurrence_start": "2031-01-12T16:00:00Z", "code": HOURS}],
                "reason": "a" * 1001,
            },
            (["reason"], "reason_too_long"),
        ),
        (
            {
                "pairs": [
                    {"occurrence_start": "2031-01-12T16:00:00Z", "code": HOURS},
                    {"occurrence_start": "2031-01-12T10:00:00-06:00", "code": HOURS},
                ],
                "reason": REASON,
            },
            (["pairs"], "duplicate_pair"),
        ),
        (
            {
                "pairs": [{"occurrence_start": "2031-01-12T16:00:00", "code": HOURS}],
                "reason": REASON,
            },
            (["pairs", 0, "occurrence_start"], "occurrence_start_invalid"),
        ),
        (
            {
                "pairs": [{"occurrence_start": "2031-01-12T16:00:00Z", "code": "x"}],
                "reason": REASON,
            },
            (["pairs", 0, "code"], "code_unknown"),
        ),
        (
            {
                "pairs": [{"occurrence_start": "2031-01-12T16:00:00Z", "code": HOURS}],
                "codes": [HOURS],
                "reason": REASON,
            },
            (["codes"], "extra_forbidden"),
        ),
    ],
)
def test_override_body_errors_are_422(module_seeded_url, client, override, error):
    url = module_seeded_url
    tl = timeline()
    owner = create_user(url, roles=(OWNER,))
    cookie = sign_in_at(client, owner, tl.now)
    body = sunday_body(url, tl) | {"override": override}
    before = everything(url)

    response = create_at(client, cookie, tl.now, body)

    assert code_of(response) == INVALID, response.text
    path, code = error
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "override", *path], code)
    ]
    assert everything(url) == before


# --- DST: Tijuana (own database) -----------------------------------------------------


def test_pairs_match_instants_not_wall_times_across_a_dst_change(seeded_url):
    tijuana = ZoneInfo("America/Tijuana")
    owner = create_user(seeded_url, roles=(OWNER,))
    # Sunday 2026-10-18 12:00 PDT; the series runs Oct 25 (PDT), Nov 1 and
    # Nov 8 (PST; the clocks went back on Nov 1 at 02:00).
    now = datetime(2026, 10, 18, 12, tzinfo=tijuana)
    body = series_body(
        student=new_student(seeded_url),
        subject=new_subject(seeded_url),
        starts_on=date(2026, 10, 25),
        start_time="10:00",
        weekdays=[7],
        count=3,
    )
    with auth_client(seeded_url) as client:
        cookie = sign_in_at(client, owner, now)
        with frozen_clock(now):
            reauthenticate(client, cookie)
            zone = patch(
                client,
                cookie,
                "/api/v1/business-settings",
                {"version": 1, "time_zone": "America/Tijuana"},
            )
        assert code_of(zone) == (200, None), zone.text
        pairs = soft_pairs(create_at(client, cookie, now, body))
        # Nov 1 10:00 written with the summer offset is 17:00Z, not 18:00Z.
        wrong = [
            (
                datetime.combine(date(2026, 11, 1), time(10))
                .replace(tzinfo=ZoneInfo("Etc/GMT+7"))
                .isoformat()
                if start.startswith("2026-11-01")
                else start,
                code,
            )
            for start, code in pairs
        ]
        refused = create_at(client, cookie, now, with_pairs(body, wrong))
        created = create_at(client, cookie, now, with_pairs(body, pairs))

    assert pairs == [
        ("2026-10-25T17:00:00Z", HOURS),
        ("2026-11-01T18:00:00Z", HOURS),
        ("2026-11-08T18:00:00Z", HOURS),
    ]
    assert wrong[1][0] == "2026-11-01T10:00:00-07:00"
    assert soft_pairs(refused) == pairs
    assert code_of(created) == CREATED, created.text
    assert len(override_rows(seeded_url, created.json()["id"])) == 3
