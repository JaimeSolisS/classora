"""Losing the consent basis suspends a minor's login (#35 E; owner decision
E3 (a): the login needs at least one valid consent).

Real logins through the real app. A suspension goes through
``identity.service.change_user_state(active → suspended)``: the student's
sessions end (the same cookie gets ``401``) and
``families.student_login_suspended`` is written.
"""

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import patch
from tests.integration.consent_support import (
    consent_rows,
    consents_of,
    given,
    revoke,
    set_account,
    user_state,
)
from tests.integration.guardian_support import (
    create_guardian,
    linked,
    set_active,
    unlink,
)
from tests.integration.invitation_support import (
    invite,
    reauthenticate,
    redeem,
    sign_in,
    token_of,
)
from tests.integration.lifecycle_support import code_of, events, me
from tests.integration.student_support import create_student, student_path, student_row
from tests.integration.user_admin_support import state_action, version_of

MINOR = "2014-03-01"
ADULT = "2000-01-01"
SUSPENDED_EVENT = "families.student_login_suspended"


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


@dataclass
class Login:
    """A minor S with an active login US, two linked guardians: G1 (account
    U1, an in-app consent) and G2 (no account, a paper consent)."""

    student: dict[str, Any]
    user: CreatedUser
    u1: CreatedUser
    g1: dict[str, Any]
    g2: dict[str, Any]
    c1: dict[str, Any]
    c2: dict[str, Any]


def minor_login(
    url: str, client: TestClient, staff: Any, *, birthdate: str = MINOR
) -> Login:
    student = create_student(client, staff, birthdate=birthdate)
    u1 = create_user(url, roles=(GUARDIAN,))
    g1 = create_guardian(client, staff, user_id=str(u1.id))
    g2 = create_guardian(client, staff)
    linked(client, staff, student["id"], g1["id"])
    linked(client, staff, student["id"], g2["id"], "father")
    c1 = given(
        client, sign_in(client, u1.username), student["id"], g1["id"], method="in_app"
    )
    c2 = given(client, staff, student["id"], g2["id"])
    user = create_user(url, roles=(STUDENT,))
    response = set_account(client, staff, student, user.id)
    assert response.status_code == 200, response.text
    return Login(response.json(), user, u1, g1, g2, c1, c2)


def suspension_events(url: str, user_id: uuid.UUID) -> list[Any]:
    return events(url, record_type="users", record_id=user_id, action=SUSPENDED_EVENT)


# --- E2: revocation ------------------------------------------------------------------


def test_revoking_one_of_two_keeps_the_login_and_the_last_one_suspends_it(
    module_seeded_url, client, staff
):
    login = minor_login(module_seeded_url, client, staff)
    session = sign_in(client, login.user.username)
    guardian = sign_in(client, login.u1.username)

    first = revoke(client, guardian, login.student["id"], login.c1["id"])
    assert first.status_code == 200, first.text
    assert first.json()["student_login_suspended"] is False
    assert user_state(module_seeded_url, login.user.id) == "active"
    assert me(client, session).status_code == 200

    overview = consents_of(client, staff, login.student["id"]).json()
    last = [c for c in overview["consents"] if c["id"] == login.c2["id"]][0]
    assert last["suspends_login_on_revoke"] is True

    second = revoke(client, staff, login.student["id"], login.c2["id"])

    assert second.status_code == 200, second.text
    assert second.json()["student_login_suspended"] is True
    assert user_state(module_seeded_url, login.user.id) == "suspended"
    assert me(client, session).status_code == 401
    [event] = suspension_events(module_seeded_url, login.user.id)
    assert event.details == {
        "student_id": login.student["id"],
        "consent_id": login.c2["id"],
        "cause": "consent_revoked",
    }
    [revoked] = events(
        module_seeded_url,
        record_type="users",
        record_id=login.user.id,
        action="identity.sessions_revoked",
    )
    assert revoked.details is not None
    assert revoked.details["reason"] == "suspended"
    states = [
        e.changes["state"]
        for e in events(module_seeded_url, record_type="users", record_id=login.user.id)
        if e.action == "update" and e.changes and "state" in e.changes
    ]
    assert states == [{"old": "active", "new": "suspended"}]


def test_with_two_consents_neither_suspends_on_its_own(
    module_seeded_url, client, staff
):
    login = minor_login(module_seeded_url, client, staff)

    overview = consents_of(client, staff, login.student["id"]).json()

    assert [c["suspends_login_on_revoke"] for c in overview["consents"]] == [
        False,
        False,
    ]


# --- E3: unlinking a guardian --------------------------------------------------------


def test_unlinking_revokes_the_guardians_records_then_suspends(
    module_seeded_url, client, owner, staff
):
    login = minor_login(module_seeded_url, client, staff)
    session = sign_in(client, login.user.username)
    assert revoke(client, staff, login.student["id"], login.c2["id"]).status_code == (
        200
    )
    assert user_state(module_seeded_url, login.user.id) == "active"

    assert unlink(client, staff, login.student["id"], login.g1["id"]).status_code == 204

    rows = {str(r.id): r for r in consent_rows(module_seeded_url, login.student["id"])}
    unlinked = rows[login.c1["id"]]
    assert unlinked.revocation_cause == "guardian_unlinked"
    assert unlinked.revoked_by_user_id == owner.id
    assert user_state(module_seeded_url, login.user.id) == "suspended"
    assert me(client, session).status_code == 401
    [event] = suspension_events(module_seeded_url, login.user.id)
    assert event.details == {
        "student_id": login.student["id"],
        "consent_id": login.c1["id"],
        "cause": "guardian_unlinked",
    }


def test_unlinking_revokes_both_scopes(module_seeded_url, client, staff):
    student = create_student(client, staff, birthdate=ADULT)
    guardian = create_guardian(client, staff)
    linked(client, staff, student["id"], guardian["id"])
    given(
        client,
        staff,
        student["id"],
        guardian["id"],
        scope="guardian_academic_access",
    )

    assert unlink(client, staff, student["id"], guardian["id"]).status_code == 204

    [row] = consent_rows(module_seeded_url, student["id"])
    assert row.revocation_cause == "guardian_unlinked"


def test_unlinking_one_of_two_consenting_guardians_keeps_the_login(
    module_seeded_url, client, staff
):
    login = minor_login(module_seeded_url, client, staff)

    assert unlink(client, staff, login.student["id"], login.g1["id"]).status_code == 204

    assert user_state(module_seeded_url, login.user.id) == "active"
    assert suspension_events(module_seeded_url, login.user.id) == []


# --- E4: deactivating a guardian profile ---------------------------------------------


def test_deactivating_the_last_consenting_guardian_suspends_until_staff_reactivate(
    module_seeded_url, client, owner, staff
):
    login = minor_login(module_seeded_url, client, staff)
    assert revoke(client, staff, login.student["id"], login.c2["id"]).status_code == (
        200
    )

    inactive = set_active(client, staff, login.g1, False)

    assert user_state(module_seeded_url, login.user.id) == "suspended"
    rows = {str(r.id): r for r in consent_rows(module_seeded_url, login.student["id"])}
    assert rows[login.c1["id"]].revoked_at is None  # kept, but no longer counts
    [event] = suspension_events(module_seeded_url, login.user.id)
    assert event.details == {
        "student_id": login.student["id"],
        "consent_id": None,
        "cause": "guardian_deactivated",
    }

    set_active(client, staff, inactive, True)
    assert user_state(module_seeded_url, login.user.id) == "suspended"
    # Staff reactivate through #31, which passes the gate again.
    reauthenticate(client, staff)
    version = version_of(module_seeded_url, login.user.id)
    response = state_action(client, staff, login.user.id, "reactivate", version)
    assert code_of(response) == (200, None)
    assert user_state(module_seeded_url, login.user.id) == "active"


# --- E5: the birthdate change --------------------------------------------------------


def test_a_birthdate_that_makes_an_unconsented_login_a_minors_is_409(
    module_seeded_url, client, staff
):
    student = create_student(client, staff, birthdate=ADULT)
    user = create_user(module_seeded_url, roles=(STUDENT,))
    detail = set_account(client, staff, student, user.id).json()

    response = patch(
        client,
        staff,
        student_path(student["id"]),
        {"version": detail["version"], "birthdate": MINOR},
    )

    assert code_of(response) == (409, "guardian_consent_required")
    assert response.json()["errors"][0]["path"] == ["body", "birthdate"]
    row = student_row(module_seeded_url, student["id"])
    assert (str(row.birthdate), row.version) == (ADULT, detail["version"])
    assert user_state(module_seeded_url, user.id) == "active"


def test_a_birthdate_change_with_a_consent_or_without_an_active_login_is_allowed(
    module_seeded_url, client, staff
):
    login = minor_login(module_seeded_url, client, staff, birthdate="2010-06-01")
    ok = patch(
        client,
        staff,
        student_path(login.student["id"]),
        {"version": login.student["version"], "birthdate": MINOR},
    )
    assert ok.status_code == 200, ok.text

    pending_student = create_student(client, staff, birthdate=ADULT)
    pending = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    detail = set_account(client, staff, pending_student, pending.id).json()
    response = patch(
        client,
        staff,
        student_path(pending_student["id"]),
        {"version": detail["version"], "birthdate": MINOR},
    )
    assert response.status_code == 200, response.text


# --- E6: adults ----------------------------------------------------------------------


def test_revoking_after_the_18th_birthday_changes_nothing_else(
    module_seeded_url, client, owner, staff, clock
):
    # 18 tomorrow in Mexico City (CLOCK_START is 2026-10-18 12:00 UTC).
    login = minor_login(module_seeded_url, client, staff, birthdate="2008-10-19")
    assert revoke(client, staff, login.student["id"], login.c2["id"]).status_code == (
        200
    )
    clock.set(datetime(2026, 10, 19, 6, 0, tzinfo=UTC))  # midnight, -06:00

    staff_now = sign_in(client, owner.username)
    response = revoke(client, staff_now, login.student["id"], login.c1["id"])

    assert response.status_code == 200, response.text
    assert response.json()["student_login_suspended"] is False
    assert user_state(module_seeded_url, login.user.id) == "active"


# --- E7: concurrency -----------------------------------------------------------------


@pytest.mark.parametrize("attempt", range(3))
def test_revocation_racing_a_redemption_never_leaves_an_unconsented_active_login(
    module_seeded_url, client, staff, attempt
):
    reauthenticate(client, staff)
    invited = invite(client, staff, role=STUDENT)
    assert invited.status_code == 201, invited.text
    user_id = uuid.UUID(invited.json()["user_id"])
    token = token_of(invited)
    student = create_student(client, staff, birthdate=MINOR)
    guardian = create_guardian(client, staff)
    linked(client, staff, student["id"], guardian["id"])
    record = given(client, staff, student["id"], guardian["id"])
    assert set_account(client, staff, student, user_id).status_code == 200
    results: dict[str, tuple[int, str | None]] = {}
    start = threading.Barrier(2)

    def run(name: str) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            if name == "redeem":
                results[name] = code_of(redeem(own_client, token))
            else:
                results[name] = code_of(
                    revoke(own_client, staff, student["id"], record["id"])
                )

    threads = [threading.Thread(target=run, args=(n,)) for n in ("redeem", "revoke")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results["revoke"] == (200, None)
    assert results["redeem"] in ((204, None), (409, "guardian_consent_required"))
    state = user_state(module_seeded_url, user_id)
    assert state != "active"
    assert state == ("suspended" if results["redeem"][0] == 204 else "pending")
