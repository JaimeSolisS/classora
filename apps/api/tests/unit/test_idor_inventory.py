"""The IDOR inventory: every scoped route names its hidden-equals-missing test
(M10-01, #149 E2-E4), and the check's self-test."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from app.main import build_app
from tests.authz.harness import load_matrix, parse_matrix
from tests.authz.idor import (
    IDOR_PATH,
    check_inventory,
    failure_message,
    in_scope_routes,
    load_inventory,
    order_problems,
    parse_inventory,
)

# --- The real app ------------------------------------------------------------------


def test_every_scoped_route_has_an_idor_entry():
    problems = check_inventory(
        build_app(include_dev_routes=True).openapi(), load_matrix(), load_inventory()
    )

    assert not problems, failure_message(problems)


def test_idor_file_is_sorted_by_path_then_method():
    assert order_problems(load_inventory()) == []


def test_idor_file_has_a_how_to_header():
    text = IDOR_PATH.read_text(encoding="utf-8")

    assert text.startswith("# IDOR inventory")
    assert "Adding an entry" in text


def test_the_body_and_query_ids_of_known_routes_are_in_scope():
    # Guards the OpenAPI reading: ids outside the path are found too.
    scope = in_scope_routes(build_app(include_dev_routes=True).openapi(), load_matrix())

    assert "body:student_id" in scope[("POST", "/api/v1/evaluations")]
    assert "query:student_id" in scope[("GET", "/api/v1/assignments")]
    assert (
        "body:add_student_ids"
        in scope[("PATCH", "/api/v1/assignments/{assignment_id}")]
    )
    assert ("GET", "/api/v1/exports/{export_id}/download") not in scope


# --- Self-test on a synthetic app, matrix and inventory --------------------------


class _Body(BaseModel):
    student_id: UUID | None = None
    title: str = ""


class _Other(BaseModel):
    note: str = ""


def synthetic_app() -> FastAPI:
    # Access rules come from the synthetic matrix; the routes only shape the
    # OpenAPI document.
    router = APIRouter()

    @router.get("/things/{thing_id}")
    async def thing(thing_id: UUID) -> None: ...

    @router.get("/things")
    async def things(owner_id: UUID | None = None) -> None: ...

    @router.post("/things")
    async def create(body: _Body | _Other) -> None: ...

    @router.get("/plain")
    async def plain() -> None: ...

    @router.get("/staff/{thing_id}")
    async def staff(thing_id: UUID) -> None: ...

    @router.get("/denied/{thing_id}")
    async def denied(thing_id: UUID) -> None: ...

    @router.get("/open/{thing_id}")
    async def open_route(thing_id: UUID) -> None: ...

    @router.get("/tutor/{thing_id}")
    async def tutor(thing_id: UUID) -> None: ...

    app = FastAPI()
    app.include_router(router)
    return app


SYNTHETIC_MATRIX = parse_matrix(
    {
        "route": [
            {
                "method": "GET",
                "path": "/denied/{thing_id}",
                "access": "authenticated",
                "deny": ["tutor", "student", "guardian"],
            },
            {
                "method": "GET",
                "path": "/open/{thing_id}",
                "access": "public",
                "reason": "x",
            },
            {"method": "GET", "path": "/plain", "access": "authenticated"},
            {
                "method": "GET",
                "path": "/staff/{thing_id}",
                "access": "permission",
                "permission": "user.view",
                "allow": ["owner"],
            },
            {"method": "GET", "path": "/things", "access": "authenticated"},
            {"method": "POST", "path": "/things", "access": "authenticated"},
            {
                "method": "GET",
                "path": "/things/{thing_id}",
                "access": "authenticated",
                "deny": ["student"],
            },
            {
                "method": "GET",
                "path": "/tutor/{thing_id}",
                "access": "permission",
                "permission": "user.view",
                "allow": ["owner", "tutor"],
            },
        ]
    }
)

TEST_FILE = """
def test_hidden(): ...
async def test_async_hidden(): ...
def helper(): ...
class TestGroup:
    def test_in_class(self): ...
"""

GOOD = "tests/integration/test_things.py::test_hidden"


def _root(tmp_path: Path) -> Path:
    folder = tmp_path / "tests" / "integration"
    folder.mkdir(parents=True)
    (folder / "test_things.py").write_text(TEST_FILE, encoding="utf-8")
    return tmp_path


def _check(tmp_path: Path, routes: list[dict[str, object]]) -> list[str]:
    return check_inventory(
        synthetic_app().openapi(),
        SYNTHETIC_MATRIX,
        parse_inventory({"route": routes}),
        root=_root(tmp_path),
    )


CORRECT = [
    {"method": "GET", "path": "/things", "exempt": "catalog filter only"},
    {"method": "POST", "path": "/things", "pending": "#12"},
    {
        "method": "GET",
        "path": "/things/{thing_id}",
        "tests": [GOOD, "tests/integration/test_things.py::TestGroup::test_in_class"],
    },
    {
        "method": "GET",
        "path": "/tutor/{thing_id}",
        "tests": ["tests/integration/test_things.py::test_async_hidden"],
        "note": "n",
    },
]


def test_the_synthetic_scope_follows_the_e4_rule():
    scope = in_scope_routes(synthetic_app().openapi(), SYNTHETIC_MATRIX)

    assert scope == {
        ("GET", "/things"): ["query:owner_id"],
        ("POST", "/things"): ["body:student_id"],
        ("GET", "/things/{thing_id}"): ["path:thing_id"],
        ("GET", "/tutor/{thing_id}"): ["path:thing_id"],
    }


def test_a_correct_inventory_reports_nothing(tmp_path: Path):
    assert _check(tmp_path, CORRECT) == []


def test_every_planted_problem_is_reported(tmp_path: Path):
    routes = [
        {"method": "GET", "path": "/denied/{thing_id}", "tests": [GOOD]},
        {"method": "GET", "path": "/things", "tests": [GOOD], "exempt": "x"},
        {"method": "POST", "path": "/things", "pending": "12"},
        {
            "method": "GET",
            "path": "/things/{thing_id}",
            "tests": [
                "tests/integration/test_things.py::helper",
                "tests/integration/test_nope.py::test_hidden",
                "tests/integration/test_things.py::test_hidden[a]",
                "tests/unit/test_things.py::test_hidden",
                "test_hidden",
            ],
            "extra": 1,
        },
        {"method": "GET", "path": "/plain"},
        {"method": "GET", "path": "/open/{thing_id}", "exempt": ""},
    ]

    assert _check(tmp_path, routes) == sorted(
        [
            "GET /denied/{thing_id}: stale entry (not an in-scope route)",
            "GET /things: needs exactly one of tests, exempt or pending"
            " (has tests, exempt)",
            "POST /things: pending must be an issue number like '#123'",
            "GET /things/{thing_id}: unknown field 'extra'",
            "GET /things/{thing_id}: test 'tests/integration/test_things.py::helper':"
            " no such test function",
            "GET /things/{thing_id}: test 'tests/integration/test_nope.py::test_hidden':"
            " no such file",
            "GET /things/{thing_id}: test"
            " 'tests/integration/test_things.py::test_hidden[a]':"
            " name a parametrized test without its [...]",
            "GET /things/{thing_id}: test 'tests/unit/test_things.py::test_hidden'"
            " is not under tests/integration/",
            "GET /things/{thing_id}: test 'test_hidden' is not a"
            " 'file.py::test_name' node id",
            "GET /plain: stale entry (not an in-scope route)",
            "GET /plain: needs exactly one of tests, exempt or pending (has none)",
            "GET /open/{thing_id}: stale entry (not an in-scope route)",
            "GET /open/{thing_id}: exempt must be a non-empty reason",
            "GET /tutor/{thing_id}: missing IDOR entry (ids: path:thing_id)",
            "GET /plain: out of order (must come before GET /things/{thing_id})",
            "GET /open/{thing_id}: out of order (must come before GET /plain)",
        ]
    )


def test_an_empty_tests_list_and_a_duplicate_are_reported(tmp_path: Path):
    routes = [
        *CORRECT[:3],
        {"method": "GET", "path": "/tutor/{thing_id}", "tests": []},
        {"method": "GET", "path": "/tutor/{thing_id}", "tests": [GOOD]},
    ]

    assert _check(tmp_path, routes) == [
        "GET /tutor/{thing_id}: duplicate entry",
        "GET /tutor/{thing_id}: tests must be a non-empty list of node ids",
    ]


def test_failure_message_lists_each_problem():
    assert failure_message(["a", "b"]).endswith(":\na\nb")
