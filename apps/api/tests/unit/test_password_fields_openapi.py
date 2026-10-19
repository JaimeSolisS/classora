"""Staff never see or set a password (#30 C4; ADR-0007 D12).

Walks the OpenAPI document: a request-body property whose name contains
``password`` may appear only on the routes that act on the caller's own
account or on a bearer token, and no response schema has a property whose
name contains ``password`` or ``hash``.
"""

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from app.main import build_app

PASSWORD_BODY_ALLOW_LIST = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/reauthenticate",
        "/api/v1/auth/password",
        "/api/v1/auth/invitation/redeem",
        "/api/v1/auth/password-reset/redeem",
        # The requester's own new account (#169).
        "/api/v1/auth/registrations",
    }
)
METHODS = ("get", "put", "post", "patch", "delete")


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    node: Any = document
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return _resolve(document, node)


def property_names(
    document: dict[str, Any], schema: dict[str, Any], seen: frozenset[str] = frozenset()
) -> Iterator[str]:
    """Every property name reachable from ``schema`` (refs, items, unions)."""
    ref = schema.get("$ref")
    if ref is not None:
        if ref in seen:
            return
        seen = seen | {ref}
    resolved = _resolve(document, schema)
    for name, child in resolved.get("properties", {}).items():
        yield name
        yield from property_names(document, child, seen)
    for key in ("items", "additionalProperties"):
        child = resolved.get(key)
        if isinstance(child, dict):
            yield from property_names(document, child, seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for child in resolved.get(key, []):
            yield from property_names(document, child, seen)


def _operations(document: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in document["paths"].items():
        for method in METHODS:
            if method in item:
                yield path, method, item[method]


def password_body_routes(document: dict[str, Any]) -> set[str]:
    """``METHOD path`` of every route whose request body has a password field."""
    found: set[str] = set()
    for path, method, operation in _operations(document):
        content = operation.get("requestBody", {}).get("content", {})
        for media in content.values():
            names = property_names(document, media.get("schema", {}))
            if any("password" in name.lower() for name in names):
                found.add(f"{method.upper()} {path}")
    return found


BOOLEAN_RESPONSE_FLAGS = frozenset({"password_set", "reset_password"})
"""Response fields whose name contains "password" but which are booleans, not
secrets (#31): ``UserDetail.password_set`` and
``UserAllowedActions.reset_password``. A test checks they are booleans."""


def secret_response_fields(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for path, method, operation in _operations(document):
        for status, response in operation.get("responses", {}).items():
            for media in response.get("content", {}).values():
                for name in property_names(document, media.get("schema", {})):
                    lowered = name.lower()
                    if name in BOOLEAN_RESPONSE_FLAGS:
                        continue
                    if "password" in lowered or "hash" in lowered:
                        found.add(f"{method.upper()} {path} {status}: {name}")
    return found


def test_password_fields_only_on_self_or_token_routes():
    document = build_app().openapi()

    routes = password_body_routes(document)

    outside = {r for r in routes if r.split(" ", 1)[1] not in PASSWORD_BODY_ALLOW_LIST}
    assert outside == set()
    # The check sees the routes it allows (it is not vacuous).
    assert {r.split(" ", 1)[1] for r in routes} == PASSWORD_BODY_ALLOW_LIST


def test_no_response_holds_a_password_or_hash():
    assert secret_response_fields(build_app().openapi()) == set()


def test_the_allowed_password_flags_are_booleans():
    schemas = build_app().openapi()["components"]["schemas"]

    flags = {
        (schema_name, name): prop
        for schema_name, schema in schemas.items()
        for name, prop in schema.get("properties", {}).items()
        if name in BOOLEAN_RESPONSE_FLAGS
    }
    assert set(flags) == {
        ("UserDetail", "password_set"),
        ("UserAllowedActions", "reset_password"),
    }
    assert all(prop["type"] == "boolean" for prop in flags.values())


def test_a_staff_route_with_a_password_field_is_found():
    class Nested(BaseModel):
        new_Password: str

    class StaffBody(BaseModel):
        username: str
        settings: list[Nested]

    class Leaky(BaseModel):
        password_hash: str

    app = FastAPI()

    @app.post("/api/v1/users/set-password")
    async def set_password(body: StaffBody) -> Leaky:  # pragma: no cover
        raise NotImplementedError

    document = app.openapi()

    assert password_body_routes(document) == {"POST /api/v1/users/set-password"}
    assert secret_response_fields(document) == {
        "POST /api/v1/users/set-password 200: password_hash"
    }
