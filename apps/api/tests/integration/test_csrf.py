"""The CSRF token with real logins (#24 C).

On the real app and a seeded database: the token in the login response equals
the one ``/auth/me`` returns, it is checked on every unsafe
``authenticated()``/``require()`` request after the session and before the
permission and the body, a failure keeps the session, and the log line names
only the reason.
"""

import logging
import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel, ConfigDict

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.csrf import CSRF_TOKEN_LENGTH, csrf_token_for
from app.core.errors import PROBLEM_JSON
from app.modules.identity.permissions import OWNER, PAYROLL_FINALIZE, TUTOR
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER, send_with_headers
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    LOGOUT,
    ME,
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    csrf_for,
    login,
    only_set_cookie,
    token_of,
)
from tests.integration.conftest import run_sql

# --- A test-only require() route with a PUT and a required body --------------------


class PeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str
    amount: int


csrf_router = APIRouter(prefix="/test-only/csrf")


@csrf_router.put("/payroll/{period_id}", responses=PERMISSION_RESPONSES)
async def put_period(
    principal: Annotated[Principal, require(PAYROLL_FINALIZE)],
    period_id: uuid.UUID,
    body: PeriodIn,
) -> dict[str, str]:
    return {"period_id": str(period_id)}


PERIOD = f"/test-only/csrf/payroll/{uuid.uuid7()}"
VALID_BODY = {"note": "closing", "amount": 3}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url, routers=[csrf_router]) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,))


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def signed_in(client: TestClient, user: CreatedUser) -> tuple[SetCookie, str]:
    """A fresh session: its cookie and the csrf_token of the login body."""
    client.cookies.clear()
    response = login(client, user.username)
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return only_set_cookie(response), response.json()["csrf_token"]


def me(client: TestClient, cookie: SetCookie) -> Response:
    client.cookies.clear()
    return client.get(ME, headers=cookie_header(cookie))


def assert_csrf_failed(response: Response) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "csrf_failed"


def audit_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    return int(row.n)


# --- C1, C2: derivation and delivery -------------------------------------------------


def test_login_and_me_return_the_derived_token(client, tutor):
    response = login(client, tutor.username)
    cookie = only_set_cookie(response)
    token = response.json()["csrf_token"]
    first = me(client, cookie)
    second = me(client, cookie)

    assert len(token) == CSRF_TOKEN_LENGTH
    assert token == csrf_token_for(token_of(cookie))
    assert first.json()["csrf_token"] == token
    assert second.json()["csrf_token"] == token
    # Never in a cookie.
    for sent in (response, first, second):
        assert token not in " ".join(sent.headers.get_list("set-cookie"))


def test_every_login_gets_a_new_token(client, tutor):
    _, first = signed_in(client, tutor)
    _, second = signed_in(client, tutor)

    assert first != second


def test_two_sessions_of_one_user_have_different_tokens(client, tutor):
    phone, phone_token = signed_in(client, tutor)
    laptop, laptop_token = signed_in(client, tutor)

    assert phone_token != laptop_token
    assert me(client, phone).json()["csrf_token"] == phone_token
    assert me(client, laptop).json()["csrf_token"] == laptop_token


# --- C3, C7: verification on logout -------------------------------------------------


def logout_with(client: TestClient, cookie: SetCookie, token_headers) -> Response:
    client.cookies.clear()
    session = [*ORIGIN_HEADER.items(), *cookie_header(cookie).items()]
    return send_with_headers(client, "POST", LOGOUT, [*session, *token_headers])


def changed(token: str) -> str:
    return ("B" if token[0] == "A" else "A") + token[1:]


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty",
        "other-session",
        "one-char",
        "last-char",
        "duplicated",
        "long",
    ],
)
def test_a_wrong_token_is_403_and_keeps_the_session(client, tutor, owner, case):
    cookie, token = signed_in(client, tutor)
    _, other = signed_in(client, owner)
    headers = {
        "missing": [],
        "empty": [(CSRF_HEADER, "")],
        "other-session": [(CSRF_HEADER, other)],
        "one-char": [(CSRF_HEADER, changed(token))],
        "last-char": [(CSRF_HEADER, token[:-1] + ("A" if token[-1] != "A" else "B"))],
        "duplicated": [(CSRF_HEADER, token), (CSRF_HEADER, token)],
        "long": [(CSRF_HEADER, token + "x" * (200 - len(token)))],
    }[case]

    response = logout_with(client, cookie, headers)

    assert_csrf_failed(response)
    assert response.headers.get_list("set-cookie") == []
    assert me(client, cookie).status_code == 200


def test_the_correct_token_logs_out(client, tutor):
    cookie, token = signed_in(client, tutor)

    response = logout_with(client, cookie, [(CSRF_HEADER, token)])

    assert response.status_code == 204
    assert me(client, cookie).status_code == 401


def test_the_header_name_is_case_insensitive(client, tutor):
    cookie, token = signed_in(client, tutor)

    response = logout_with(client, cookie, [("x-csrf-token", token)])

    assert response.status_code == 204


def test_after_a_second_login_the_old_token_fails(client, tutor):
    old_cookie, old_token = signed_in(client, tutor)
    client.cookies.clear()
    rotated = client.post(
        LOGIN,
        json={"username": tutor.username, "password": TEST_USER_PASSWORD},
        headers=cookie_header(old_cookie),
    )
    assert rotated.status_code == 200
    new_cookie = only_set_cookie(rotated)

    response = logout_with(client, new_cookie, [(CSRF_HEADER, old_token)])

    assert_csrf_failed(response)
    assert rotated.json()["csrf_token"] != old_token
    assert me(client, new_cookie).status_code == 200


def test_without_a_cookie_logout_is_401_not_403(client):
    client.cookies.clear()

    response = client.post(LOGOUT, headers={CSRF_HEADER: "x" * 43})

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_a_revoked_session_with_its_token_is_401(client, tutor):
    cookie, token = signed_in(client, tutor)
    assert logout_with(client, cookie, [(CSRF_HEADER, token)]).status_code == 204

    response = logout_with(client, cookie, [(CSRF_HEADER, token)])

    assert response.status_code == 401


# --- C5: public routes never need the token -------------------------------------------


def test_login_with_a_stale_cookie_and_no_token_rotates(client, tutor):
    old_cookie, _ = signed_in(client, tutor)
    client.cookies.clear()

    response = client.post(
        LOGIN,
        json={"username": tutor.username, "password": TEST_USER_PASSWORD},
        headers=cookie_header(old_cookie),
    )

    assert response.status_code == 200
    assert me(client, old_cookie).status_code == 401


# --- C3 order: 401, then csrf_failed, then permission_denied, then 422 ---------------


def put(client: TestClient, cookie: SetCookie | None, token: str | None, body):
    client.cookies.clear()
    headers = {}
    if cookie is not None:
        headers.update(cookie_header(cookie))
    if token is not None:
        headers[CSRF_HEADER] = token
    return client.put(PERIOD, json=body, headers=headers)


@pytest.mark.parametrize(
    "body",
    [pytest.param(VALID_BODY, id="valid"), pytest.param({"note": 1}, id="bad-body")],
)
def test_csrf_is_checked_before_the_permission_and_the_body(client, tutor, body):
    cookie, token = signed_in(client, tutor)

    assert put(client, None, None, body).status_code == 401
    assert_csrf_failed(put(client, cookie, None, body))
    assert_csrf_failed(put(client, cookie, changed(token), body))
    denied = put(client, cookie, token, body)
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


def test_a_permitted_caller_with_the_token_reaches_the_body(client, owner):
    cookie, token = signed_in(client, owner)

    assert_csrf_failed(put(client, cookie, None, {"note": 1}))
    invalid = put(client, cookie, token, {"note": 1})
    valid = put(client, cookie, token, VALID_BODY)

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert valid.status_code == 200


# --- C6: one log line, no values, no audit ------------------------------------------


def test_failures_log_the_reason_only_and_are_not_audited(
    client, tutor, module_seeded_url, caplog
):
    cookie, token = signed_in(client, tutor)
    before = audit_count(module_seeded_url)
    caplog.clear()
    foreign = "http://evil-origin.example"

    with caplog.at_level(logging.WARNING, logger="app.security"):
        logout_with(client, cookie, [])
        logout_with(client, cookie, [(CSRF_HEADER, changed(token))])
        client.cookies.clear()
        send_with_headers(
            client,
            "POST",
            LOGOUT,
            [("Origin", foreign), *cookie_header(cookie).items()],
        )
        request = client.build_request("POST", LOGOUT, headers=cookie_header(cookie))
        del request.headers["origin"]
        client.send(request)

    records = [r for r in caplog.records if r.name == "app.security"]
    assert [(r.levelname, r.getMessage(), r.reason) for r in records] == [
        ("WARNING", "CSRF check failed", "token_missing"),
        ("WARNING", "CSRF check failed", "token_invalid"),
        ("WARNING", "CSRF check failed", "origin_mismatch"),
        ("WARNING", "CSRF check failed", "origin_missing"),
    ]
    for record in records:
        assert (record.method, record.path) == ("POST", LOGOUT)
        rendered = str(record.__dict__)
        for secret in (token, changed(token), cookie.value, csrf_for(cookie), foreign):
            assert secret not in rendered
    assert audit_count(module_seeded_url) == before
    assert me(client, cookie).status_code == 200
