"""The ``Origin`` check on every unsafe request (#24 B).

On the real app (``build_app`` with its lifespan) and a seeded database: login
(``public()``) and logout (``authenticated()``) refuse a missing, ``null``,
foreign or duplicated ``Origin`` with ``403 csrf_failed``, before any
authentication; the exact app origin passes; safe methods, unknown paths and
wrong methods are not affected.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.errors import PROBLEM_JSON
from tests.authz.csrf import (
    TEST_APP_ORIGIN,
    send_with_headers,
    send_without_origin,
)
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    LOGOUT,
    ME,
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    csrf_for,
    login,
    only_set_cookie,
    signed_headers,
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def user(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url)


def assert_csrf_failed(response: Response) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["code"] == "csrf_failed"
    assert body["detail"] == (
        "The request could not be verified. Reload the page and try again."
    )
    assert body["request_id"]


FOREIGN_ORIGINS = [
    pytest.param([("Origin", "null")], id="null"),
    pytest.param([("Origin", "https://127.0.0.1:5173")], id="foreign-scheme"),
    pytest.param([("Origin", "http://127.0.0.1:5174")], id="foreign-port"),
    pytest.param([("Origin", "http://localhost:5173")], id="localhost"),
    pytest.param([("Origin", "http://127.0.0.1")], id="no-port"),
    pytest.param([("Origin", f"{TEST_APP_ORIGIN}/")], id="trailing-slash"),
    pytest.param([("Origin", "HTTP://127.0.0.1:5173")], id="upper-case"),
    pytest.param([("Origin", "http://evil.example")], id="foreign-host"),
    pytest.param(
        [("Origin", TEST_APP_ORIGIN), ("Origin", TEST_APP_ORIGIN)], id="duplicated"
    ),
    pytest.param(
        [("Origin", TEST_APP_ORIGIN), ("Origin", "http://evil.example")],
        id="duplicated-mixed",
    ),
]


def credentials(user: CreatedUser) -> dict[str, str]:
    return {"username": user.username, "password": TEST_USER_PASSWORD}


# --- Login (public) ------------------------------------------------------------------


def test_login_without_origin_is_403(client, user):
    client.cookies.clear()
    response = send_without_origin(client, "POST", LOGIN, json=credentials(user))

    assert_csrf_failed(response)
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize("headers", FOREIGN_ORIGINS)
def test_login_with_a_foreign_origin_is_403(client, user, headers):
    client.cookies.clear()
    response = send_with_headers(client, "POST", LOGIN, headers, json=credentials(user))

    assert_csrf_failed(response)
    assert response.headers.get_list("set-cookie") == []


def test_login_with_the_app_origin_passes(client, user):
    client.cookies.clear()
    response = send_with_headers(
        client, "POST", LOGIN, [("Origin", TEST_APP_ORIGIN)], json=credentials(user)
    )

    assert response.status_code == 200, response.text


def test_an_invalid_login_body_with_a_foreign_origin_is_403_not_422(client):
    response = send_with_headers(
        client, "POST", LOGIN, [("Origin", "http://evil.example")], json={}
    )

    assert_csrf_failed(response)


# --- Logout (authenticated) ----------------------------------------------------------


def signed_in(client: TestClient, user: CreatedUser):
    response = login(client, user.username)
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return only_set_cookie(response)


def assert_still_signed_in(client: TestClient, cookie) -> None:
    client.cookies.clear()
    assert client.get(ME, headers=cookie_header(cookie)).status_code == 200


def test_logout_without_origin_is_403_and_keeps_the_session(client, user):
    cookie = signed_in(client, user)

    response = send_without_origin(
        client, "POST", LOGOUT, headers=signed_headers(cookie)
    )

    assert_csrf_failed(response)
    assert_still_signed_in(client, cookie)


@pytest.mark.parametrize("headers", FOREIGN_ORIGINS)
def test_logout_with_a_foreign_origin_is_403_and_keeps_the_session(
    client, user, headers
):
    cookie = signed_in(client, user)
    session = [*cookie_header(cookie).items(), ("X-CSRF-Token", csrf_for(cookie))]

    response = send_with_headers(client, "POST", LOGOUT, [*headers, *session])

    assert_csrf_failed(response)
    assert_still_signed_in(client, cookie)


def test_logout_with_the_app_origin_and_token_passes(client, user):
    cookie = signed_in(client, user)

    response = client.post(LOGOUT, headers=signed_headers(cookie))

    assert response.status_code == 204


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param([("Origin", "http://evil.example")], id="foreign"),
        pytest.param([], id="missing"),
    ],
)
def test_a_bad_origin_without_a_cookie_is_403_not_401(client, headers):
    client.cookies.clear()
    if headers:
        response = send_with_headers(client, "POST", LOGOUT, headers)
    else:
        response = send_without_origin(client, "POST", LOGOUT)

    assert_csrf_failed(response)


def test_the_app_origin_without_a_cookie_is_401(client):
    client.cookies.clear()
    response = client.post(LOGOUT)

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


# --- Safe methods, unknown paths, wrong methods ---------------------------------------


def test_get_is_never_checked(client, user):
    cookie = signed_in(client, user)
    foreign = [("Origin", "http://evil.example")]

    me = send_with_headers(
        client, "GET", ME, [*foreign, *cookie_header(cookie).items()]
    )
    live = send_with_headers(client, "GET", "/health/live", foreign)
    no_origin = send_without_origin(client, "GET", "/health/live")
    head = send_with_headers(client, "HEAD", "/health/live", foreign)

    assert me.status_code == 200
    assert live.status_code == 200
    assert no_origin.status_code == 200
    assert head.status_code != 403


def test_options_is_never_checked(client):
    response = send_with_headers(
        client, "OPTIONS", LOGIN, [("Origin", "http://evil.example")]
    )

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


def test_an_unknown_path_is_still_404(client):
    response = send_with_headers(
        client, "POST", "/api/v1/nope", [("Origin", "http://evil.example")]
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_a_wrong_method_is_still_405(client):
    response = send_without_origin(client, "DELETE", ME)

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
