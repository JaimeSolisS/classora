"""Problem Details for every error kind (A1-A6) and production wiring (D1)."""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.conventions import install_conventions
from app.core.errors import AppError, VersionConflictError
from app.main import create_app
from tests.authz.csrf import PROD_APP_ORIGIN
from tests.unit.conventions_app import LEAK_MARKER

PROBLEM_JSON = "application/problem+json"


def assert_problem(response, status: int, code: str) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["status"] == status
    assert body["code"] == code
    assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", body["code"])
    assert body["request_id"] == response.headers["x-request-id"]
    return body


# --- A3 validation ------------------------------------------------------------


def test_body_validation_error_has_field_errors_without_input(client: TestClient):
    response = client.post("/signup", json={"password": "s3cret-value", "age": "x"})

    body = assert_problem(response, 422, "validation_error")
    assert body["title"] == "Unprocessable Content"
    assert body["errors"] == [
        {
            "path": ["body", "age"],
            "code": "int_parsing",
            "message": (
                "Input should be a valid integer, unable to parse string as an integer"
            ),
        }
    ]
    assert "s3cret-value" not in response.text
    for item in body["errors"]:
        assert set(item) == {"path", "code", "message"}


def test_nested_body_path_includes_list_index(client: TestClient):
    response = client.post("/orders", json={"items": [{"amount": -1}]})

    body = assert_problem(response, 422, "validation_error")
    assert body["errors"][0]["path"] == ["body", "items", 0, "amount"]
    assert body["errors"][0]["code"] == "greater_than_equal"


def test_missing_body_field(client: TestClient):
    response = client.post("/signup", json={"age": 3})

    body = assert_problem(response, 422, "validation_error")
    assert body["errors"][0]["path"] == ["body", "password"]
    assert body["errors"][0]["code"] == "missing"


def test_malformed_json_body(client: TestClient):
    response = client.post(
        "/signup",
        content=b'{"password": "s3cret-value", ',
        headers={"content-type": "application/json"},
    )

    body = assert_problem(response, 422, "validation_error")
    assert body["errors"][0]["code"] == "json_invalid"
    assert body["errors"][0]["path"][0] == "body"
    assert "s3cret-value" not in response.text


def test_query_path_and_header_validation(client: TestClient):
    query = assert_problem(client.get("/query?limit=0"), 422, "validation_error")
    assert query["errors"][0]["path"] == ["query", "limit"]
    assert query["errors"][0]["code"] == "greater_than_equal"

    missing = assert_problem(client.get("/query"), 422, "validation_error")
    assert missing["errors"][0]["path"] == ["query", "limit"]
    assert missing["errors"][0]["code"] == "missing"

    path = assert_problem(client.get("/path/abc"), 422, "validation_error")
    assert path["errors"][0]["path"] == ["path", "number"]
    assert path["errors"][0]["code"] == "int_parsing"

    header = assert_problem(
        client.get("/header", headers={"x-count": "many"}), 422, "validation_error"
    )
    assert header["errors"][0]["path"] == ["header", "x-count"]


# --- A4 framework HTTP errors -------------------------------------------------


def test_unknown_path_is_404_problem(client: TestClient):
    response = client.get("/nope", headers={"X-Request-ID": "qa-404"})

    body = assert_problem(response, 404, "not_found")
    assert body["title"] == "Not Found"
    assert body["request_id"] == "qa-404"


def test_wrong_method_is_405_problem_with_allow_header(client: TestClient):
    response = client.post("/health/live")

    body = assert_problem(response, 405, "method_not_allowed")
    assert body["title"] == "Method Not Allowed"
    assert response.headers["allow"] == "GET"


def test_http_exception_keeps_headers_and_hides_detail(client: TestClient):
    response = client.get("/http-exception")

    body = assert_problem(response, 418, "i_m_a_teapot")
    assert response.headers["x-extra"] == "kept"
    assert "detail" not in body
    assert "internal detail" not in response.text


# --- A2 / A6 AppError ---------------------------------------------------------


def test_app_error_subclass_renders_its_status_code_and_detail(client: TestClient):
    body = assert_problem(client.get("/app-error"), 423, "thing_locked")

    assert body["title"] == "Locked"
    assert body["detail"] == "The thing is locked."
    assert "errors" not in body


def test_app_error_with_field_errors(client: TestClient):
    body = assert_problem(client.get("/app-error-fields"), 500, "internal_error")

    assert body["detail"] == "Custom detail."
    assert body["errors"] == [
        {"path": ["body", "name"], "code": "taken", "message": "Taken."}
    ]


def test_version_conflict_is_409(client: TestClient):
    body = assert_problem(client.get("/conflict"), 409, "version_conflict")

    assert body["title"] == "Conflict"
    assert body["detail"] == VersionConflictError.detail
    assert issubclass(VersionConflictError, AppError)


# --- A5 / E2 unhandled exceptions --------------------------------------------


def test_unhandled_exception_is_generic_500_without_leak(client: TestClient):
    response = client.get("/boom")

    body = assert_problem(response, 500, "internal_error")
    assert body["title"] == "Internal Server Error"
    assert body["detail"] == "An unexpected error occurred."
    exposed = response.text + " ".join(f"{k}: {v}" for k, v in response.headers.items())
    for forbidden in (LEAK_MARKER, "RuntimeError", "Traceback"):
        assert forbidden not in exposed


# --- D1 production wiring -----------------------------------------------------


def test_production_app_has_no_demo_route(
    app_env: None, monkeypatch: pytest.MonkeyPatch
):
    # APP_ENV=prod: the local/DEV demo job and notification routes are not
    # mounted (M1-16 D4, #41 D2).
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_ORIGIN", PROD_APP_ORIGIN)
    app = create_app()

    assert app.debug is False
    assert set(app.openapi()["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/academic-periods",
        "/api/v1/academic-periods/{period_id}",
        "/api/v1/approval-requests",
        "/api/v1/approval-requests/{request_id}",
        "/api/v1/approval-requests/{request_id}/approve",
        "/api/v1/approval-requests/{request_id}/reject",
        "/api/v1/approval-requests/{request_id}/request-changes",
        "/api/v1/audit-events",
        "/api/v1/notifications",
        "/api/v1/notifications/stream",
        "/api/v1/notifications/unread-count",
        "/api/v1/notifications/read-all",
        "/api/v1/notifications/{notification_id}/read",
        "/api/v1/business-settings",
        "/api/v1/scheduling-policies",
        "/api/v1/scheduling-policies/{policy_id}",
        "/api/v1/classrooms",
        "/api/v1/classrooms/{classroom_id}",
        "/api/v1/closures",
        "/api/v1/closures/check",
        "/api/v1/closures/{closure_id}",
        "/api/v1/course-templates",
        "/api/v1/course-templates/{template_id}",
        "/api/v1/tutors",
        "/api/v1/tutors/{user_id}",
        "/api/v1/tutors/{user_id}/qualifications",
        "/api/v1/tutors/{user_id}/qualifications/{qualification_id}/cancel",
        "/api/v1/tutors/{user_id}/qualifications/{qualification_id}/revoke",
        "/api/v1/tutors/{user_id}/availability",
        "/api/v1/tutors/{user_id}/availability/weekly",
        "/api/v1/tutors/{user_id}/availability/settings",
        "/api/v1/tutors/{user_id}/availability/exceptions",
        "/api/v1/tutors/{user_id}/availability/exceptions/{exception_id}",
        "/api/v1/tutors/{user_id}/availability/check",
        "/api/v1/class-sessions",
        "/api/v1/class-sessions/suggestions",
        "/api/v1/class-sessions/{class_session_id}",
        "/api/v1/class-sessions/{class_session_id}/replacement-tutors",
        "/api/v1/class-sessions/{class_session_id}/offers",
        "/api/v1/class-sessions/{class_session_id}/conflict-overrides",
        "/api/v1/class-sessions/{class_session_id}/substitutions",
        "/api/v1/class-sessions/{class_session_id}/substitutions/{substitution_id}/withdraw",
        "/api/v1/class-sessions/{class_session_id}/cancel",
        "/api/v1/assignment-offers",
        "/api/v1/assignment-offers/{offer_id}/accept",
        "/api/v1/assignment-offers/{offer_id}/decline",
        "/api/v1/schedule/tutor-options",
        "/api/v1/tutor-suggestions",
        "/api/v1/class-series",
        "/api/v1/class-series/{class_series_id}",
        "/api/v1/class-series/{class_series_id}/cancellations",
        "/api/v1/class-series/{class_series_id}/edits",
        "/api/v1/class-series/{class_series_id}/offers",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/me/preferences",
        "/api/v1/auth/reauthenticate",
        "/api/v1/auth/invitation/lookup",
        "/api/v1/auth/invitation/redeem",
        "/api/v1/invitations",
        "/api/v1/invitations/{user_id}/reissue",
        "/api/v1/privacy-notice",
        "/api/v1/privacy-notice/acceptance",
        "/api/v1/auth/password",
        "/api/v1/auth/password-reset/lookup",
        "/api/v1/auth/password-reset/redeem",
        "/api/v1/password-resets",
        "/api/v1/auth/registrations",
        "/api/v1/users",
        "/api/v1/users/{user_id}",
        "/api/v1/users/{user_id}/suspend",
        "/api/v1/users/{user_id}/archive",
        "/api/v1/users/{user_id}/reactivate",
        "/api/v1/users/{user_id}/roles",
        "/api/v1/users/{user_id}/delegations",
        "/api/v1/education-levels",
        "/api/v1/education-levels/{level_id}",
        "/api/v1/education-levels/{level_id}/subjects",
        "/api/v1/subjects",
        "/api/v1/subjects/{subject_id}",
        "/api/v1/students",
        "/api/v1/students/{student_id}",
        "/api/v1/students/{student_id}/guardians",
        "/api/v1/students/{student_id}/guardians/{guardian_id}",
        "/api/v1/students/{student_id}/account",
        "/api/v1/students/{student_id}/consents",
        "/api/v1/students/{student_id}/consents/{consent_id}/revoke",
        "/api/v1/guardians",
        "/api/v1/guardians/{guardian_id}",
        "/api/v1/families",
        "/api/v1/families/{family_id}",
        "/api/v1/families/{family_id}/students/{student_id}",
        "/api/v1/families/{family_id}/guardians/{guardian_id}",
    }
    top_level = {getattr(r, "path", None) for r in app.routes} - {None}
    assert top_level == {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    with TestClient(app) as client:
        for path in ("/health/live", "/openapi.json"):
            assert client.get(path).status_code == 200


def test_production_app_renders_404_problem(app_env: None):
    with TestClient(create_app()) as client:
        response = client.get("/nope")

    assert_problem(response, 404, "not_found")


# --- AppError headers (#27 B6) ------------------------------------------------------


class SlowDownError(AppError):
    status = 429
    code = "slow_down"
    detail = "Slow down."

    def __init__(self) -> None:
        super().__init__()
        self.headers = {"Retry-After": "7", "X-Extra": "1"}


def test_app_error_headers_are_sent_and_default_to_none():
    app = FastAPI()
    install_conventions(app)

    @app.get("/slow")
    async def slow() -> None:
        raise SlowDownError()

    @app.get("/plain")
    async def plain() -> None:
        raise VersionConflictError()

    with TestClient(app) as client:
        slow_response = client.get("/slow")
        plain_response = client.get("/plain")

    assert_problem(slow_response, 429, "slow_down")
    assert slow_response.headers["retry-after"] == "7"
    assert slow_response.headers["x-extra"] == "1"
    assert AppError.headers is None
    assert "retry-after" not in plain_response.headers
