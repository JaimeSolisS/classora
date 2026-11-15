"""Security of available slots and session requests (#59 S1-S6; H3):
cross-family access (IDOR) on every route, the adult consent rule, the
closed output allow-lists and the leak test, the per-user rate limits and
the logs. Real logins through the real app (``session_request_support``).
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.pagination import OffsetPage
from app.modules.identity.permissions import ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR
from app.modules.scheduling.request_schemas import (
    AvailableDay,
    AvailableSlot,
    AvailableSlots,
    SessionRequestDetail,
    SessionRequestItem,
    SessionRequestStudent,
    SessionRequestSubject,
)
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.session_request_support import (
    MONDAY_WINDOW,
    NOW,
    REQUESTS,
    Family,
    create_request,
    created,
    deactivate_guardian,
    get_slots,
    give_academic_consent,
    guardian_with_child,
    request_body,
    request_rows,
    revoke_academic_consent,
    slots_params,
    student_account,
    unlink,
    withdraw,
)
from tests.integration.test_class_session_create import (
    MONDAY,
    at,
    existing_session,
    new_subject,
    new_tutor,
)

PROBLEM_KEYS = {"type", "title", "status", "code", "detail", "request_id"}


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
    subject = new_subject(module_seeded_url)
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    return subject


def body_without_request_id(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body.pop("request_id", None)
    return body


def same_404(first: Response, second: Response) -> None:
    assert code_of(first) == (404, "not_found"), first.text
    assert body_without_request_id(first) == body_without_request_id(second)


# --- S1/S2: IDOR on every route ------------------------------------------------------


def test_another_familys_child_is_the_same_404_as_an_unknown_id(
    module_seeded_url, client, cookie, family, subject
):
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)
    theirs = created(
        create_request(client, other_cookie, request_body(other.student_id, subject))
    )
    unknown = uuid.uuid4()

    same_404(
        get_slots(client, cookie, slots_params(other.student_id, subject)),
        get_slots(client, cookie, slots_params(unknown, subject)),
    )
    same_404(
        create_request(client, cookie, request_body(other.student_id, subject)),
        create_request(client, cookie, request_body(unknown, subject)),
    )
    same_404(
        get(client, cookie, f"{REQUESTS}/{theirs['id']}"),
        get(client, cookie, f"{REQUESTS}/{unknown}"),
    )
    same_404(
        withdraw(client, cookie, theirs["id"], theirs["version"]),
        withdraw(client, cookie, unknown, 1),
    )
    listed = get(client, cookie, REQUESTS, student_id=str(other.student_id))
    assert listed.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
    assert request_rows(module_seeded_url, other.student_id)[0].status == "pending"


def test_a_student_cannot_act_for_another_student(module_seeded_url, client, subject):
    me, mine = student_account(module_seeded_url)
    other, theirs_student = student_account(module_seeded_url)
    other_cookie = sign_in(client, other.username)
    theirs = created(
        create_request(client, other_cookie, request_body(theirs_student, subject))
    )
    cookie = sign_in(client, me.username)

    assert code_of(
        get_slots(client, cookie, slots_params(theirs_student, subject))
    ) == (404, "not_found")
    assert code_of(
        create_request(client, cookie, request_body(theirs_student, subject))
    ) == (404, "not_found")
    assert code_of(get(client, cookie, f"{REQUESTS}/{theirs['id']}")) == (
        404,
        "not_found",
    )
    assert code_of(withdraw(client, cookie, theirs["id"], 1)) == (404, "not_found")
    assert get(client, cookie, REQUESTS).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }
    assert created(create_request(client, cookie, request_body(mine, subject)))


@pytest.mark.parametrize("change", ["deactivated", "unlinked"])
def test_a_guardian_who_lost_the_link_loses_their_old_request(
    module_seeded_url, client, cookie, family, subject, change
):
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    if change == "deactivated":
        deactivate_guardian(module_seeded_url, family.guardian_id)
    else:
        unlink(module_seeded_url, family)

    assert code_of(get(client, cookie, f"{REQUESTS}/{detail['id']}")) == (
        404,
        "not_found",
    )
    assert code_of(withdraw(client, cookie, detail["id"], detail["version"])) == (
        404,
        "not_found",
    )
    assert code_of(
        get_slots(client, cookie, slots_params(family.student_id, subject))
    ) == (404, "not_found")
    assert get(client, cookie, REQUESTS).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }
    # It stays pending for staff.
    assert request_rows(module_seeded_url, family.student_id)[0].status == "pending"


@pytest.mark.parametrize(
    "roles", [(ACADEMIC_COORDINATOR,), (FINANCE_STAFF,), (TUTOR,), ("owner",)]
)
def test_staff_and_tutors_get_404_and_an_empty_list(
    module_seeded_url, client, cookie, family, subject, roles
):
    created(create_request(client, cookie, request_body(family.student_id, subject)))
    account = create_user(module_seeded_url, roles=roles)
    viewer = sign_in(client, account.username)

    assert code_of(
        get_slots(client, viewer, slots_params(family.student_id, subject))
    ) == (404, "not_found")
    assert code_of(
        create_request(client, viewer, request_body(family.student_id, subject))
    ) == (404, "not_found")
    assert get(client, viewer, REQUESTS).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }


# --- E3: adults ----------------------------------------------------------------------


def test_a_guardian_of_an_adult_needs_the_students_consent(
    module_seeded_url, client, subject
):
    family = guardian_with_child(module_seeded_url, adult=True)
    cookie = sign_in(client, family.user.username)

    refused = get_slots(client, cookie, slots_params(family.student_id, subject))
    refused_create = create_request(
        client, cookie, request_body(family.student_id, subject)
    )
    give_academic_consent(module_seeded_url, family)
    allowed = get_slots(client, cookie, slots_params(family.student_id, subject))
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    revoke_academic_consent(module_seeded_url, family)
    again = get_slots(client, cookie, slots_params(family.student_id, subject))

    assert code_of(refused) == (403, "student_consent_required")
    assert set(refused.json()) == PROBLEM_KEYS
    assert code_of(refused_create) == (403, "student_consent_required")
    assert allowed.status_code == 200
    assert code_of(again) == (403, "student_consent_required")
    # Without the consent the request is hidden again (details hide).
    assert code_of(get(client, cookie, f"{REQUESTS}/{detail['id']}")) == (
        404,
        "not_found",
    )
    assert get(client, cookie, REQUESTS).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }


def test_a_guardian_with_consent_from_the_start(module_seeded_url, client, subject):
    family = guardian_with_child(module_seeded_url, adult=True, consent=True)
    cookie = sign_in(client, family.user.username)

    assert (
        get_slots(client, cookie, slots_params(family.student_id, subject)).status_code
        == 200
    )


# --- S3: closed allow-lists and the leak test ----------------------------------------


def test_the_output_models_are_closed_allow_lists():
    expected = {
        AvailableSlots: {
            "time_zone",
            "student_id",
            "subject_id",
            "duration_minutes",
            "from_",
            "to",
            "days",
        },
        AvailableDay: {"date", "slots"},
        AvailableSlot: {"starts_at", "ends_at", "already_requested"},
        SessionRequestDetail: {
            "id",
            "student",
            "subject",
            "starts_at",
            "ends_at",
            "duration_minutes",
            "status",
            "note",
            "requested_by_me",
            "can_withdraw",
            "created_at",
            "resolved_at",
            "version",
            # #60 E5/G1: only the reason, never the session, tutor or room.
            "rejection_reason",
        },
        SessionRequestStudent: {"id", "display_name"},
        SessionRequestSubject: {"id", "name", "education_level_name"},
    }
    for model, fields in expected.items():
        assert set(model.model_fields) == fields, model
        assert model.model_config.get("extra") == "forbid", model
    assert set(SessionRequestItem.model_fields) == expected[SessionRequestDetail]
    # The list is the #21 offset page of closed items.
    assert set(OffsetPage[SessionRequestItem].model_fields) == {
        "items",
        "total",
        "limit",
        "offset",
    }


def test_no_family_response_leaks_tutors_sessions_or_other_families(
    module_seeded_url, client, cookie, family
):
    subject = new_subject(module_seeded_url)
    secret_names = ["Profesora Secreta Uno", "Profesor Secreto Dos"]
    tutors = [
        new_tutor(
            module_seeded_url,
            qualified_for=[subject],
            windows=MONDAY_WINDOW,
            display_name=name,
        )
        for name in secret_names
    ]
    booked = existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "11:00"),
        state="confirmed",
        tutor_user_id=tutors[0],
    )
    other = guardian_with_child(module_seeded_url)
    other_session = existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        state="unassigned",
        student_id=other.student_id,
    )
    own_session = existing_session(
        module_seeded_url,
        starts_at=at(MONDAY + timedelta(days=7), "10:00"),
        state="confirmed",
        tutor_user_id=tutors[1],
        student_id=family.student_id,
    )
    [other_row] = run_sql(
        module_seeded_url,
        "SELECT given_names || ' ' || family_names AS name FROM students WHERE id = :s",
        {"s": other.student_id},
    )
    secrets = [
        *map(str, tutors),
        *secret_names,
        str(other.student_id),
        other_row.name,
        str(booked),
        str(other_session),
        str(own_session),
        str(other.user.id),
    ]

    responses = [
        get_slots(
            client,
            cookie,
            slots_params(
                family.student_id, subject, MONDAY, MONDAY + timedelta(days=7)
            ),
        ),
        get_slots(
            client, cookie, slots_params(family.student_id, subject, duration=50)
        ),
        get_slots(client, cookie, slots_params(other.student_id, subject)),
    ]
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    responses += [
        create_request(
            client,
            cookie,
            request_body(family.student_id, subject, at(MONDAY, "10:30")),
        ),
        create_request(
            client,
            cookie,
            request_body(family.student_id, subject, at(MONDAY, "12:00")),
        ),
        create_request(
            client,
            cookie,
            request_body(family.student_id, subject, "2031-01-06T10:15:00-06:00"),
        ),
        create_request(client, cookie, request_body(other.student_id, subject)),
        get(client, cookie, REQUESTS),
        get(client, cookie, f"{REQUESTS}/{detail['id']}"),
        withdraw(client, cookie, detail["id"], 99),
        withdraw(client, cookie, detail["id"], detail["version"]),
    ]
    statuses = {r.status_code for r in responses}
    assert {200, 404, 409, 422} <= statuses
    for response in responses:
        for secret in secrets:
            assert secret not in response.text, (secret, response.text)
    for response in responses:
        if response.status_code == 409:
            assert set(response.json()) == PROBLEM_KEYS, response.text


# --- S4: per-user rate limits --------------------------------------------------------


def test_the_61st_slot_search_is_429_and_4xx_count(
    module_seeded_url, client, cookie, family, subject
):
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)
    bad = slots_params(family.student_id, subject, duration=50)
    for _ in range(60):
        assert get_slots(client, cookie, bad).status_code == 422

    limited = get_slots(client, cookie, slots_params(family.student_id, subject))
    neighbour = get_slots(client, other_cookie, slots_params(other.student_id, subject))

    assert code_of(limited) == (429, "rate_limited")
    assert limited.headers["retry-after"] == "900"
    assert limited.json()["detail"] == "Too many attempts. Try again later."
    # Same client IP, another user: their own budget.
    assert neighbour.status_code == 200


def test_the_11th_request_in_an_hour_is_429(
    module_seeded_url, client, cookie, family, subject
):
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)
    bad = request_body(family.student_id, subject, "2031-01-06T10:15:00-06:00")
    for _ in range(10):
        assert create_request(client, cookie, bad).status_code == 422

    limited = create_request(client, cookie, request_body(family.student_id, subject))
    neighbour = create_request(
        client, other_cookie, request_body(other.student_id, subject)
    )

    assert code_of(limited) == (429, "rate_limited")
    assert limited.headers["retry-after"] == "3600"
    assert request_rows(module_seeded_url, family.student_id) == []
    assert created(neighbour)


def test_the_counters_store_hashed_user_keys(
    module_seeded_url, client, cookie, family, subject
):
    from app.core.ratelimit import key_hash

    get_slots(client, cookie, slots_params(family.student_id, subject))

    rows = run_sql(
        module_seeded_url,
        "SELECT policy, key_hash, count FROM rate_limit_counters"
        " WHERE policy = 'available_slots.user'",
    )
    assert [(r.policy, bytes(r.key_hash), r.count) for r in rows] == [
        (
            "available_slots.user",
            key_hash("available_slots.user", str(family.user.id)),
            1,
        )
    ]


# --- S6: logs ------------------------------------------------------------------------


def test_logs_never_carry_the_note_the_student_or_the_times(
    module_seeded_url, client, cookie, family, subject, caplog
):
    note = "Nota privada de la familia 7d1e"
    with caplog.at_level(logging.DEBUG, logger="app"):
        created(
            create_request(
                client, cookie, request_body(family.student_id, subject, note=note)
            )
        )
        conflict = create_request(
            client, cookie, request_body(family.student_id, subject, note=note)
        )
        get_slots(client, cookie, slots_params(family.student_id, subject))
    assert code_of(conflict) == (409, "session_request_overlap")

    secrets = [note, str(family.student_id), "2031-01-06"]
    # Only the app's own loggers: the test client's request lines (httpx,
    # emitted once another test in the process configured logging) carry
    # the query string by design and are not application logs.
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
