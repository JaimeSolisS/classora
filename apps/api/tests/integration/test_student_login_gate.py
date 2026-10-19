"""The student-login gate (#35 B; ADR-0007 D18, #29 E3).

Every activation of an account holding ``student`` passes the families gate
inside ``identity.service``: #29 invitation redemption, #169 registration
approval, #31 reactivation and #170 role changes. A refused activation rolls
back (the invitation token stays usable, the request stays pending).
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.families.errors import StudentProfileRequiredError
from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.consent_support import (
    adult_profile_for,
    given,
    minor_profile_for,
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
from tests.integration.lifecycle_support import code_of, in_uow, signed_in
from tests.integration.registration_support import (
    REQUEST_PASSWORD,
    approve,
    register,
    request_row,
)
from tests.integration.registration_support import (
    unique_username as unique_requester,
)
from tests.integration.student_support import create_student
from tests.integration.user_admin_support import put_roles, state_action

PROFILE_REQUIRED = (409, "student_profile_required")
CONSENT_REQUIRED = (409, "guardian_consent_required")
MINOR = "2014-03-01"
ADULT = "2000-01-01"
NEW_PASSWORD = "a-brand-new-password"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def staff(client, owner):
    cookie = sign_in(client, owner.username)
    reauthenticate(client, cookie)
    return cookie


def invited_student(client: TestClient, cookie: Any) -> tuple[uuid.UUID, str]:
    response = invite(client, cookie, role=STUDENT)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["user_id"]), token_of(response)


def linked_profile(
    client: TestClient, cookie: Any, user_id: uuid.UUID, birthdate: str
) -> dict[str, Any]:
    student = create_student(client, cookie, birthdate=birthdate)
    response = set_account(client, cookie, student, user_id)
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def consenting_guardian(
    client: TestClient, cookie: Any, student_id: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A linked guardian (no login) with a paper login consent."""
    guardian = create_guardian(client, cookie)
    linked(client, cookie, student_id, guardian["id"])
    return guardian, given(client, cookie, student_id, guardian["id"])


# --- #29 invitation redemption ---------------------------------------------------


def test_an_unlinked_student_account_cannot_be_redeemed_and_the_token_stays_usable(
    module_seeded_url, client, staff
):
    user_id, token = invited_student(client, staff)

    assert code_of(redeem(client, token, NEW_PASSWORD)) == PROFILE_REQUIRED
    assert user_state(module_seeded_url, user_id) == "pending"

    linked_profile(client, staff, user_id, ADULT)
    assert code_of(redeem(client, token, NEW_PASSWORD)) == (204, None)
    assert user_state(module_seeded_url, user_id) == "active"


def test_a_minor_needs_a_consent_then_the_student_can_sign_in(
    module_seeded_url, client, staff
):
    user_id, token = invited_student(client, staff)
    student = linked_profile(client, staff, user_id, MINOR)

    assert code_of(redeem(client, token, NEW_PASSWORD)) == CONSENT_REQUIRED
    assert user_state(module_seeded_url, user_id) == "pending"

    consenting_guardian(client, staff, student["id"])
    assert code_of(redeem(client, token, NEW_PASSWORD)) == (204, None)
    assert user_state(module_seeded_url, user_id) == "active"
    assert signed_in(client, username_of(module_seeded_url, user_id), NEW_PASSWORD)


def username_of(url: str, user_id: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT username FROM users WHERE id = :id", {"id": user_id})
    return str(row.username)


def test_an_adult_needs_no_consent(module_seeded_url, client, staff):
    user_id, token = invited_student(client, staff)
    linked_profile(client, staff, user_id, ADULT)

    assert code_of(redeem(client, token, NEW_PASSWORD)) == (204, None)
    assert user_state(module_seeded_url, user_id) == "active"


@pytest.mark.parametrize("loss", ["unlinked", "inactive", "revoked"])
def test_a_consent_whose_basis_is_gone_does_not_count(
    module_seeded_url, client, staff, loss
):
    user_id, token = invited_student(client, staff)
    student = linked_profile(client, staff, user_id, MINOR)
    guardian, record = consenting_guardian(client, staff, student["id"])
    if loss == "unlinked":
        assert unlink(client, staff, student["id"], guardian["id"]).status_code == 204
    elif loss == "inactive":
        set_active(client, staff, guardian, False)
    else:
        assert code_of(revoke(client, staff, student["id"], record["id"]))[0] == 200

    assert code_of(redeem(client, token, NEW_PASSWORD)) == CONSENT_REQUIRED
    assert user_state(module_seeded_url, user_id) == "pending"


# --- #169 registration approval ------------------------------------------------------


def test_approving_a_minors_student_request_needs_a_consent(
    module_seeded_url, client, staff
):
    username = unique_requester()
    assert code_of(register(client, username=username, role=STUDENT))[0] == 202
    row = request_row(module_seeded_url, username)
    student = linked_profile(client, staff, row.user_id, MINOR)

    assert code_of(approve(client, staff, row.id)) == CONSENT_REQUIRED
    after = request_row(module_seeded_url, username)
    assert (after.state, after.status) == ("pending", "pending")

    consenting_guardian(client, staff, student["id"])
    assert code_of(approve(client, staff, row.id)) == (200, None)
    assert user_state(module_seeded_url, row.user_id) == "active"
    assert signed_in(client, username, REQUEST_PASSWORD)


def test_approving_a_student_request_without_a_profile_is_refused(
    module_seeded_url, client, staff
):
    username = unique_requester()
    assert code_of(register(client, username=username, role=STUDENT))[0] == 202
    row = request_row(module_seeded_url, username)

    assert code_of(approve(client, staff, row.id)) == PROFILE_REQUIRED
    assert request_row(module_seeded_url, username).status == "pending"


# --- #31 reactivation ----------------------------------------------------------------


def test_reactivating_a_suspended_minor_without_consent_is_refused(
    module_seeded_url, client, staff
):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="suspended")
    minor_profile_for(module_seeded_url, person.id)

    response = state_action(client, staff, person.id, "reactivate", 1)

    assert code_of(response) == CONSENT_REQUIRED
    assert user_state(module_seeded_url, person.id) == "suspended"


def test_reactivating_a_suspended_minor_with_consent_works(
    module_seeded_url, client, staff
):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="suspended")
    minor_profile_for(module_seeded_url, person.id, consenting=True)

    assert code_of(state_action(client, staff, person.id, "reactivate", 1)) == (
        200,
        None,
    )
    assert user_state(module_seeded_url, person.id) == "active"


# --- #170 role changes ---------------------------------------------------------------


def test_adding_student_to_an_active_user_without_a_profile_is_refused(
    module_seeded_url, client, staff
):
    person = create_user(module_seeded_url, roles=(TUTOR,))

    response = put_roles(client, staff, person.id, [STUDENT, TUTOR], 1)

    assert code_of(response) == PROFILE_REQUIRED
    assert in_uow(
        module_seeded_url, lambda s: identity.get_account_reference(s, person.id)
    ).roles == frozenset({TUTOR})


@pytest.mark.parametrize("profile", ["consenting_minor", "adult"])
def test_adding_student_to_an_active_user_with_a_profile_works(
    module_seeded_url, client, staff, profile
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    if profile == "adult":
        adult_profile_for(module_seeded_url, person.id)
    else:
        minor_profile_for(module_seeded_url, person.id, consenting=True)

    assert code_of(put_roles(client, staff, person.id, [STUDENT, TUTOR], 1)) == (
        200,
        None,
    )


def test_adding_student_to_an_active_minor_without_consent_is_refused(
    module_seeded_url, client, staff
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    minor_profile_for(module_seeded_url, person.id)

    assert code_of(put_roles(client, staff, person.id, [STUDENT, TUTOR], 1)) == (
        CONSENT_REQUIRED
    )


def test_adding_student_to_a_pending_user_is_checked_at_activation(module_seeded_url):
    person = create_user(module_seeded_url, roles=(), state="pending")

    assert in_uow(
        module_seeded_url,
        lambda s: identity.add_user_role(s, user_id=person.id, role=STUDENT),
    )
    with pytest.raises(StudentProfileRequiredError):
        in_uow(
            module_seeded_url,
            lambda s: identity.change_user_state(
                s, user_id=person.id, new_state="active"
            ),
        )


def test_the_gate_applies_whatever_the_other_roles(module_seeded_url):
    person = create_user(module_seeded_url, roles=(STUDENT, OWNER), state="suspended")

    with pytest.raises(StudentProfileRequiredError):
        in_uow(
            module_seeded_url,
            lambda s: identity.change_user_state(
                s, user_id=person.id, new_state="active"
            ),
        )


# --- Other activations never touch the families tables ----------------------------

FAMILY_TABLES = ("students", "guardians", "student_guardians", "consent_records")


def statements_of(url: str, call: Any) -> list[str]:
    seen: list[str] = []

    async def _run() -> None:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await call(uow.session)
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return seen


@pytest.mark.parametrize(
    "role", [TUTOR, GUARDIAN, OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF]
)
def test_other_activations_never_touch_the_families_tables(module_seeded_url, role):
    person = create_user(module_seeded_url, roles=(role,), state="suspended")

    seen = statements_of(
        module_seeded_url,
        lambda s: identity.change_user_state(s, user_id=person.id, new_state="active"),
    )

    assert seen
    assert not [
        statement
        for statement in seen
        for table in FAMILY_TABLES
        if f" {table}" in statement or f'"{table}"' in statement
    ]
    assert user_state(module_seeded_url, person.id) == "active"


def test_a_student_activation_reads_the_families_tables(module_seeded_url):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="suspended")
    adult_profile_for(module_seeded_url, person.id)

    seen = statements_of(
        module_seeded_url,
        lambda s: identity.change_user_state(s, user_id=person.id, new_state="active"),
    )

    assert any("FROM students" in statement for statement in seen)


# --- Fail closed ---------------------------------------------------------------------


def test_without_a_registered_gate_a_student_activation_raises(
    module_seeded_url, no_student_login_gate
):
    student = create_user(module_seeded_url, roles=(STUDENT,), state="suspended")
    adult_profile_for(module_seeded_url, student.id)
    tutor = create_user(module_seeded_url, roles=(TUTOR,), state="suspended")

    with pytest.raises(identity.StudentLoginGateMissingError):
        in_uow(
            module_seeded_url,
            lambda s: identity.change_user_state(
                s, user_id=student.id, new_state="active"
            ),
        )
    active = create_user(module_seeded_url, roles=(TUTOR,))
    adult_profile_for(module_seeded_url, active.id)
    with pytest.raises(identity.StudentLoginGateMissingError):
        in_uow(
            module_seeded_url,
            lambda s: identity.add_user_role(s, user_id=active.id, role=STUDENT),
        )
    assert user_state(module_seeded_url, student.id) == "suspended"

    assert in_uow(
        module_seeded_url,
        lambda s: identity.change_user_state(s, user_id=tutor.id, new_state="active"),
    )
    assert user_state(module_seeded_url, tutor.id) == "active"
