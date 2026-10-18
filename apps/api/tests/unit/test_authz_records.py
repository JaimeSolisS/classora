"""Record-level policies: the 404/403 rule and multi-role union (M2-03 C)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.authz import (
    PermissionDeniedError,
    Principal,
    RecordNotFoundError,
    authorize_record,
    get_current_principal,
)
from app.modules.identity.permissions import GUARDIAN, STUDENT_VIEW, TUTOR
from tests.authz.assertions import assert_same_not_found
from tests.unit.authz_app import (
    GUARDIAN_ID,
    NOTE,
    OTHER_TUTOR_ID,
    TUTOR_ID,
    build_authz_app,
)

NOTE_URL = f"/notes/{NOTE.id}"


def as_user(user_id: uuid.UUID, *roles: str) -> Principal:
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset({STUDENT_VIEW})
    )


@pytest.fixture
def app() -> FastAPI:
    return build_authz_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def sign_in(app: FastAPI, who: Principal) -> None:
    app.dependency_overrides[get_current_principal] = lambda: who


# --- C1: authorize_record ----------------------------------------------------------


def test_missing_record_is_not_found():
    with pytest.raises(RecordNotFoundError):
        authorize_record(None, can_view=lambda r: True)


def test_hidden_record_is_not_found_even_if_the_action_is_allowed():
    with pytest.raises(RecordNotFoundError):
        authorize_record("r", can_view=lambda r: False, can_act=lambda r: True)


def test_visible_record_without_the_action_is_denied():
    with pytest.raises(PermissionDeniedError):
        authorize_record("r", can_view=lambda r: True, can_act=lambda r: False)


def test_allowed_record_is_returned():
    assert authorize_record("r", can_view=lambda r: True) == "r"
    assert authorize_record("r", can_view=lambda r: True, can_act=lambda r: True) == "r"


def test_record_not_found_has_no_detail_and_no_arguments():
    assert (RecordNotFoundError.status, RecordNotFoundError.code) == (404, "not_found")
    assert RecordNotFoundError().detail is None
    with pytest.raises(TypeError):
        RecordNotFoundError("student 42")  # type: ignore[call-arg]


# --- C4: over HTTP -----------------------------------------------------------------


def test_visible_record(app, client):
    sign_in(app, as_user(TUTOR_ID, TUTOR))

    response = client.get(NOTE_URL)

    assert response.status_code == 200
    assert response.json() == {"id": str(NOTE.id)}


def test_hidden_record_looks_missing(app, client):
    sign_in(app, as_user(OTHER_TUTOR_ID, TUTOR))

    hidden = client.get(NOTE_URL)
    missing = client.get(f"/notes/{uuid.uuid7()}")

    assert_same_not_found(hidden, missing)


def test_hidden_record_looks_like_an_unknown_path(app, client):
    sign_in(app, as_user(OTHER_TUTOR_ID, TUTOR))

    assert_same_not_found(client.get(NOTE_URL), client.get("/no/such/path"))


def test_hidden_record_action_looks_missing_too(app, client):
    sign_in(app, as_user(OTHER_TUTOR_ID, TUTOR))

    assert_same_not_found(
        client.post(f"{NOTE_URL}/archive"),
        client.post(f"/notes/{uuid.uuid7()}/archive"),
    )


def test_visible_record_with_a_forbidden_action_is_403(app, client):
    sign_in(app, as_user(GUARDIAN_ID, GUARDIAN))

    assert client.get(NOTE_URL).status_code == 200
    response = client.post(f"{NOTE_URL}/archive")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_allowed_action(app, client):
    sign_in(app, as_user(TUTOR_ID, TUTOR))

    assert client.post(f"{NOTE_URL}/archive").status_code == 200


def test_a_second_role_makes_the_record_visible(app, client):
    # GUARDIAN_ID is not the note's tutor: as a tutor alone it is hidden.
    sign_in(app, as_user(GUARDIAN_ID, TUTOR))
    assert client.get(NOTE_URL).status_code == 404

    sign_in(app, as_user(GUARDIAN_ID, TUTOR, GUARDIAN))

    assert client.get(NOTE_URL).status_code == 200


# --- C3: the assertion itself --------------------------------------------------------


def test_assert_same_not_found_rejects_a_403(app, client):
    sign_in(app, as_user(GUARDIAN_ID, GUARDIAN))
    forbidden = client.post(f"{NOTE_URL}/archive")
    missing = client.post(f"/notes/{uuid.uuid7()}/archive")

    with pytest.raises(AssertionError):
        assert_same_not_found(forbidden, missing)


def test_assert_same_not_found_rejects_a_different_body(app, client):
    from httpx import Response

    sign_in(app, as_user(OTHER_TUTOR_ID, TUTOR))
    missing = client.get(f"/notes/{uuid.uuid7()}")
    body = {**missing.json(), "detail": "Student 42 is not yours."}
    leaky = Response(
        404, json=body, headers={"content-type": missing.headers["content-type"]}
    )

    with pytest.raises(AssertionError):
        assert_same_not_found(leaky, missing)
