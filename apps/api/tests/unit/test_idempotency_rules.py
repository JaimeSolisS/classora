"""Idempotency keys without a database (#88 B2, B3, B6, B7, E3, E4).

The header rules, the request hash, the serialization of ``respond()``, the
guards that run before any database work, and the OpenAPI description of a
decorated route. Replay, mismatch, concurrency and expiry run against
PostgreSQL in ``tests/integration/test_idempotency.py``.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, FastAPI, Response
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core import idempotency
from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    get_current_principal,
    public,
    require,
)
from app.core.conventions import install_conventions
from app.core.errors import PROBLEM_DETAILS_REF, PROBLEM_JSON
from app.core.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_RESPONSES,
    IdempotencyKeyInvalidError,
    IdempotencyKeyRequiredError,
    IdempotencyRule,
    IdempotentReplay,
    IdempotentRequest,
    canonical_body,
    idempotent,
    parse_key,
    request_hash,
    route_idempotency_rules,
)
from app.core.money import Money
from app.main import build_app

PERMISSION = "user.view"
GOOD_KEY = "k" * 16

# --- B2: the header -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "a" * 16,
        "A" * 128,
        str(uuid.uuid4()),
        "abc_DEF-123_ghi-",
    ],
)
def test_a_well_formed_key_is_accepted(value):
    assert parse_key([value]) == value


@pytest.mark.parametrize("values", [[], [""]])
def test_a_missing_or_empty_key_is_required(values):
    with pytest.raises(IdempotencyKeyRequiredError):
        parse_key(values)


@pytest.mark.parametrize(
    "values",
    [
        ["a" * 15],
        ["a" * 129],
        ["a" * 15 + "!"],
        ["a" * 15 + " "],
        ["a" * 16 + "\n"],
        ["a" * 15 + "é"],
        ["a" * 15 + "٣"],  # a non-ASCII digit
        [GOOD_KEY, GOOD_KEY],
        ["", ""],
        [f"{GOOD_KEY},{GOOD_KEY}"],
    ],
)
def test_a_malformed_or_repeated_key_is_invalid(values):
    with pytest.raises(IdempotencyKeyInvalidError):
        parse_key(values)


def test_the_errors_have_fixed_codes_and_details():
    required, invalid = IdempotencyKeyRequiredError(), IdempotencyKeyInvalidError()
    reused = idempotency.IdempotencyKeyReusedError()

    assert (required.status, required.code) == (400, "idempotency_key_required")
    assert (invalid.status, invalid.code) == (400, "idempotency_key_invalid")
    assert (reused.status, reused.code) == (422, "idempotency_key_reused")
    for error in (required, invalid, reused):
        assert error.detail
        assert error.errors is None and error.details is None


def test_the_retention_is_seven_days():
    assert idempotency.RETENTION.days == 7
    assert idempotency.RETENTION.seconds == 0


# --- B3: the hash ---------------------------------------------------------------------

PATH = "/api/v1/billing/payments/0199a000-0000-7000-8000-000000000001"
BODY = {"amount": "10.00", "lines": [{"b": 1, "a": 2}], "note": "café"}


def dumped(value: Any, **options: Any) -> bytes:
    return json.dumps(value, **options).encode()


def test_key_order_and_whitespace_do_not_change_the_hash():
    compact = dumped(BODY, separators=(",", ":"))
    spaced = dumped(BODY, indent=4)
    reordered = dumped({"note": "café", "lines": [{"a": 2, "b": 1}], "amount": "10.00"})
    escaped = dumped(BODY, ensure_ascii=True)

    hashes = {
        request_hash("POST", PATH, b) for b in (compact, spaced, reordered, escaped)
    }

    assert len(hashes) == 1
    assert len(hashes.pop()) == 32


def test_the_canonical_body_sorts_keys_without_whitespace_or_escapes():
    assert canonical_body(dumped(BODY, indent=2)) == (
        '{"amount":"10.00","lines":[{"a":2,"b":1}],"note":"café"}'.encode()
    )
    assert canonical_body(b"") == b""
    assert canonical_body(b"not json") == b"not json"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", PATH, BODY),
        ("POST", PATH.replace("0001", "0002"), BODY),
        ("POST", "/api/v1/billing/charges", BODY),
        ("POST", PATH, {**BODY, "amount": "10.01"}),
        ("POST", PATH, {**BODY, "lines": [{"b": 1, "a": 3}]}),
        ("POST", PATH, {**BODY, "extra": None}),
        ("POST", PATH, [BODY]),
    ],
)
def test_the_method_the_path_and_every_value_change_the_hash(method, path, body):
    assert request_hash(method, path, dumped(body)) != request_hash(
        "POST", PATH, dumped(BODY)
    )


def test_an_empty_body_hashes_as_empty():
    assert request_hash("POST", PATH, b"") == request_hash("POST", PATH, b"")
    assert request_hash("POST", PATH, b"") != request_hash("POST", PATH, b"{}")


def test_the_fields_are_separated():
    # "POST" + "/a" + body "" never collides with "POS" + "T/a".
    assert request_hash("POST", "/a", b"") != request_hash("POS", "T/a", b"")


# --- B6: respond() --------------------------------------------------------------------


class Line(BaseModel):
    label: str
    amount: Money


class Receipt(BaseModel):
    id: uuid.UUID
    total: Money
    lines: list[Line]
    note: str | None = None
    number: str = Field(serialization_alias="receiptNumber")


RECEIPT = Receipt(
    id=uuid.UUID("0199a000-0000-7000-8000-0000000000aa"),
    total=Money("1250.5"),
    lines=[
        Line(label="Clase", amount=Money("1000")),
        Line(label="ñ", amount=Money("250.5")),
    ],
    number="R-2026-000001",
)


class FakeSession:
    """Records the statements ``respond()`` executes."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement)


def claimed_handle() -> tuple[IdempotentRequest, FakeSession]:
    session = FakeSession()
    handle = IdempotentRequest(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid7(),
        endpoint="POST /x",
        key=GOOD_KEY,
        request_hash=b"\x00" * 32,
        method="POST",
        path="/x",
    )
    handle.state = "claimed"
    return handle, session


def fastapi_body(model: BaseModel) -> bytes:
    """The bytes FastAPI writes for ``model`` as a route's ``response_model``."""
    app = FastAPI()

    @app.get("/receipt", response_model=Receipt)
    async def receipt() -> Any:
        return model

    with TestClient(app) as client:
        return client.get("/receipt").content


@pytest.mark.anyio
async def test_respond_serializes_like_the_response_model():
    handle, session = claimed_handle()

    response = await handle.respond(RECEIPT, status_code=201)

    assert response.status_code == 201
    assert response.media_type == "application/json"
    assert response.body == fastapi_body(RECEIPT)
    assert json.loads(response.body)["total"] == "1250.50"
    assert "receiptNumber" in json.loads(response.body)
    assert handle.responded and len(session.statements) == 1
    params = session.statements[0].compile().params
    assert params["response_status"] == 201
    assert params["response_body"] == json.loads(response.body)


@pytest.mark.anyio
async def test_respond_with_204_has_no_body():
    handle, session = claimed_handle()

    response = await handle.respond(None, status_code=204)

    assert response.status_code == 204
    assert response.body == b""
    assert session.statements[0].compile().params["response_status"] == 204


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "status"),
    [
        (RECEIPT, 199),
        (RECEIPT, 300),
        (RECEIPT, 409),
        (RECEIPT, 204),
        (None, 201),
        ({"total": "1.00"}, 201),
    ],
)
async def test_respond_refuses_other_statuses_and_contents(content, status):
    handle, session = claimed_handle()

    with pytest.raises(ValueError):
        await handle.respond(content, status_code=status)

    assert session.statements == []
    assert handle.state == "claimed"


@pytest.mark.anyio
async def test_respond_twice_is_a_programming_error():
    handle, _ = claimed_handle()
    await handle.respond(RECEIPT, status_code=200)

    with pytest.raises(RuntimeError):
        await handle.respond(RECEIPT, status_code=200)


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["new", "replayed"])
async def test_respond_before_a_claim_or_after_a_replay_is_a_programming_error(state):
    handle, session = claimed_handle()
    handle.state = state

    with pytest.raises(RuntimeError):
        await handle.respond(RECEIPT, status_code=201)

    assert session.statements == []


def test_the_handle_repr_hides_the_key_and_the_hash():
    handle, _ = claimed_handle()

    assert GOOD_KEY not in repr(handle)
    assert "00" * 32 not in repr(handle)


# --- B7, E6: guards and the header check through HTTP ---------------------------------


class ItemIn(BaseModel):
    amount: Money


class ItemOut(BaseModel):
    amount: Money


OWNER = Principal(
    user_id=uuid.UUID("0199a000-0000-7000-8000-0000000000b1"),
    roles=frozenset({"owner"}),
    permissions=frozenset({PERMISSION}),
)


def build_sample_app() -> FastAPI:
    app = FastAPI()
    install_conventions(app)
    app.dependency_overrides[get_current_principal] = lambda: OWNER
    router = APIRouter(prefix="/api/v1/sample")
    responses = {**PERMISSION_RESPONSES, **IDEMPOTENCY_RESPONSES}

    @router.post(
        "/items/{item_id}",
        status_code=201,
        response_model=ItemOut,
        responses=responses,
        dependencies=[require(PERMISSION)],
    )
    async def create_item(
        item_id: uuid.UUID,
        body: ItemIn,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        return await idem.respond(ItemOut(amount=body.amount), status_code=201)

    @router.get("/read", dependencies=[require(PERMISSION)])
    async def read(idem: Annotated[IdempotentRequest, idempotent()]) -> None:
        return None

    @router.post("/early", status_code=201)
    async def early(
        idem: Annotated[IdempotentRequest, idempotent()],
        principal: Annotated[Principal, require(PERMISSION)],
    ) -> None:
        return None

    @router.post("/public", dependencies=[public(reason="sample")])
    async def open_route(idem: Annotated[IdempotentRequest, idempotent()]) -> None:
        return None

    @router.post("/plain", dependencies=[require(PERMISSION)])
    async def plain(body: ItemIn) -> ItemOut:
        return ItemOut(amount=body.amount)

    app.include_router(router)
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(build_sample_app(), raise_server_exceptions=False) as test_client:
        yield test_client


ITEMS = "/api/v1/sample/items/0199a000-0000-7000-8000-0000000000c1"


def code_of(response: Any) -> str:
    assert response.headers["content-type"] == PROBLEM_JSON
    return str(response.json()["code"])


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "idempotency_key_required"),
        ({IDEMPOTENCY_HEADER: ""}, "idempotency_key_required"),
        ({IDEMPOTENCY_HEADER: "short"}, "idempotency_key_invalid"),
        ({IDEMPOTENCY_HEADER: "x" * 129}, "idempotency_key_invalid"),
    ],
)
def test_a_missing_or_bad_header_is_400_never_fastapis_422(client, headers, code):
    response = client.post(ITEMS, json={"amount": "1.00"}, headers=headers)

    assert response.status_code == 400
    assert code_of(response) == code
    assert "short" not in response.text


def test_a_missing_key_wins_over_an_invalid_body(client):
    response = client.post(ITEMS, json={"amount": 12.5})

    assert response.status_code == 400
    assert code_of(response) == "idempotency_key_required"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/sample/read"),
        ("POST", "/api/v1/sample/early"),
        ("POST", "/api/v1/sample/public"),
    ],
    ids=["safe-method", "before-the-access-rule", "public-route"],
)
def test_a_misdeclared_route_is_a_programming_error(client, method, path):
    response = client.request(method, path, headers={IDEMPOTENCY_HEADER: GOOD_KEY})

    assert response.status_code == 500
    assert code_of(response) == "internal_error"


def test_a_replay_renders_the_stored_response():
    app = FastAPI()
    install_conventions(app)

    @app.post("/replay")
    async def replay() -> None:
        raise IdempotentReplay(201, {"amount": "1.00"})

    @app.post("/replay-204")
    async def replay_204() -> None:
        raise IdempotentReplay(204, None)

    with TestClient(app) as test_client:
        stored = test_client.post("/replay")
        empty = test_client.post("/replay-204")

    assert stored.status_code == 201
    assert stored.json() == {"amount": "1.00"}
    assert stored.headers["content-type"] == "application/json"
    assert stored.headers[idempotency.REPLAYED_HEADER] == "true"
    assert stored.headers["x-request-id"]
    assert (empty.status_code, empty.content) == (204, b"")
    assert empty.headers[idempotency.REPLAYED_HEADER] == "true"


def api_contexts(app: FastAPI) -> list[Any]:
    return [
        c
        for c in iter_route_contexts(app.routes)
        if isinstance(c.original_route, APIRoute)
    ]


# --- E3, E4: the marker and the OpenAPI document --------------------------------------


def test_the_marker_is_found_on_decorated_routes_only():
    app = build_sample_app()
    marked = {
        (method, context.path)
        for context in api_contexts(app)
        if route_idempotency_rules(context)
        for method in context.methods or ()
    }

    assert marked == {
        ("POST", "/api/v1/sample/items/{item_id}"),
        ("GET", "/api/v1/sample/read"),
        ("POST", "/api/v1/sample/early"),
        ("POST", "/api/v1/sample/public"),
    }
    [context] = [
        c
        for c in iter_route_contexts(app.routes)
        if c.path == "/api/v1/sample/items/{item_id}"
    ]
    assert route_idempotency_rules(context) == [IdempotencyRule()]


def test_openapi_lists_the_header_as_required_with_400_and_422():
    document = build_sample_app().openapi()
    operation = document["paths"]["/api/v1/sample/items/{item_id}"]["post"]

    [header] = [p for p in operation["parameters"] if p["in"] == "header"]
    assert header["name"] == IDEMPOTENCY_HEADER
    assert header["required"] is True
    assert header["schema"] == {
        "type": "string",
        "minLength": 16,
        "maxLength": 128,
        "pattern": "^[A-Za-z0-9_-]{16,128}$",
        "title": IDEMPOTENCY_HEADER,
    }
    for status in ("400", "422"):
        content = operation["responses"][status]["content"]
        assert content[PROBLEM_JSON]["schema"] == {"$ref": PROBLEM_DETAILS_REF}
    assert set(IDEMPOTENCY_RESPONSES) == {400, 422}


def test_openapi_adds_the_header_once_and_only_to_decorated_routes():
    app = build_sample_app()
    app.openapi()
    document = app.openapi()

    operation = document["paths"]["/api/v1/sample/items/{item_id}"]["post"]
    headers = [p for p in operation["parameters"] if p["in"] == "header"]
    plain = document["paths"]["/api/v1/sample/plain"]["post"]
    assert len(headers) == 1
    assert IDEMPOTENCY_HEADER not in json.dumps(plain)


DECORATED_ROUTES = {
    ("/api/v1/billing/packages", "post"),
    ("/api/v1/families/{family_id}/billing-account/payments", "post"),
    ("/api/v1/families/{family_id}/billing-account/allocations", "post"),
}


def test_the_real_app_declares_only_the_ledger_posting_routes():
    # E4: #88 changed no route; #94 declares the first one (the package
    # purchase), #96 the payment and the credit allocation; the header is
    # documented only there.
    app = build_app(include_dev_routes=True)

    decorated = [c for c in api_contexts(app) if route_idempotency_rules(c)]
    assert {
        (c.path, method.lower())
        for c in decorated
        for method in c.original_route.methods
    } == DECORATED_ROUTES
    document = app.openapi()
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            parameters = json.dumps(operation.get("parameters", []))
            expected = (path, method) in DECORATED_ROUTES
            assert (IDEMPOTENCY_HEADER in parameters) == expected, (path, method)
