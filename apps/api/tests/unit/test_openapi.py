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
from app.core.errors import PROBLEM_JSON, ProblemDetails, problem_response
from app.core.logging import request_id_var
from app.main import API_TITLE, API_VERSION, build_app, create_app
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
    # A8: never the dev-only demo route.
    assert set(document["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/audit-events",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/reauthenticate",
        "/api/v1/auth/invitation/lookup",
        "/api/v1/auth/invitation/redeem",
        "/api/v1/invitations",
        "/api/v1/invitations/{user_id}/reissue",
        "/api/v1/auth/password",
        "/api/v1/auth/password-reset/lookup",
        "/api/v1/auth/password-reset/redeem",
        "/api/v1/password-resets",
        "/api/v1/auth/registrations",
        "/api/v1/registration-requests",
        "/api/v1/registration-requests/{request_id}/approve",
        "/api/v1/registration-requests/{request_id}/reject",
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

    assert {"ProblemDetails", "FieldError"} <= set(schemas)
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
    }
    assert problem["properties"]["errors"]["items"] == {
        "$ref": "#/components/schemas/FieldError"
    }
    assert set(schemas["FieldError"]["required"]) == {"path", "code", "message"}


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
