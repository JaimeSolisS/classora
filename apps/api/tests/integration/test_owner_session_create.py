"""One extra session of a group or a course cohort (#592 AC1-AC4, AC6-AC9)
through ``POST /api/v1/class-sessions`` with real logins; ``clock.now`` is
frozen per step.

The groups and cohorts use the series timeline
(:mod:`tests.integration.series_support`): "now" is a Sunday 12:00 (Mexico
City) and their Monday/Wednesday 16:00 series start the next day. The
one-off sessions go on other days (a Tuesday by default), so they never
overlap the series' own occurrences.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from httpx import Response

from app.modules.families.models import Student
from app.modules.identity.permissions import ACADEMIC_COORDINATOR, ADMINISTRATOR, OWNER
from app.modules.payroll import service as payroll
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.cohort_support import (
    cohort_body,
    create_cohort,
    new_template,
)
from tests.integration.cohort_support import enroll as enroll_cohort
from tests.integration.cohort_support import withdraw as withdraw_cohort
from tests.integration.conftest import run_sql
from tests.integration.group_support import (
    create_group,
    enroll,
    group_body,
    participants,
    series_of,
    withdraw,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import run_job
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    INVALID,
    change,
    close_center,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

SESSIONS = "/api/v1/class-sessions"
CONFLICT = (409, "scheduling_conflict")
FORBIDDEN = (403, "permission_denied")
OK = (200, None)
OVERRIDE_EVENT = "scheduling.session_override"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, Any]:
    subject = new_subject(module_seeded_url)
    return {
        "students": [new_student(module_seeded_url) for _ in range(4)],
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url, capacity=10),
    }


# --- Helpers -------------------------------------------------------------------------


def make_group(
    client: Any,
    cookie: Any,
    refs: dict[str, Any],
    starts_on: date,
    *,
    students: int = 2,
    **changes: Any,
) -> dict[str, Any]:
    changes.setdefault("room", refs["room"])
    changes.setdefault("tutor", refs["tutor"])
    response = create_group(
        client,
        cookie,
        group_body(
            subject=refs["subject"],
            starts_on=starts_on,
            students=refs["students"][:students],
            **changes,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def make_cohort(
    client: Any,
    cookie: Any,
    refs: dict[str, Any],
    starts_on: date,
    *,
    session_count: int = 10,
) -> dict[str, Any]:
    template = new_template(
        client, cookie, [refs["subject"]], session_count=session_count
    )
    response = create_cohort(
        client,
        cookie,
        cohort_body(
            template=template["id"],
            subject=refs["subject"],
            starts_on=starts_on,
            tutor=refs["tutor"],
            room=refs["room"],
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def one_off(
    owner: str,
    owner_id: Any,
    starts_at: datetime,
    *,
    minutes: int = 60,
    tutor: Any = None,
    room: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """A group (``owner="group"``) or course (``"course"``) create body."""
    field = "group_id" if owner == "group" else "cohort_id"
    return {
        "kind": owner,
        field: str(owner_id),
        "tutor_user_id": None if tutor is None else str(tutor),
        "classroom_id": None if room is None else str(room),
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(minutes=minutes)).isoformat(),
        **extra,
    }


def create_at(
    url: str, client: Any, at: datetime, payload: dict[str, Any], cookie: Any = None
) -> Response:
    with frozen_clock(at):
        return post(client, cookie or owner_cookie(url, client), SESSIONS, payload)


def created(response: Response) -> dict[str, Any]:
    assert code_of(response) == CREATED, response.text
    found: dict[str, Any] = response.json()
    return found


def students_of(detail: dict[str, Any]) -> set[str]:
    return {p["student_id"] for p in detail["participants"]}


def ids(*students: uuid.UUID) -> set[str]:
    return {str(s) for s in students}


def session_total(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM class_sessions")
    return int(row.n)


def session_row(url: str, session_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, kind, state, version, starts_at, series_id, original_start,"
        " is_exception, group_id, cohort_id FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return row


def enrollment_id(url: str, table: str, owner_field: str, owner_id: Any, student: Any):
    [row] = run_sql(
        url,
        f"SELECT id, version FROM {table} WHERE {owner_field} = :o"  # noqa: S608
        " AND student_id = :s AND withdrawn_at IS NULL",
        {"o": str(owner_id), "s": student},
    )
    return row


def errors_of(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"], e["code"]) for e in response.json()["errors"]]


def conflict_codes(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == CONFLICT, response.text
    return [(c["code"], c["severity"]) for c in response.json()["conflicts"]]


# --- AC1, AC2: the create ------------------------------------------------------------


def test_a_group_one_off_takes_the_group_subject_and_its_enrolled_students(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b = refs["students"][:2]
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)

    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off(
                "group",
                group["id"],
                tl.at(tl.week(1, 2), "16:00"),
                tutor=refs["tutor"],
                room=refs["room"],
            ),
        )
    )

    assert detail["kind"] == "group"
    assert detail["group_id"] == group["id"]
    assert detail["cohort_id"] is None
    assert detail["subject"]["id"] == str(refs["subject"])
    assert students_of(detail) == ids(a, b)
    assert detail["series_id"] is None
    assert detail["original_start"] is None
    assert detail["is_exception"] is False
    assert detail["state"] == "offered"
    offer = detail["pending_offer"]
    assert offer["scope"] == "session"
    assert offer["tutor"]["id"] == str(refs["tutor"])
    [note] = run_sql(
        url,
        "SELECT recipient_user_id FROM notifications"
        " WHERE notification_type = 'scheduling.offer_received'"
        " AND params->>'offer_id' = :id",
        {"id": offer["id"]},
    )
    assert note.recipient_user_id == refs["tutor"]
    row = session_row(url, detail["id"])
    assert (row.kind, row.series_id, row.original_start) == ("group", None, None)
    assert participants(url, detail["id"]) == ids(a, b)

    # Without a tutor it is unassigned.
    unassigned = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(1, 4), "16:00")),
        )
    )
    assert unassigned["state"] == "unassigned"
    assert unassigned["pending_offer"] is None
    assert students_of(unassigned) == ids(a, b)


def test_a_course_one_off_takes_the_actively_enrolled_students_only(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b, gone = refs["students"][:3]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        for student in (a, b, gone):
            assert code_of(enroll_cohort(client, cookie, cohort["id"], student)) == (
                CREATED
            )
        period = enrollment_id(
            url, "cohort_enrollments", "cohort_id", cohort["id"], gone
        )
        assert (
            code_of(
                withdraw_cohort(client, cookie, cohort["id"], period.id, period.version)
            )
            == OK
        )

    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off(
                "course",
                cohort["id"],
                tl.at(tl.week(2, 2), "16:00"),
                minutes=90,
                tutor=refs["tutor"],
                room=refs["room"],
            ),
        )
    )

    assert detail["kind"] == "course"
    assert detail["cohort_id"] == cohort["id"]
    assert detail["group_id"] is None
    assert detail["subject"]["id"] == str(refs["subject"])
    assert students_of(detail) == ids(a, b)
    assert detail["series_id"] is None
    assert detail["state"] == "offered"


# --- AC3: refusals -------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"student_id": str(uuid.uuid4())},
        {"subject_id": str(uuid.uuid4())},
        {"makeup_of_session_id": str(uuid.uuid4())},
    ],
)
def test_individual_members_are_unknown_fields(module_seeded_url, client, extra):
    url = module_seeded_url
    tl = timeline()
    before = session_total(url)
    [(field, _)] = extra.items()

    for owner in ("group", "course"):
        response = create_at(
            url,
            client,
            tl.now,
            one_off(owner, uuid.uuid4(), tl.at(tl.week(1, 2), "16:00")) | extra,
        )

        # The path has no union tag: ["body", <field>], as for individual.
        assert errors_of(response) == [(["body", field], "extra_forbidden")]
    assert session_total(url) == before


def test_an_unknown_kind_is_422(module_seeded_url, client):
    tl = timeline()
    payload = one_off("group", uuid.uuid4(), tl.at(tl.week(1, 2), "16:00"))

    response = create_at(module_seeded_url, client, tl.now, payload | {"kind": "x"})

    assert code_of(response) == INVALID


def test_an_unknown_group_or_cohort_is_422(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    before = session_total(url)
    start = tl.at(tl.week(1, 2), "16:00")

    group = create_at(url, client, tl.now, one_off("group", uuid.uuid4(), start))
    cohort = create_at(url, client, tl.now, one_off("course", uuid.uuid4(), start))

    assert errors_of(group) == [(["body", "group_id"], "group_unknown")]
    assert errors_of(cohort) == [(["body", "cohort_id"], "cohort_unknown")]
    assert session_total(url) == before


def test_an_inactive_enrolled_student_is_422_on_the_owner(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    in_uow(url, lambda s: change(s, Student, refs["students"][1], is_active=False))
    before = session_total(url)

    response = create_at(
        url,
        client,
        tl.now,
        one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00")),
    )

    assert errors_of(response) == [(["body", "group_id"], "student_inactive")]
    assert session_total(url) == before


def test_a_course_date_outside_the_cohort_dates_is_422(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        # Ten Monday/Wednesday sessions: from tl.monday to Wednesday of week 5.
        cohort = make_cohort(client, owner_cookie(url, client), refs, tl.monday)
    before = session_total(url)

    for day in (tl.monday - timedelta(days=1), tl.week(5, 4), tl.week(6, 2)):
        response = create_at(
            url,
            client,
            tl.now,
            one_off("course", cohort["id"], tl.at(day, "18:00")),
        )
        assert errors_of(response) == [
            (["body", "starts_at"], "session_outside_cohort_dates")
        ]
    assert session_total(url) == before

    # The first and last session dates themselves are inside.
    for day in (tl.monday, tl.week(5, 3)):
        created(
            create_at(
                url,
                client,
                tl.now,
                one_off("course", cohort["id"], tl.at(day, "13:00")),
            )
        )


def test_an_ended_group_or_cohort_is_409(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        # No tutor or room on the group: the cohort takes them at the same times.
        group = make_group(
            client, cookie, refs, tl.monday, count=2, tutor=None, room=None
        )
        cohort = make_cohort(client, cookie, refs, tl.monday, session_count=2)
    later = tl.at(tl.week(2, 1), "12:00")
    before = session_total(url)

    ended_group = create_at(
        url, client, later, one_off("group", group["id"], tl.at(tl.week(2, 2), "16:00"))
    )
    # Within the cohort's dates (Monday and Wednesday of week 1), which have
    # passed: the date check passes, the cohort has ended.
    ended_cohort = create_at(
        url,
        client,
        later,
        one_off("course", cohort["id"], tl.at(tl.week(1, 2), "16:00")),
    )

    assert code_of(ended_group) == (409, "group_ended")
    assert code_of(ended_cohort) == (409, "cohort_ended")
    assert session_total(url) == before


def test_a_coordinator_and_an_administrator_overriding_without_delegation_are_403(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    coordinator = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    admin = create_user(url, roles=(ADMINISTRATOR,))
    payload = one_off("group", group["id"], tl.at(tl.week(1, 7), "10:00"))
    before = session_total(url)

    with frozen_clock(tl.now):
        by_coordinator = post(
            client, sign_in(client, coordinator.username), SESSIONS, payload
        )
        by_admin = post(
            client,
            sign_in(client, admin.username),
            SESSIONS,
            payload
            | {"override": {"codes": ["outside_operating_hours"], "reason": "Repaso"}},
        )

    assert code_of(by_coordinator) == FORBIDDEN
    assert code_of(by_admin) == FORBIDDEN
    assert session_total(url) == before


# --- AC4: conflicts ------------------------------------------------------------------


def test_an_enrolled_student_with_a_class_then_is_a_hard_student_overlap(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b = refs["students"][:2]
    start = tl.at(tl.week(1, 2), "16:00")
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    busy = existing_session(url, starts_at=start, student_id=b)

    response = create_at(url, client, tl.now, one_off("group", group["id"], start))

    assert conflict_codes(response) == [("student_overlap", "hard")]
    [conflict] = response.json()["conflicts"]
    assert conflict["details"] == {
        "student_id": str(b),
        "class_session_ids": [str(busy)],
    }
    assert str(a) not in str(conflict)


def test_a_room_smaller_than_the_roster_is_hard(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    small = new_room(url, capacity=1)

    response = create_at(
        url,
        client,
        tl.now,
        one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00"), room=small),
    )

    assert conflict_codes(response) == [("room_capacity_exceeded", "hard")]


def test_a_roster_above_the_group_maximum_is_not_a_conflict(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday, students=1, max_students=1)
        over = enroll(
            client,
            cookie,
            group["id"],
            refs["students"][1],
            override={"codes": ["group_capacity_exceeded"], "reason": "Cupo extra"},
        )
        assert code_of(over) == CREATED, over.text

    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off(
                "group", group["id"], tl.at(tl.week(1, 2), "16:00"), room=refs["room"]
            ),
        )
    )

    assert students_of(detail) == ids(*refs["students"][:2])


def test_outside_opening_hours_is_soft_and_overridable(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    # A Sunday: the center has no opening hours.
    payload = one_off("group", group["id"], tl.at(tl.week(1, 7), "10:00"))

    refused = create_at(url, client, tl.now, payload)
    with frozen_clock(tl.now):
        owner = create_user(url, roles=(OWNER,))
        cookie = sign_in(client, owner.username)
    overridden = create_at(
        url,
        client,
        tl.now,
        payload
        | {"override": {"codes": ["outside_operating_hours"], "reason": "Repaso"}},
        cookie,
    )

    assert conflict_codes(refused) == [("outside_operating_hours", "soft")]
    detail = created(overridden)
    [stored] = detail["overrides"]
    assert stored["codes"] == ["outside_operating_hours"]
    assert stored["reason"] == "Repaso"
    [event] = events(url, record_id=uuid.UUID(detail["id"]), action=OVERRIDE_EVENT)
    assert event.actor_user_id == owner.id


def test_a_closure_date_is_center_closed(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    day = tl.week(2, 2)
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    close_center(url, day)

    response = create_at(
        url, client, tl.now, one_off("group", group["id"], tl.at(day, "16:00"))
    )

    assert ("center_closed", "soft") in conflict_codes(response)


# --- AC6: enrollment changes reach the one-off ----------------------------------------


def test_group_enrollment_changes_reach_a_future_one_off_only(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b, c = refs["students"][:3]
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    early = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00")),
        )
    )
    late = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(3, 2), "16:00")),
        )
    )
    # The early one-off has started when the roster changes.
    during = tl.at(tl.week(1, 2), "16:30")

    with frozen_clock(during):
        cookie = owner_cookie(url, client)
        assert code_of(enroll(client, cookie, group["id"], c)) == CREATED
    late_row = session_row(url, late["id"])
    assert participants(url, late["id"]) == ids(a, b, c)
    assert late_row.version == late["version"] + 1
    assert participants(url, early["id"]) == ids(a, b)

    period = enrollment_id(url, "group_enrollments", "group_id", group["id"], a)
    with frozen_clock(during):
        cookie = owner_cookie(url, client)
        response = withdraw(client, cookie, group["id"], period.id, period.version)
        assert code_of(response) == OK, response.text
    assert participants(url, late["id"]) == ids(b, c)
    assert participants(url, early["id"]) == ids(a, b)


def test_cohort_enrollment_changes_reach_a_future_one_off(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b = refs["students"][:2]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        assert code_of(enroll_cohort(client, cookie, cohort["id"], a)) == CREATED
    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("course", cohort["id"], tl.at(tl.week(2, 2), "16:00"), minutes=90),
        )
    )
    assert students_of(detail) == ids(a)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        assert code_of(enroll_cohort(client, cookie, cohort["id"], b)) == CREATED
    assert participants(url, detail["id"]) == ids(a, b)
    assert session_row(url, detail["id"]).version == detail["version"] + 1

    period = enrollment_id(url, "cohort_enrollments", "cohort_id", cohort["id"], a)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = withdraw_cohort(
            client, cookie, cohort["id"], period.id, period.version
        )
        assert code_of(response) == OK, response.text
    assert participants(url, detail["id"]) == ids(b)


# --- AC7: life cycle -----------------------------------------------------------------


def test_patch_cancel_the_nightly_job_and_a_series_cancel(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday)
    moved = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00")),
        )
    )
    kept = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(2, 2), "16:00")),
        )
    )
    dropped = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(3, 2), "16:00")),
        )
    )

    # PATCH moves it (the group lock is taken) and it stays no exception.
    new_start = tl.at(tl.week(1, 4), "17:00")
    with frozen_clock(tl.now):
        response = post_patch(
            client,
            owner_cookie(url, client),
            moved["id"],
            {
                "version": moved["version"],
                "starts_at": new_start.isoformat(),
                "ends_at": (new_start + timedelta(hours=1)).isoformat(),
            },
        )
    assert code_of(response) == OK, response.text
    assert response.json()["is_exception"] is False
    assert response.json()["series_id"] is None

    # Cancel works.
    with frozen_clock(tl.now):
        response = post(
            client,
            owner_cookie(url, client),
            f"{SESSIONS}/{dropped['id']}/cancel",
            {"version": dropped["version"], "reason": "Cierre por obra"},
        )
    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "cancelled"

    # The nightly job and a series cancellation leave the one-offs alone.
    before = {r: session_row(url, r) for r in (moved["id"], kept["id"])}
    run_job(url, tl.now + timedelta(days=1))
    [series] = series_of(url, group["id"])
    with frozen_clock(tl.now):
        response = post(
            client,
            owner_cookie(url, client),
            f"/api/v1/class-series/{series.id}/cancellations",
            {
                "mode": "following",
                "from_date": tl.monday.isoformat(),
                "reason": "Fin del grupo",
                "version": series.version,
            },
        )
    assert code_of(response) == OK, response.text
    for row_id, row in before.items():
        assert session_row(url, row_id) == row
    one_offs = run_sql(
        url,
        "SELECT id FROM class_sessions WHERE group_id = :g AND series_id IS NULL",
        {"g": group["id"]},
    )
    assert len(one_offs) == 3


def post_patch(client: Any, cookie: Any, session_id: Any, body: Any) -> Response:
    return patch(client, cookie, f"{SESSIONS}/{session_id}", body)


# --- AC8: an empty roster ------------------------------------------------------------


def test_a_group_without_students_gets_a_session_without_participants(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a = refs["students"][0]
    with frozen_clock(tl.now):
        group = make_group(
            client, owner_cookie(url, client), refs, tl.monday, students=0
        )

    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00")),
        )
    )

    assert detail["participants"] == []
    with frozen_clock(tl.now):
        assert code_of(enroll(client, owner_cookie(url, client), group["id"], a)) == (
            CREATED
        )
    assert participants(url, detail["id"]) == ids(a)


# --- AC9: billing and payroll ---------------------------------------------------------


def test_a_session_priced_group_charges_each_participant_of_the_one_off(
    module_seeded_url, client
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    a, b = new_student(url), new_student(url)
    for student in (a, b):
        ec.add_family(url, student)
    ec.group_session_price(url, subject, "100.00")
    with frozen_clock(tl.now):
        group = ec.make_group(
            client, owner_cookie(url, client), subject, tl.monday, [a, b]
        )
    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(1, 2), "16:00")),
        )
    )
    ec.confirm_sessions(url, [detail["id"]])

    ec.run_sessions(url)

    charges = run_sql(
        url,
        "SELECT student_id, amount FROM charges WHERE kind = 'session'"
        " AND class_session_id = :s",
        {"s": detail["id"]},
    )
    assert {(row.student_id, f"{row.amount:.2f}") for row in charges} == {
        (a, "100.00"),
        (b, "100.00"),
    }


def test_a_course_one_off_is_never_charged_and_counts_for_the_course_pay(
    module_seeded_url, client
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    a = new_student(url)
    ec.add_family(url, a)
    ec.cohort_fee(url, subject, "1000.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.enroll_cohort(client, cookie, cohort["id"], a)
    cohort_id = uuid.UUID(cohort["id"])

    async def planned(session: Any) -> int:
        rows = await scheduling.cohort_sessions(session, [cohort_id])
        return payroll.planned_sessions(row.state for row in rows)

    before = in_uow(url, planned)
    detail = created(
        create_at(
            url,
            client,
            tl.now,
            one_off("course", cohort["id"], tl.at(tl.week(2, 2), "16:00"), minutes=90),
        )
    )
    ec.confirm_sessions(url, [detail["id"]])

    ec.run_sessions(url)

    assert in_uow(url, planned) == before + 1
    assert (
        run_sql(
            url,
            "SELECT id FROM charges WHERE class_session_id = :s",
            {"s": detail["id"]},
        )
        == []
    )


def test_proration_counts_the_one_off(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        group = make_group(client, owner_cookie(url, client), refs, tl.monday, count=10)
    owner = scheduling.SeriesOwner.group(uuid.UUID(group["id"]))

    def counts() -> Any:
        return in_uow(
            url, lambda s: scheduling.enrollment_session_counts(s, owner, tl.now)
        )

    before = counts()
    created(
        create_at(
            url,
            client,
            tl.now,
            one_off("group", group["id"], tl.at(tl.week(2, 2), "16:00")),
        )
    )
    after = counts()

    assert before.total == 10
    assert (after.total, after.remaining) == (11, before.remaining + 1)
