"""Change requests by families and tutors (#62 S, A3, B, D, G2; H1, H3),
with real logins through the real app.

Helpers: ``change_request_support`` (each test takes its own decade; the
scene's confirmed session is on the second Tuesday at 10:00, about 214 hours
after "now"). Staff decisions are in ``test_change_request_decisions.py``.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.change_request_support import (
    CONSENT,
    CREATED,
    INVALID,
    NOT_FOUND,
    NOT_PENDING,
    OK,
    PENDING,
    SLOT_UNAVAILABLE,
    STALE,
    STARTED,
    Scene,
    add_guardian,
    add_student_login,
    adult_student,
    approval_of,
    call,
    create,
    created,
    hours,
    listing,
    make_scene,
    ok,
    read,
    request_row,
    withdraw,
)
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_edit_support import add_closure, set_row
from tests.integration.test_class_session_create import new_student
from tests.integration.test_session_cancellation import one_off

DETAIL_KEYS = {
    "id",
    "kind",
    "status",
    "class_session",
    "student",
    "proposed_starts_at",
    "proposed_ends_at",
    "note",
    "outcome",
    "decision",
    "requested_by_me",
    "can_withdraw",
    "created_at",
    "resolved_at",
    "version",
}
SESSION_KEYS = {"id", "starts_at", "ends_at", "subject"}
SUBJECT_KEYS = {"id", "name", "education_level_name"}
OUTCOME_KEYS = {"classification", "makeup_allowed", "makeup_until"}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def scene(module_seeded_url: str, client: Any) -> Scene:
    return make_scene(module_seeded_url, client)


def z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def other_session(scene: Scene, **body: Any) -> dict[str, Any]:
    """Another one-off session of the scene's ctx (default: offered)."""
    return one_off(scene.ctx, **body)


# --- B3: create and classify ---------------------------------------------------------


@pytest.mark.parametrize(
    ("ahead", "classification", "makeup", "priority"),
    [
        (hours(25), "on_time", True, "normal"),
        (hours(24), "on_time", True, "normal"),
        (hours(24) - timedelta(minutes=1), "late", False, "high"),
    ],
    ids=["25h", "exactly-24h", "23h59m"],
)
def test_a_family_cancellation_is_classified_at_request_time(
    scene, ahead, classification, makeup, priority
):
    at = scene.start - ahead
    note = "  Viaje familiar  "

    response = create(scene, scene.guardian, at=at, note=note)

    detail = created(response)
    makeup_until = scene.start + timedelta(days=30) if makeup else None
    assert detail["kind"] == "cancellation"
    assert detail["status"] == "pending"
    assert detail["note"] == "Viaje familiar"
    assert detail["outcome"] == {
        "classification": classification,
        "makeup_allowed": makeup,
        "makeup_until": None if makeup_until is None else z(makeup_until),
    }
    assert detail["decision"] is None
    assert detail["requested_by_me"] is True
    assert detail["can_withdraw"] is True
    assert detail["student"]["id"] == str(scene.ctx.student)
    assert detail["class_session"]["id"] == scene.session["id"]
    row = request_row(scene.url, detail["id"])
    assert (row.initiated_by, row.student_id, row.requested_by_user_id) == (
        "family",
        scene.ctx.student,
        scene.guardian.id,
    )
    assert (row.classification, row.makeup_allowed, row.makeup_until) == (
        classification,
        makeup,
        makeup_until,
    )
    assert (row.session_starts_at, row.created_at) == (scene.start, at)
    [policy] = scheduling_policy_ids(scene.url, at)
    assert row.policy_id == policy
    item = approval_of(scene.url, detail["id"])
    assert (item.request_type, item.status, item.requester_user_id, item.priority) == (
        "scheduling.change_request",
        "pending",
        scene.guardian.id,
        priority,
    )


def scheduling_policy_ids(url: str, at: datetime) -> list[uuid.UUID]:
    from tests.integration.conftest import run_sql

    return [
        row.id
        for row in run_sql(
            url,
            "SELECT id FROM scheduling_policies WHERE effective_from <= :at"
            " ORDER BY effective_from DESC LIMIT 1",
            {"at": at},
        )
    ]


def test_a_family_reschedule_48_hours_ahead_is_on_time_without_makeup(scene):
    at = scene.start - hours(48)
    proposed = scene.start + hours(2)

    detail = created(
        create(
            scene, scene.guardian, kind="reschedule", proposed_starts_at=proposed, at=at
        )
    )

    assert detail["outcome"] == {
        "classification": "on_time",
        "makeup_allowed": False,
        "makeup_until": None,
    }
    assert detail["proposed_starts_at"] == z(proposed)
    assert detail["proposed_ends_at"] == z(proposed + hours(1))
    row = request_row(scene.url, detail["id"])
    assert (row.proposed_starts_at, row.proposed_ends_at) == (
        proposed,
        proposed + hours(1),
    )


@pytest.mark.parametrize(
    ("kind", "makeup"), [("cancellation", True), ("reschedule", False)]
)
def test_a_tutor_request_is_always_excused(scene, kind, makeup):
    at = scene.start - hours(1)
    body = (
        {"proposed_starts_at": scene.start + hours(3)} if kind == "reschedule" else {}
    )

    detail = created(create(scene, scene.ctx.tutor, kind=kind, at=at, **body))

    assert detail["outcome"]["classification"] == "excused"
    assert detail["outcome"]["makeup_allowed"] is makeup
    # The tutor sees their student's name (they teach them).
    assert detail["student"]["id"] == str(scene.ctx.student)
    row = request_row(scene.url, detail["id"])
    assert (row.initiated_by, row.student_id) == ("tutor", None)
    assert approval_of(scene.url, detail["id"]).priority == "high"


def test_a_student_requests_for_their_own_session(scene):
    student = adult_student(scene.url, scene.ctx.tl.monday.year)
    login = add_student_login(scene.url, student)
    session = one_off(
        scene.ctx,
        accept=True,
        student=student,
        room=scene.ctx.other_room,
        starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00"),
    )

    detail = created(create(scene, login, session_id=session["id"]))

    assert request_row(scene.url, detail["id"]).student_id == student


# --- S2: refusals without an oracle --------------------------------------------------


def test_hidden_sessions_are_the_same_404(scene):
    stranger = add_guardian(scene.url, new_student(scene.url))
    other_tutor = create_user(scene.url, roles=("tutor",))
    offered = other_session(scene, starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00"))
    unassigned = other_session(
        scene, tutor=False, starts_at=scene.at(scene.ctx.tl.week(2, 4), "10:00")
    )
    cases = [
        (scene.guardian, uuid.uuid4()),
        (stranger, scene.session["id"]),
        (scene.guardian, offered["id"]),
        (scene.guardian, unassigned["id"]),
        (other_tutor, scene.session["id"]),
        (scene.ctx.owner, scene.session["id"]),
    ]

    bodies = []
    for user, session_id in cases:
        response = create(scene, user, session_id=session_id)
        assert code_of(response) == NOT_FOUND, (user, response.text)
        bodies.append(
            {
                k: v
                for k, v in response.json().items()
                if k not in ("instance", "request_id")
            }
        )
    assert all(body == bodies[0] for body in bodies)


def test_a_guardian_of_an_adult_needs_the_consent(scene):
    student = adult_student(scene.url, scene.ctx.tl.monday.year)
    without = add_guardian(scene.url, student)
    with_consent = add_guardian(scene.url, student, consent=True)
    session = one_off(
        scene.ctx,
        accept=True,
        student=student,
        room=scene.ctx.other_room,
        starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00"),
    )

    assert code_of(create(scene, without, session_id=session["id"])) == CONSENT
    created(create(scene, with_consent, session_id=session["id"]))


def test_group_sessions_are_not_changed_by_request(scene):
    start = scene.at(scene.ctx.tl.week(2, 3), "10:00")

    async def _group(session: AsyncSession) -> uuid.UUID:
        row = await persist_class_session(
            session,
            kind="group",
            state="confirmed",
            subject_id=scene.ctx.subject,
            tutor_user_id=scene.ctx.tutor.id,
            starts_at=start,
            ends_at=start + hours(1),
        )
        await add_participant(session, row, scene.ctx.student)
        return row.id

    group_session = in_uow(scene.url, _group)

    response = create(scene, scene.guardian, session_id=group_session)

    assert code_of(response) == (409, "change_request_kind_unsupported")
    assert response.json()["details"] == {"kind": "group"}


def test_only_a_confirmed_session_can_be_changed(scene):
    offered = other_session(scene, starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00"))

    response = create(scene, scene.ctx.tutor, session_id=offered["id"])

    assert code_of(response) == (409, "class_session_not_changeable")
    assert response.json()["details"] == {"state": "offered"}
    set_row(scene.url, scene.session["id"], state="completed")
    for user in (scene.guardian, scene.ctx.tutor):
        response = create(scene, user)
        assert code_of(response) == (409, "class_session_not_changeable")
        assert response.json()["details"] == {"state": "completed"}


def test_a_started_session_can_no_longer_be_changed(scene):
    response = create(scene, scene.guardian, at=scene.start)

    assert code_of(response) == STARTED


def test_one_pending_request_per_session(scene):
    first = created(create(scene, scene.guardian))

    by_tutor = create(scene, scene.ctx.tutor)
    by_other_guardian = create(scene, scene.other_guardian)

    assert code_of(by_tutor) == PENDING
    assert "details" not in by_tutor.json()
    assert code_of(by_other_guardian) == PENDING
    assert by_other_guardian.json()["details"] == {"change_request_id": first["id"]}


@pytest.mark.parametrize(
    ("proposal", "code"),
    [
        (lambda s: s.start.replace(tzinfo=None).isoformat(), "naive_datetime"),
        (
            lambda s: (s.start + timedelta(minutes=30, seconds=30)).isoformat(),
            "not_whole_minutes",
        ),
        (
            lambda s: s.at(s.ctx.tl.week(2, 2), "23:30").isoformat(),
            "crosses_local_date",
        ),
        (lambda s: (s.ctx.tl.now - hours(1)).isoformat(), "proposal_in_past"),
        (lambda s: s.start.isoformat(), "proposal_unchanged"),
        (lambda s: None, "proposal_required"),
    ],
    ids=["naive", "seconds", "crosses-date", "past", "unchanged", "missing"],
)
def test_reschedule_proposal_errors(scene, proposal, code):
    body = {"proposed_starts_at": proposal(scene)}

    response = create(scene, scene.guardian, kind="reschedule", **body)

    assert code_of(response) == INVALID
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "proposed_starts_at"], code)
    ]


def test_a_cancellation_takes_no_proposal(scene):
    response = create(scene, scene.guardian, proposed_starts_at=scene.start + hours(2))

    assert code_of(response) == INVALID
    assert response.json()["errors"][0]["code"] == "proposal_not_allowed"


def test_a_family_proposal_must_be_an_available_slot(scene):
    tl = scene.ctx.tl
    busy_at = scene.at(tl.week(2, 3), "10:00")
    other_session(scene, tutor=False, room=scene.ctx.other_room, starts_at=busy_at)
    closed_day = tl.week(2, 4)
    add_closure(scene.url, closed_day)
    refused = [
        busy_at,  # the student has another session
        scene.at(closed_day, "10:00"),  # a closure
        scene.at(tl.week(2, 2), "20:00"),  # after the closing time
        scene.at(tl.week(2, 7), "10:00"),  # Sunday: closed
        scene.at(tl.week(1, 1), "10:00"),  # 22 h ahead: inside the booking notice
    ]

    for proposed in refused:
        response = create(
            scene, scene.guardian, kind="reschedule", proposed_starts_at=proposed
        )
        assert code_of(response) == SLOT_UNAVAILABLE, (proposed, response.text)
        assert not {"details", "conflicts", "errors"} & set(response.json())


def test_a_proposal_overlapping_only_the_session_itself_is_accepted(scene):
    detail = created(
        create(
            scene,
            scene.guardian,
            kind="reschedule",
            proposed_starts_at=scene.start + timedelta(minutes=30),
        )
    )

    assert detail["proposed_starts_at"] == z(scene.start + timedelta(minutes=30))


def test_a_tutor_proposal_is_checked_for_the_bounds_only(scene):
    late_evening = scene.at(scene.ctx.tl.week(2, 2), "21:00")

    detail = created(
        create(
            scene, scene.ctx.tutor, kind="reschedule", proposed_starts_at=late_evening
        )
    )

    assert detail["proposed_starts_at"] == z(late_evening)


# --- S2, D: reads and visibility -----------------------------------------------------


def test_reads_follow_the_family_and_tutor_rules(scene):
    family_request = created(create(scene, scene.guardian))
    second = one_off(
        scene.ctx, accept=True, starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00")
    )
    tutor_request = created(create(scene, scene.ctx.tutor, session_id=second["id"]))
    stranger = add_guardian(scene.url, new_student(scene.url))

    # The other guardian of the child sees the family request, not the tutor's.
    seen = ok(read(scene, scene.other_guardian, family_request["id"]))
    assert (seen["requested_by_me"], seen["can_withdraw"]) == (False, False)
    assert code_of(read(scene, scene.other_guardian, tutor_request["id"])) == NOT_FOUND
    assert code_of(read(scene, scene.guardian, tutor_request["id"])) == NOT_FOUND
    page = ok(listing(scene, scene.guardian))
    assert [item["id"] for item in page["items"]] == [family_request["id"]]
    # The tutor sees only what they asked.
    assert code_of(read(scene, scene.ctx.tutor, family_request["id"])) == NOT_FOUND
    page = ok(listing(scene, scene.ctx.tutor))
    assert [item["id"] for item in page["items"]] == [tutor_request["id"]]
    # Anyone else: 404 and an empty page; staff too.
    for user in (stranger, scene.ctx.owner):
        assert code_of(read(scene, user, family_request["id"])) == NOT_FOUND
        assert ok(listing(scene, user))["items"] == []
    assert code_of(read(scene, scene.guardian, uuid.uuid4())) == NOT_FOUND


def test_the_list_is_an_offset_page_with_filters(scene):
    first = created(create(scene, scene.guardian))
    second_session = one_off(
        scene.ctx, accept=True, starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00")
    )
    second = created(
        create(
            scene,
            scene.other_guardian,
            session_id=second_session["id"],
            at=scene.ctx.tl.now + timedelta(minutes=1),
        )
    )

    page = ok(listing(scene, scene.guardian))
    assert {key: page[key] for key in ("total", "limit", "offset")} == {
        "total": 2,
        "limit": 25,
        "offset": 0,
    }
    assert [item["id"] for item in page["items"]] == [second["id"], first["id"]]
    assert [
        i["id"] for i in ok(listing(scene, scene.guardian, limit=1, offset=1))["items"]
    ] == [first["id"]]
    assert (
        ok(listing(scene, scene.guardian, class_session_id=second_session["id"]))[
            "total"
        ]
        == 1
    )
    assert ok(listing(scene, scene.guardian, status="approved"))["total"] == 0
    assert ok(listing(scene, scene.guardian, status="pending"))["total"] == 2
    foreign = ok(listing(scene, scene.guardian, student_id=new_student(scene.url)))
    assert foreign == {"items": [], "total": 0, "limit": 25, "offset": 0}
    assert code_of(listing(scene, scene.guardian, limit=101)) == INVALID


def test_the_list_takes_at_most_five_statements(scene):
    for day in (2, 3, 4):
        session = (
            scene.session
            if day == 2
            else one_off(
                scene.ctx,
                accept=True,
                starts_at=scene.at(scene.ctx.tl.week(2, day), "10:00"),
            )
        )
        created(create(scene, scene.guardian, session_id=session["id"]))
    principal = principal_of(scene.guardian.id, "guardian")

    with frozen_clock(scene.ctx.tl.now):
        statements, page = count_statements(
            scene.url,
            lambda session: scheduling.list_change_requests(
                session,
                principal,
                student_id=None,
                class_session_id=None,
                status="all",
                limit=25,
                offset=0,
            ),
        )

    assert page.total == 3
    assert statements <= 5


def principal_of(user_id: uuid.UUID, *roles: str) -> Any:
    from app.core.authz import Principal

    return Principal(user_id=user_id, roles=frozenset(roles), permissions=frozenset())


# --- D3: withdraw --------------------------------------------------------------------


def test_the_requester_withdraws(scene):
    detail = created(create(scene, scene.guardian))

    assert code_of(withdraw(scene, scene.other_guardian, detail["id"])) == NOT_FOUND
    assert code_of(withdraw(scene, scene.guardian, detail["id"], version=9)) == STALE
    withdrawn = ok(withdraw(scene, scene.guardian, detail["id"]))

    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["can_withdraw"] is False
    assert withdrawn["resolved_at"] is not None
    assert approval_of(scene.url, detail["id"]).status == "cancelled"
    again = withdraw(scene, scene.guardian, detail["id"], version=2)
    assert code_of(again) == NOT_PENDING
    assert "details" not in again.json()
    # The session is free for a new request.
    created(create(scene, scene.ctx.tutor))


def test_withdrawing_after_the_start_is_allowed(scene):
    detail = created(create(scene, scene.guardian))

    assert (
        code_of(
            withdraw(scene, scene.guardian, detail["id"], at=scene.start + hours(1))
        )
        == OK
    )


# --- G2: the session detail ----------------------------------------------------------


def test_the_session_detail_names_the_pending_request(scene):
    def session_detail() -> dict[str, Any]:
        return ok(
            call(
                scene,
                scene.ctx.owner,
                None,
                lambda cookie: get(
                    scene.ctx.client,
                    cookie,
                    f"/api/v1/class-sessions/{scene.session['id']}",
                ),
            )
        )

    assert session_detail()["pending_change_request_id"] is None
    detail = created(create(scene, scene.guardian))
    assert session_detail()["pending_change_request_id"] == detail["id"]

    statements, found = count_statements(
        scene.url,
        lambda session: scheduling.get_class_session(
            session, uuid.UUID(scene.session["id"])
        ),
    )
    assert str(found.pending_change_request_id) == detail["id"]
    assert statements <= 8  # #55's seven, plus at most one (here none)

    withdraw(scene, scene.guardian, detail["id"])
    assert session_detail()["pending_change_request_id"] is None


# --- S3: closed allow-lists and the leak test ----------------------------------------


def test_family_bodies_are_closed_allow_lists(scene):
    detail = created(create(scene, scene.guardian, note="Nota"))
    page = ok(listing(scene, scene.guardian))

    for body in (
        detail,
        page["items"][0],
        ok(read(scene, scene.guardian, detail["id"])),
    ):
        assert set(body) == DETAIL_KEYS
        assert set(body["class_session"]) == SESSION_KEYS
        assert set(body["class_session"]["subject"]) == SUBJECT_KEYS
        assert set(body["student"]) == {"id", "display_name"}
        assert set(body["outcome"]) == OUTCOME_KEYS
    assert set(page) == {"items", "total", "limit", "offset"}
    for model in (
        scheduling_schema("ChangeRequestDetail"),
        scheduling_schema("ChangeRequestItem"),
        scheduling_schema("ChangeRequestSession"),
        scheduling_schema("ChangeRequestOutcome"),
        scheduling_schema("ChangeRequestDecision"),
    ):
        assert model.model_config.get("extra") == "forbid"


def scheduling_schema(name: str) -> Any:
    from app.modules.scheduling import change_request_schemas

    return getattr(change_request_schemas, name)


def test_no_family_response_leaks_a_tutor_a_room_or_another_family(scene):
    tl = scene.ctx.tl
    stranger_student = new_student(scene.url)
    stranger_session = one_off(
        scene.ctx,
        accept=True,
        student=stranger_student,
        room=scene.ctx.other_room,
        starts_at=scene.at(tl.week(2, 3), "10:00"),
    )
    texts = []
    detail = created(create(scene, scene.guardian, note="Nota"))
    texts.append(json.dumps(detail))
    texts.append(listing(scene, scene.guardian).text)
    texts.append(read(scene, scene.guardian, detail["id"]).text)
    texts.append(create(scene, scene.other_guardian).text)  # 409 with the id
    texts.append(create(scene, scene.guardian, session_id=stranger_session["id"]).text)
    texts.append(
        create(
            scene,
            scene.other_guardian,
            kind="reschedule",
            session_id=scene.session["id"],
            proposed_starts_at=scene.at(tl.week(2, 3), "10:00"),
        ).text
    )
    secrets = [
        str(scene.ctx.tutor.id),
        scene.ctx.tutor.username,
        str(scene.ctx.room),
        str(scene.ctx.other_room),
        str(stranger_student),
        stranger_session["id"],
    ]
    for text in texts:
        for secret in secrets:
            assert secret not in text, (secret, text)


# --- S4: rate limit ------------------------------------------------------------------


def test_the_11th_request_in_an_hour_is_429_and_4xx_count(scene):
    codes = [code_of(create(scene, scene.guardian))]
    codes.append(code_of(create(scene, scene.guardian, kind="reschedule")))  # 422
    for _ in range(8):
        codes.append(code_of(create(scene, scene.guardian)))  # 409 pending

    limited = create(scene, scene.guardian)

    assert codes[:2] == [CREATED, INVALID]
    assert set(codes[2:]) == {PENDING}
    assert code_of(limited) == (429, "rate_limited")
    assert limited.headers["Retry-After"]


# --- S5: logs ------------------------------------------------------------------------


def test_logs_never_carry_the_note_the_student_or_the_times(scene, caplog):
    note = "Nota privada 5c2f"
    proposed = scene.start + hours(3)
    with caplog.at_level(logging.DEBUG, logger="app"):
        created(
            create(
                scene,
                scene.guardian,
                kind="reschedule",
                proposed_starts_at=proposed,
                note=note,
            )
        )
        conflict = create(scene, scene.other_guardian, note=note)
    assert code_of(conflict) == PENDING

    secrets = [
        note,
        str(scene.ctx.student),
        proposed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
        proposed.isoformat(),
    ]
    records = [
        r for r in caplog.records if r.name == "app" or r.name.startswith("app.")
    ]
    assert records
    for record in records:
        text = " ".join(
            [record.getMessage(), *(str(v) for v in record.__dict__.values())]
        )
        for secret in secrets:
            assert secret not in text, (secret, record.name, text)


# --- A4: the time-zone dependency ----------------------------------------------------


def test_pending_reschedules_block_a_time_zone_change(scene):
    def pending() -> bool:
        async def _check(session: AsyncSession) -> bool:
            return await scheduling.has_pending_reschedule_requests(session)

        return in_uow(scene.url, _check)

    cancellation = created(create(scene, scene.guardian))
    withdraw(scene, scene.guardian, cancellation["id"])
    detail = created(
        create(
            scene,
            scene.guardian,
            kind="reschedule",
            proposed_starts_at=scene.start + hours(3),
        )
    )
    assert pending() is True
    withdraw(scene, scene.guardian, detail["id"])
    # Other tests' pending reschedules may exist in the module database; this
    # one no longer counts.
    from tests.integration.conftest import run_sql

    assert (
        run_sql(
            scene.url,
            "SELECT count(*) AS n FROM session_change_requests WHERE id = :id"
            " AND status = 'pending'",
            {"id": detail["id"]},
        )[0].n
        == 0
    )
