"""OpenAPI export and the Problem Details contract (M1-12)."""

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.core import openapi
from app.core.config import env_var_names
from app.core.conventions import install_conventions
from app.core.errors import (
    PROBLEM_JSON,
    ProblemDetails,
    problem_response,
)
from app.core.logging import request_id_var
from app.main import API_TITLE, API_VERSION, build_app, create_app
from app.modules.scheduling.conflicts import Conflict
from app.modules.scheduling.errors import SchedulingConflictError
from tests.authz.csrf import PROD_APP_ORIGIN
from tests.unit.conventions_app import build_test_app

CONFIG_VARS = ("APP_ENV", "DATABASE_URL", "DATABASE_DIRECT_URL")
PROBLEM_REF = {"$ref": "#/components/schemas/ProblemDetails"}
PROBLEMS = TypeAdapter(ProblemDetails)


@pytest.fixture
def no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*CONFIG_VARS, *env_var_names()):
        monkeypatch.delenv(name, raising=False)
    assert not any(name in os.environ for name in CONFIG_VARS)


def export_text(tmp_path: Path, name: str) -> str:
    target = tmp_path / name
    assert openapi.main([str(target)]) == 0
    return target.read_bytes().decode("utf-8")


# --- A1, A2, A4: export without configuration, deterministic -----------------


def test_export_runs_without_configuration(no_configuration, tmp_path):
    document = json.loads(export_text(tmp_path, "openapi.json"))

    assert document["openapi"].startswith("3.1")
    assert document["info"] == {"title": API_TITLE, "version": API_VERSION}
    assert document["info"]["title"] == "Classora API"
    # A8: never the dev-only demo routes (#41 D2: nor the notification one).
    assert set(document["paths"]) == {
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
        "/api/v1/tutors/{user_id}/availability/change-requests/{change_request_id}/cancel",
        "/api/v1/tutors/{user_id}/availability/check",
        "/api/v1/class-sessions",
        "/api/v1/class-sessions/suggestions",
        "/api/v1/class-sessions/{class_session_id}",
        "/api/v1/class-sessions/{class_session_id}/replacement-tutors",
        "/api/v1/class-sessions/{class_session_id}/offers",
        "/api/v1/class-sessions/{class_session_id}/conflict-overrides",
        "/api/v1/class-sessions/{class_session_id}/substitutions",
        "/api/v1/class-sessions/{class_session_id}/substitutions/{substitution_id}/withdraw",
        "/api/v1/class-sessions/{class_session_id}/attendance",
        "/api/v1/class-sessions/{class_session_id}/attendance/corrections",
        "/api/v1/class-sessions/{class_session_id}/closeout",
        "/api/v1/class-sessions/{class_session_id}/closeout/approve",
        "/api/v1/class-sessions/{class_session_id}/closeout/request-changes",
        "/api/v1/class-sessions/{class_session_id}/closeout/corrections",
        "/api/v1/class-sessions/{class_session_id}/closeout/on-behalf",
        "/api/v1/closeouts/overdue",
        # #79 session notes
        "/api/v1/class-sessions/{class_session_id}/notes",
        "/api/v1/students/{student_id}/notes",
        "/api/v1/students/{student_id}/learning-notes",
        "/api/v1/notes/{note_id}",
        "/api/v1/notes/{note_id}/publish",
        "/api/v1/notes/{note_id}/unpublish",
        # #80 evaluations
        "/api/v1/evaluation-templates",
        "/api/v1/evaluation-templates/{template_id}",
        "/api/v1/evaluations",
        "/api/v1/evaluations/{evaluation_id}",
        "/api/v1/evaluations/{evaluation_id}/finalize",
        "/api/v1/evaluations/{evaluation_id}/void",
        "/api/v1/students/{student_id}/evaluations",
        "/api/v1/students/{student_id}/academic-history",
        "/api/v1/class-sessions/{class_session_id}/evaluation-context",
        # #81 learning plans
        "/api/v1/students/{student_id}/learning-plans",
        "/api/v1/learning-plans/{plan_id}",
        "/api/v1/learning-plans/{plan_id}/transition",
        "/api/v1/learning-plan-milestones/{milestone_id}",
        # #82 homework assignments
        "/api/v1/assignments",
        "/api/v1/assignments/{assignment_id}",
        "/api/v1/assignments/{assignment_id}/students/{student_id}/submission",
        "/api/v1/assignments/{assignment_id}/students/{student_id}/grade",
        "/api/v1/students/{student_id}/assignments",
        "/api/v1/students/{student_id}/assignments/{assignment_id}",
        # #83 progress reports
        "/api/v1/progress-reports",
        "/api/v1/progress-reports/{report_id}",
        "/api/v1/progress-reports/{report_id}/submit",
        "/api/v1/progress-reports/{report_id}/approve",
        "/api/v1/progress-reports/{report_id}/request-changes",
        "/api/v1/progress-reports/{report_id}/retract",
        "/api/v1/students/{student_id}/progress-reports",
        "/api/v1/students/{student_id}/progress-reports/{report_id}",
        # #316 report schedules
        "/api/v1/students/{student_id}/progress-report-schedules",
        "/api/v1/progress-report-schedules/{schedule_id}",
        # #84 progress dashboard
        "/api/v1/students/{student_id}/progress",
        "/api/v1/students/{student_id}/session-log",
        "/api/v1/class-sessions/{class_session_id}/cancel",
        "/api/v1/assignment-offers",
        "/api/v1/assignment-offers/{offer_id}/accept",
        "/api/v1/assignment-offers/{offer_id}/decline",
        "/api/v1/schedule/tutor-options",
        # #67: the role-scoped schedules.
        "/api/v1/tutors/{user_id}/schedule",
        "/api/v1/students/{student_id}/schedule",
        "/api/v1/schedule/family-students",
        "/api/v1/tutor-suggestions",
        "/api/v1/class-series",
        "/api/v1/class-series/{class_series_id}",
        "/api/v1/class-series/{class_series_id}/cancellations",
        "/api/v1/class-series/{class_series_id}/edits",
        "/api/v1/class-series/{class_series_id}/offers",
        "/api/v1/groups",
        "/api/v1/groups/{group_id}",
        "/api/v1/groups/{group_id}/sessions",
        "/api/v1/groups/{group_id}/enrollments",
        "/api/v1/groups/{group_id}/enrollments/{enrollment_id}/withdraw",
        "/api/v1/cohorts",
        "/api/v1/cohorts/{cohort_id}",
        "/api/v1/cohorts/{cohort_id}/sessions",
        "/api/v1/cohorts/{cohort_id}/enrollments",
        "/api/v1/cohorts/{cohort_id}/enrollments/{enrollment_id}/withdraw",
        "/api/v1/available-slots",
        "/api/v1/session-requests",
        "/api/v1/session-requests/{request_id}",
        "/api/v1/session-requests/{request_id}/withdraw",
        "/api/v1/session-requests/{request_id}/review",
        "/api/v1/session-requests/{request_id}/confirm",
        "/api/v1/session-requests/{request_id}/reject",
        "/api/v1/change-requests",
        "/api/v1/change-requests/{change_request_id}",
        "/api/v1/change-requests/{change_request_id}/withdraw",
        "/api/v1/change-requests/{change_request_id}/review",
        "/api/v1/change-requests/{change_request_id}/approve",
        "/api/v1/change-requests/{change_request_id}/reject",
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


def test_two_exports_are_byte_identical(no_configuration, tmp_path):
    first = export_text(tmp_path, "first.json")
    second = export_text(tmp_path, "second.json")

    assert first == second
    assert first.endswith("}\n")
    assert first.startswith('{\n  "openapi": ')
    for environment_dependent in (str(tmp_path), str(Path.cwd()), os.uname().nodename):
        assert environment_dependent not in first


def test_export_without_an_output_path_prints_usage(capsys):
    assert openapi.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_exported_document_matches_the_running_app(app_env, monkeypatch, tmp_path):
    # Same builder: the served /openapi.json equals the export (APP_ENV=prod,
    # where the dev-only demo route is not mounted).
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_ORIGIN", PROD_APP_ORIGIN)
    with TestClient(create_app()) as client:
        served = client.get("/openapi.json").json()

    assert served == json.loads(export_text(tmp_path, "openapi.json"))


# --- A3: create_app still validates configuration ----------------------------


def test_create_app_still_refuses_missing_configuration(no_configuration, capsys):
    with pytest.raises(SystemExit) as exc_info:
        create_app()

    assert exc_info.value.code == 1
    assert "Configuration error" in capsys.readouterr().err


# --- A5, A6: Problem Details in the contract -----------------------------------


def test_problem_schemas_replace_fastapi_validation_schemas():
    schemas = openapi.build_document()["components"]["schemas"]

    assert {"ProblemDetails", "FieldError", "SchedulingConflict"} <= set(schemas)
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    problem = schemas["ProblemDetails"]
    assert set(problem["required"]) == {"type", "title", "status", "code", "request_id"}
    assert set(problem["properties"]) == {
        "type",
        "title",
        "status",
        "code",
        "request_id",
        "detail",
        "errors",
        "conflicts",
        "details",
    }
    assert problem["properties"]["errors"]["items"] == {
        "$ref": "#/components/schemas/FieldError"
    }
    assert set(schemas["FieldError"]["required"]) == {"path", "code", "message"}
    # #48 B1: the ADR-0005 conflict list, one generic member.
    assert problem["properties"]["conflicts"]["items"] == {
        "$ref": "#/components/schemas/SchedulingConflict"
    }
    conflict = schemas["SchedulingConflict"]
    assert set(conflict["required"]) == {"code", "severity", "details"}
    assert conflict["properties"]["severity"]["enum"] == ["hard", "soft"]


def test_ready_documents_its_503_as_problem_details():
    ready = openapi.build_document()["paths"]["/health/ready"]["get"]

    assert ready["responses"]["503"]["content"] == {
        PROBLEM_JSON: {"schema": PROBLEM_REF}
    }


def test_every_422_is_documented_as_problem_details():
    # The test conventions app has routes with bodies and query parameters.
    document = build_test_app().openapi()

    responses = [
        operation["responses"]
        for operations in document["paths"].values()
        for operation in operations.values()
    ]
    with_422 = [r["422"] for r in responses if "422" in r]
    assert with_422
    for response in with_422:
        assert response["content"] == {PROBLEM_JSON: {"schema": PROBLEM_REF}}
    assert "HTTPValidationError" not in json.dumps(document)
    assert "ProblemDetails" in document["components"]["schemas"]


def test_dev_routes_document_422_as_problem_details():
    document = build_app(include_dev_routes=True).openapi()

    operation = document["paths"]["/api/v1/dev/demo-jobs"]["post"]
    assert operation["responses"]["422"]["content"] == {
        PROBLEM_JSON: {"schema": PROBLEM_REF}
    }
    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_document_is_built_once_and_cached():
    app = build_app()

    assert app.openapi() is app.openapi()


def _rendered_body(response) -> dict:
    return json.loads(response.body)


def test_problem_model_validates_the_renderer_output():
    # Inside a request the middleware always sets the request ID.
    token = request_id_var.set("req-1")
    try:
        bodies = _render_sample_problems()
    finally:
        request_id_var.reset(token)
    plain, with_detail, with_errors = bodies

    for body in bodies:
        assert PROBLEMS.validate_python(body, strict=True) == body
    assert "detail" not in plain and "errors" not in plain
    assert with_detail["detail"] == "Database is not reachable."
    assert with_errors["errors"][0]["path"] == ["body", "items", 0]


def _render_sample_problems() -> tuple[dict, dict, dict]:
    plain = _rendered_body(problem_response(404, "not_found"))
    with_detail = _rendered_body(
        problem_response(503, "database_unavailable", "Database is not reachable.")
    )
    with_errors = _rendered_body(
        problem_response(
            422,
            "validation_error",
            "The request is not valid.",
            errors=[{"path": ["body", "items", 0], "code": "missing", "message": "x"}],
        )
    )
    return plain, with_detail, with_errors


def test_problem_model_validates_a_real_422(client: TestClient):
    # The conventions app's /signup route: a missing field and a wrong type.
    response = client.post("/signup", json={"age": "old"})

    assert response.status_code == 422
    body = response.json()
    assert body["errors"]
    assert PROBLEMS.validate_python(body, strict=True) == body


def test_problem_model_validates_a_real_409_scheduling_conflict():
    # #48 B1: the app's own error handler renders the conflict list.
    app = FastAPI()
    install_conventions(app)

    @app.get("/conflict")
    async def conflict() -> None:
        raise SchedulingConflictError(
            [
                Conflict("student_overlap", {"class_session_ids": ["x"]}).as_wire(),
                Conflict("notice_window", {"booking_notice_hours": 24}).as_wire(),
            ]
        )

    response = TestClient(app).get("/conflict")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "scheduling_conflict"
    assert body["detail"] == "The session conflicts with existing rules."
    assert [c["severity"] for c in body["conflicts"]] == ["hard", "soft"]
    assert "errors" not in body
    assert PROBLEMS.validate_python(body, strict=True) == body


def test_problem_model_rejects_unknown_and_missing_fields():
    valid = {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "code": "not_found",
        "request_id": "abc",
    }
    with pytest.raises(ValidationError):
        PROBLEMS.validate_python({**valid, "trace": "x"})
    with pytest.raises(ValidationError):
        PROBLEMS.validate_python({k: v for k, v in valid.items() if k != "code"})


# --- A7: typed health responses ----------------------------------------------


def test_health_responses_have_a_typed_status_model():
    document = openapi.build_document()
    schema_ref = {"$ref": "#/components/schemas/HealthStatus"}

    for path in ("/health/live", "/health/ready"):
        ok = document["paths"][path]["get"]["responses"]["200"]
        assert ok["content"]["application/json"]["schema"] == schema_ref
    health = document["components"]["schemas"]["HealthStatus"]
    assert health["required"] == ["status"]
    assert health["properties"]["status"]["const"] == "ok"


def test_build_app_needs_no_settings(no_configuration):
    app: FastAPI = build_app()

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.text == '{"status":"ok"}'


def test_the_calendar_range_read_documents_its_query():
    """#57 A1: the query model's fields are the documented parameters."""
    operation = build_app().openapi()["paths"]["/api/v1/class-sessions"]["get"]
    parameters = {p["name"]: p for p in operation["parameters"]}

    assert set(parameters) == {
        "from",
        "to",
        "tutor_user_id",
        "student_id",
        "classroom_id",
        "kind",
        "include_cancelled",
    }
    assert all(p["in"] == "query" for p in parameters.values())
    assert {name for name, p in parameters.items() if p["required"]} == {"from", "to"}
