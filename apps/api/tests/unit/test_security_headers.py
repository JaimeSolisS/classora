"""``Cache-Control: no-store`` and ``X-Content-Type-Options: nosniff`` on every
API response (#24 D)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    get_current_principal,
    public,
    require,
)
from app.core.conventions import install_conventions
from app.core.middleware import RequestContextMiddleware
from app.core.security_headers import (
    API_SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)
from app.main import build_app
from tests.authz.csrf import ORIGIN_HEADER, send_without_origin
from tests.unit.authz_app import build_authz_app

EXPECTED = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


class Body(BaseModel):
    amount: int


def headers_app() -> FastAPI:
    """The sample app (Origin check, conventions) plus probe routes."""
    app = build_authz_app()

    @app.get("/probe/ok", dependencies=[public(reason="probe")])
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/probe/own-headers", dependencies=[public(reason="probe")])
    async def own_headers(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "public, max-age=600"
        response.headers["X-Content-Type-Options"] = "sniff-please"
        return {"status": "ok"}

    @app.get("/probe/crash", dependencies=[public(reason="probe")])
    async def crash() -> None:
        raise RuntimeError("boom")

    @app.post("/probe/body", dependencies=[public(reason="probe")])
    async def body(payload: Body) -> Body:
        return payload

    @app.get(
        "/probe/denied",
        dependencies=[require("user.view")],
        responses=PERMISSION_RESPONSES,
    )
    async def denied() -> None: ...

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = headers_app()
    with TestClient(app, raise_server_exceptions=False, headers=ORIGIN_HEADER) as c:
        yield c


def nobody(app: FastAPI) -> None:
    principal = Principal(
        user_id=uuid.uuid7(), roles=frozenset(), permissions=frozenset()
    )
    app.dependency_overrides[get_current_principal] = lambda: principal


CASES = [
    pytest.param("GET", "/probe/ok", None, 200, id="200"),
    pytest.param("GET", "/probe/own-headers", None, 200, id="route-set-values"),
    pytest.param("GET", "/sample/me", None, 401, id="401"),
    pytest.param("GET", "/probe/denied", "nobody", 403, id="403-permission"),
    pytest.param("POST", "/probe/body", "no-origin", 403, id="403-csrf"),
    pytest.param("GET", "/nope", None, 404, id="404-unknown-path"),
    pytest.param("DELETE", "/probe/ok", None, 405, id="405"),
    pytest.param("POST", "/probe/body", None, 422, id="422"),
    pytest.param("GET", "/probe/crash", None, 500, id="500-last-resort"),
    pytest.param("GET", "/docs", None, 200, id="docs"),
    pytest.param("GET", "/redoc", None, 200, id="redoc"),
    pytest.param("GET", "/openapi.json", None, 200, id="openapi"),
]


@pytest.mark.parametrize(("method", "path", "setup", "status"), CASES)
def test_every_response_has_the_headers_exactly_once(
    client, method, path, setup, status
):
    if setup == "nobody":
        nobody(client.app)
    if setup == "no-origin":
        response = send_without_origin(client, method, path, json={"amount": 1})
    else:
        response = client.request(method, path, json={"amount": "x"})

    assert response.status_code == status, response.text
    raw = [(k.lower(), v) for k, v in response.headers.multi_items()]
    for name, value in EXPECTED.items():
        assert [v for k, v in raw if k == name] == [value], name


@pytest.mark.parametrize("path", ["/health/live", "/openapi.json", "/docs"])
def test_the_real_app_sets_them_on_probes_and_docs(path):
    with TestClient(build_app(), raise_server_exceptions=False) as client:
        response = client.get(path)

    assert response.status_code == 200
    for name, value in EXPECTED.items():
        assert response.headers.get_list(name) == [value]


def test_the_middleware_is_outside_the_request_context_middleware():
    app = FastAPI()
    install_conventions(app)

    classes = [m.cls for m in app.user_middleware]

    # user_middleware lists the outermost first.
    assert classes.index(SecurityHeadersMiddleware) < classes.index(
        RequestContextMiddleware
    )


def test_the_header_set_is_exactly_the_two_documented_headers():
    assert dict((k.decode(), v.decode()) for k, v in API_SECURITY_HEADERS) == EXPECTED


def test_repeated_route_values_become_one_header():
    app = build_authz_app()

    @app.get("/probe/x", dependencies=[public(reason="probe")])
    async def probe(response: Response) -> str:
        response.headers.append("cache-control", "max-age=1")
        response.headers.append("cache-control", "private")
        return "ok"

    with TestClient(app) as client:
        response = client.get("/probe/x")

    assert response.headers.get_list("cache-control") == ["no-store"]
