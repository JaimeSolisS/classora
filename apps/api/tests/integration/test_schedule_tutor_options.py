"""The calendar's tutor filter options (#57 B, E2) and the batch
qualification read behind its ``tutor_not_qualified`` marker
(``staff.service.qualified_pairs``, #57 A5, E3)."""

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.staff import service as staff
from app.modules.staff.models import TutorQualification
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.test_class_session_create import new_subject

OPTIONS = "/api/v1/schedule/tutor-options"
MX = ZoneInfo("America/Mexico_City")
INVALID = (422, "validation_error")


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


def tag() -> str:
    return uuid.uuid4().hex[:8]


def test_only_tutor_role_users_matching_q_with_their_state(
    module_seeded_url, client, cookie
):
    url = module_seeded_url
    word = tag()
    bea = create_user(url, roles=(TUTOR,), display_name=f"Bea {word}")
    ana = create_user(
        url, roles=(TUTOR,), display_name=f"ana {word}", state="suspended"
    )
    create_user(url, roles=(FINANCE_STAFF,), display_name=f"Caro {word}")
    create_user(url, roles=(TUTOR,), display_name="Dora Otra")

    response = get(client, cookie, OPTIONS, q=f"  {word.upper()} ")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"id": str(ana.id), "display_name": f"ana {word}", "state": "suspended"},
            {"id": str(bea.id), "display_name": f"Bea {word}", "state": "active"},
        ]
    }


def test_no_contact_data(module_seeded_url, client, cookie):
    word = tag()
    create_user(
        module_seeded_url,
        roles=(TUTOR,),
        display_name=f"Eva {word}",
        email=f"eva-{word}@example.com",
        phone="+525512345678",
    )

    [item] = get(client, cookie, OPTIONS, q=word).json()["items"]

    assert set(item) == {"id", "display_name", "state"}


def test_limit_defaults_to_20_and_is_bounded(module_seeded_url, client, cookie):
    word = tag()
    for i in range(22):
        create_user(module_seeded_url, roles=(TUTOR,), display_name=f"T{i:02d} {word}")

    assert len(get(client, cookie, OPTIONS, q=word).json()["items"]) == 20
    assert len(get(client, cookie, OPTIONS, q=word, limit=2).json()["items"]) == 2
    assert len(get(client, cookie, OPTIONS, q=word, limit=50).json()["items"]) == 22
    for limit in (0, 51):
        assert code_of(get(client, cookie, OPTIONS, limit=limit)) == INVALID


def test_q_is_one_to_100_characters(client, cookie):
    assert code_of(get(client, cookie, OPTIONS, q="")) == INVALID
    assert code_of(get(client, cookie, OPTIONS, q="x" * 101)) == INVALID
    assert get(client, cookie, OPTIONS, q="x" * 100).status_code == 200
    assert get(client, cookie, OPTIONS).status_code == 200


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF])
def test_coordinators_and_finance_get_the_options(module_seeded_url, client, role):
    word = tag()
    create_user(module_seeded_url, roles=(TUTOR,), display_name=f"Fer {word}")
    viewer = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    response = get(client, viewer, OPTIONS, q=word)

    assert [i["display_name"] for i in response.json()["items"]] == [f"Fer {word}"]


def test_a_tutor_is_denied(module_seeded_url, client):
    tutor = sign_in(client, create_user(module_seeded_url, roles=(TUTOR,)).username)

    assert code_of(get(client, tutor, OPTIONS)) == (403, "permission_denied")


def test_the_options_take_at_most_three_statements(module_seeded_url):
    word = tag()
    for i in range(3):
        create_user(module_seeded_url, roles=(TUTOR,), display_name=f"G{i} {word}")

    statements, options = count_statements(
        module_seeded_url,
        lambda session: scheduling.list_tutor_options(session, q=word, limit=20),
    )

    assert len(options.items) == 3
    assert statements <= 3


# --- staff.service.qualified_pairs ---------------------------------------------------


def qualification(status: str, tutor_id: uuid.UUID, subject_id: uuid.UUID) -> Any:
    decided = datetime(2026, 1, 5, tzinfo=MX)
    fields: dict[str, Any] = {}
    if status != "pending":
        fields |= {"decided_at": decided, "decided_by_user_id": tutor_id}
    if status == "rejected":
        fields["rejection_reason"] = "No."
    if status == "revoked":
        fields |= {
            "revoked_at": decided,
            "revoked_by_user_id": tutor_id,
            "revocation_reason": "Ya no imparte la materia",
        }
    return TutorQualification(
        tutor_user_id=tutor_id, subject_id=subject_id, status=status, **fields
    )


def test_qualified_pairs_counts_approved_only(module_seeded_url):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,)).id
    statuses = ["pending", "approved", "rejected", "cancelled", "revoked"]
    subjects = {status: uuid.UUID(str(new_subject(url))) for status in statuses}

    async def _add(session: AsyncSession) -> None:
        session.add_all(
            qualification(status, tutor, subject_id)
            for status, subject_id in subjects.items()
        )

    in_uow(url, _add)
    other = create_user(url, roles=(TUTOR,)).id
    pairs = [(tutor, subject) for subject in subjects.values()]
    pairs.append((other, subjects["approved"]))

    statements, found = count_statements(
        url, lambda session: staff.qualified_pairs(session, pairs)
    )

    assert found == frozenset({(tutor, subjects["approved"])})
    assert statements == 1


def test_qualified_pairs_of_nothing_sends_no_statement(module_seeded_url):
    statements, found = count_statements(
        module_seeded_url, lambda session: staff.qualified_pairs(session, [])
    )

    assert (statements, found) == (0, frozenset())
