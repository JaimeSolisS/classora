"""Audit of consent records, account links and suspensions (#35 G).

``consent_records`` is ``Audited`` with nothing excluded: a ``create`` per
record and an ``update`` per revocation. An account link is a ``students``
``update`` of ``user_id``. A suspension by loss of consent writes
``families.student_login_suspended``. Refusals write nothing, and no audit
row holds a password or a hash.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.consent_support import (
    give,
    given,
    revoke,
    set_account,
    user_state,
)
from tests.integration.guardian_support import create_guardian, linked
from tests.integration.invitation_support import (
    invite,
    reauthenticate,
    redeem,
    sign_in,
    token_of,
)
from tests.integration.lifecycle_support import code_of, events, no_secret_in_audit
from tests.integration.student_support import create_student

MINOR = "2014-03-01"
ADULT = "2000-01-01"
PASSWORD = "a-brand-new-password"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def staff(client, owner):
    return sign_in(client, owner.username)


def audit_ids(url: str) -> set[uuid.UUID]:
    return {row.id for row in run_sql(url, "SELECT id FROM audit_events")}


def by_id(url: str, record_type: str, record_id: Any) -> list[Any]:
    return events(url, record_type=record_type, record_id=uuid.UUID(str(record_id)))


def test_a_consent_and_its_revocation_are_audited(
    module_seeded_url, client, owner, staff
):
    student = create_student(client, staff, birthdate=MINOR)
    guardian = create_guardian(client, staff)
    linked(client, staff, student["id"], guardian["id"])

    created = give(client, staff, student["id"], guardian["id"])
    assert created.status_code == 201, created.text
    record = created.json()
    revoked = revoke(client, staff, student["id"], record["id"])
    assert revoked.status_code == 200

    create_event, update_event = by_id(
        module_seeded_url, "consent_records", record["id"]
    )
    assert create_event.action == "create"
    assert create_event.actor_user_id == owner.id
    assert str(create_event.request_id) == created.headers["X-Request-ID"]
    assert create_event.changes is not None
    assert set(create_event.changes) == {
        "student_id",
        "guardian_id",
        "scope",
        "method",
        "notice_version_id",
        "granted_at",
        "recorded_by_user_id",
        "revoked_at",
        "revoked_by_user_id",
        "revocation_cause",
    }
    assert create_event.changes["scope"] == {"old": None, "new": "student_login"}
    assert update_event.action == "update"
    assert str(update_event.request_id) == revoked.headers["X-Request-ID"]
    assert update_event.changes is not None
    assert set(update_event.changes) == {
        "revoked_at",
        "revoked_by_user_id",
        "revocation_cause",
    }
    assert update_event.changes["revocation_cause"] == {"old": None, "new": "revoked"}


def test_an_account_link_is_a_students_update(module_seeded_url, client, owner, staff):
    student = create_student(client, staff)
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")

    response = set_account(client, staff, student, person.id)

    assert response.status_code == 200
    [event] = [
        e
        for e in by_id(module_seeded_url, "students", student["id"])
        if e.action == "update"
    ]
    assert event.actor_user_id == owner.id
    assert str(event.request_id) == response.headers["X-Request-ID"]
    assert event.changes == {"user_id": {"old": None, "new": str(person.id)}}


def test_a_suspension_writes_the_named_event_and_no_secret(
    module_seeded_url, client, owner, staff
):
    reauthenticate(client, staff)
    invited = invite(client, staff, role=STUDENT)
    user_id = uuid.UUID(invited.json()["user_id"])
    student = create_student(client, staff, birthdate=MINOR)
    guardian_user = create_user(module_seeded_url, roles=(GUARDIAN,))
    guardian = create_guardian(client, staff, user_id=str(guardian_user.id))
    linked(client, staff, student["id"], guardian["id"])
    record = given(
        client,
        sign_in(client, guardian_user.username),
        student["id"],
        guardian["id"],
        method="in_app",
    )
    assert set_account(client, staff, student, user_id).status_code == 200
    assert code_of(redeem(client, token_of(invited), PASSWORD)) == (204, None)
    [row] = run_sql(
        module_seeded_url,
        "SELECT password_hash FROM users WHERE id = :id",
        {"id": user_id},
    )

    guardian_cookie = sign_in(client, guardian_user.username)
    response = revoke(client, guardian_cookie, student["id"], record["id"])

    assert response.json()["student_login_suspended"] is True
    assert user_state(module_seeded_url, user_id) == "suspended"
    [event] = events(
        module_seeded_url,
        record_type="users",
        record_id=user_id,
        action="families.student_login_suspended",
    )
    assert event.actor_user_id == guardian_user.id
    assert str(event.request_id) == response.headers["X-Request-ID"]
    assert event.details == {
        "student_id": student["id"],
        "consent_id": record["id"],
        "cause": "consent_revoked",
    }
    no_secret_in_audit(module_seeded_url, PASSWORD, row.password_hash)


def test_refusals_write_nothing(module_seeded_url, client, staff):
    student = create_student(client, staff, birthdate=MINOR)
    adult = create_student(client, staff, birthdate=ADULT)
    guardian = create_guardian(client, staff)
    linked(client, staff, student["id"], guardian["id"])
    record = given(client, staff, student["id"], guardian["id"])
    assert revoke(client, staff, student["id"], record["id"]).status_code == 200
    taken = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    assert set_account(client, staff, adult, taken.id).status_code == 200
    active = create_user(module_seeded_url, roles=(STUDENT,))
    tutor = sign_in(client, create_user(module_seeded_url).username)
    reauthenticate(client, staff)
    invited = invite(client, staff, role=STUDENT)
    before = audit_ids(module_seeded_url)

    refusals = [
        # 403, 404, 409, 422 on the consent routes
        give(client, tutor, student["id"], guardian["id"]),
        give(client, staff, "00000000-0000-4000-8000-000000000999", guardian["id"]),
        revoke(client, staff, student["id"], record["id"]),
        give(client, staff, student["id"], guardian["id"], method="in_app"),
        # 409 and 422 on the account route, and the gate there
        set_account(client, staff, student, taken.id),
        set_account(client, staff, student, "00000000-0000-4000-8000-000000000999"),
        set_account(client, staff, student, active.id),
        # the gate on redemption (no profile linked)
        redeem(client, token_of(invited), PASSWORD),
    ]

    assert [code_of(r) for r in refusals] == [
        (403, "permission_denied"),
        (404, "not_found"),
        (409, "consent_already_revoked"),
        (422, "consent_method_invalid"),
        (409, "student_account_in_use"),
        (422, "student_account_unavailable"),
        (409, "guardian_consent_required"),
        (409, "student_profile_required"),
    ]
    assert audit_ids(module_seeded_url) == before
