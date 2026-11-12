"""The IDOR inventory (M10-01): every scoped route and its hidden-equals-missing test.

``idor.toml`` lists every API route that a tutor, student or guardian can call
with a record id. :func:`check_inventory` compares it with the permission
matrix and an app's OpenAPI document, and lists every problem as
``METHOD path: reason``. ``tests/unit/test_idor_inventory.py`` runs it on the
real app.

In scope (#149 E4): a matrix entry reachable by ``tutor``, ``student`` or
``guardian`` (``authenticated`` minus ``deny``, or ``permission`` with one of
them in ``allow``) whose route takes a path parameter, or a query parameter or
top-level JSON body property named ``*_id``/``*_ids``.
"""

import ast
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.authz.harness import MatrixEntry

IDOR_PATH = Path(__file__).with_name("idor.toml")
API_ROOT = Path(__file__).resolve().parents[2]
"""``apps/api``: test node ids are relative to it."""

SCOPED_ROLES = frozenset({"tutor", "student", "guardian"})
ENTRY_FIELDS = frozenset({"method", "path", "tests", "exempt", "pending", "note"})
EVIDENCE_FIELDS = ("tests", "exempt", "pending")
NODE_PREFIX = "tests/integration/"

_PATH_PARAM = re.compile(r"{([^}:]+)(?::[^}]*)?}")
_ID_NAME = re.compile(r"^[a-z0-9_]+_ids?$")
_PENDING = re.compile(r"^#\d+$")


@dataclass(frozen=True)
class IdorEntry:
    """One ``[[route]]`` table of ``idor.toml``, kept as written."""

    method: Any
    path: Any
    tests: Any = None
    exempt: Any = None
    pending: Any = None
    note: Any = None
    unknown_fields: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.method), str(self.path))

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


def parse_inventory(document: Mapping[str, Any]) -> list[IdorEntry]:
    """The entries of a parsed inventory document (``{"route": [...]}``)."""
    entries = []
    for table in document.get("route", []):
        values = {name: table[name] for name in ENTRY_FIELDS if name in table}
        entries.append(
            IdorEntry(
                **{"method": None, "path": None, **values},
                unknown_fields=tuple(sorted(set(table) - ENTRY_FIELDS)),
            )
        )
    return entries


def load_inventory(path: Path = IDOR_PATH) -> list[IdorEntry]:
    with path.open("rb") as file:
        return parse_inventory(tomllib.load(file))


# --- The in-scope set ------------------------------------------------------------


def reachable_by_scoped_role(entry: MatrixEntry) -> bool:
    """A tutor, student or guardian may call the route (E4)."""
    if entry.access == "authenticated":
        return bool(SCOPED_ROLES - entry.denied_roles)
    if entry.access == "permission":
        return bool(SCOPED_ROLES & entry.allowed_roles)
    return False


def _resolve(document: Mapping[str, Any], schema: Any) -> Any:
    seen: set[str] = set()
    while isinstance(schema, Mapping) and "$ref" in schema:
        ref = str(schema["$ref"])
        if ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        target: Any = document
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, Mapping) else {}
        schema = target
    return schema if isinstance(schema, Mapping) else {}


def _top_level_properties(document: Mapping[str, Any], schema: Any) -> set[str]:
    """Property names of a body schema, through ``$ref``/``anyOf``/``oneOf``/``allOf``."""
    names: set[str] = set()
    pending = [schema]
    visited = 0
    while pending and visited < 50:
        visited += 1
        current = _resolve(document, pending.pop())
        names.update(current.get("properties", {}) or {})
        for combinator in ("anyOf", "oneOf", "allOf"):
            pending.extend(current.get(combinator, []) or [])
    return names


def route_ids(document: Mapping[str, Any], method: str, path: str) -> list[str]:
    """The ids a route takes: path parameters, then ``*_id``/``*_ids`` query
    parameters and top-level JSON body properties (labelled by location)."""
    ids = [f"path:{name}" for name in _PATH_PARAM.findall(path)]
    template = _PATH_PARAM.sub(lambda m: "{" + m.group(1) + "}", path)
    operation = document.get("paths", {}).get(template, {}).get(method.lower(), {})
    for parameter in operation.get("parameters", []) or []:
        parameter = _resolve(document, parameter)
        name = str(parameter.get("name", ""))
        if parameter.get("in") == "query" and _ID_NAME.match(name):
            ids.append(f"query:{name}")
    content = (operation.get("requestBody") or {}).get("content", {}) or {}
    schema = (content.get("application/json") or {}).get("schema")
    if schema is not None:
        ids.extend(
            f"body:{name}"
            for name in sorted(_top_level_properties(document, schema))
            if _ID_NAME.match(name)
        )
    return ids


def in_scope_routes(
    document: Mapping[str, Any], matrix: Iterable[MatrixEntry]
) -> dict[tuple[str, str], list[str]]:
    """``(method, path)`` → ids, for every in-scope matrix entry."""
    scope: dict[tuple[str, str], list[str]] = {}
    for entry in matrix:
        if not reachable_by_scoped_role(entry):
            continue
        ids = route_ids(document, str(entry.method), str(entry.path))
        if ids:
            scope[entry.key] = ids
    return scope


# --- The check -------------------------------------------------------------------


def order_problems(entries: Iterable[IdorEntry]) -> list[str]:
    """Entries must be sorted by path, then method (as ``matrix.toml``)."""
    keys = [(str(e.path), str(e.method)) for e in entries]
    return [
        f"{after[1]} {after[0]}: out of order"
        f" (must come before {before[1]} {before[0]})"
        for before, after in zip(keys, keys[1:], strict=False)
        if after < before
    ]


def _defined_tests(file: Path) -> set[str]:
    """``name`` and ``Class::name`` of the module-level ``test_*`` functions
    and the ``test_*`` methods of module-level classes (parsed, not imported)."""
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, functions) and node.name.startswith("test_"):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.update(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, functions) and item.name.startswith("test_")
            )
    return names


def node_problem(node_id: Any, root: Path, cache: dict[Path, set[str]]) -> str | None:
    """Why a cited test node id does not resolve, or ``None``."""
    if not isinstance(node_id, str) or "::" not in node_id:
        return f"test {node_id!r} is not a 'file.py::test_name' node id"
    file_part, _, name = node_id.partition("::")
    if "[" in name:
        return f"test {node_id!r}: name a parametrized test without its [...]"
    if not file_part.startswith(NODE_PREFIX) or not file_part.endswith(".py"):
        return f"test {node_id!r} is not under {NODE_PREFIX}"
    file = root / file_part
    if not file.is_file():
        return f"test {node_id!r}: no such file"
    if file not in cache:
        cache[file] = _defined_tests(file)
    if name not in cache[file]:
        return f"test {node_id!r}: no such test function"
    return None


def entry_problems(
    entry: IdorEntry, root: Path, cache: dict[Path, set[str]]
) -> list[str]:
    """Format problems of one entry (field kinds, evidence, node ids)."""
    problems = [f"unknown field {name!r}" for name in entry.unknown_fields]
    given = [name for name in EVIDENCE_FIELDS if getattr(entry, name) is not None]
    if len(given) != 1:
        problems.append(
            "needs exactly one of tests, exempt or pending"
            f" (has {', '.join(given) or 'none'})"
        )
    if entry.tests is not None:
        if not isinstance(entry.tests, list) or not entry.tests:
            problems.append("tests must be a non-empty list of node ids")
        else:
            problems.extend(
                problem
                for node_id in entry.tests
                if (problem := node_problem(node_id, root, cache)) is not None
            )
    if entry.exempt is not None and (
        not isinstance(entry.exempt, str) or not entry.exempt.strip()
    ):
        problems.append("exempt must be a non-empty reason")
    if entry.pending is not None and (
        not isinstance(entry.pending, str) or not _PENDING.match(entry.pending)
    ):
        problems.append("pending must be an issue number like '#123'")
    if entry.note is not None and (
        not isinstance(entry.note, str) or not entry.note.strip()
    ):
        problems.append("note must be a non-empty string")
    return problems


def check_inventory(
    document: Mapping[str, Any],
    matrix: Iterable[MatrixEntry],
    inventory: list[IdorEntry],
    *,
    root: Path = API_ROOT,
) -> list[str]:
    """Every problem of ``inventory`` against the in-scope routes, sorted."""
    scope = in_scope_routes(document, matrix)
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    cache: dict[Path, set[str]] = {}
    for entry in inventory:
        if entry.key in seen:
            problems.append(f"{entry.label}: duplicate entry")
        seen.add(entry.key)
        if entry.key not in scope:
            problems.append(f"{entry.label}: stale entry (not an in-scope route)")
        problems.extend(
            f"{entry.label}: {problem}"
            for problem in entry_problems(entry, root, cache)
        )
    problems.extend(
        f"{method} {path}: missing IDOR entry (ids: {', '.join(ids)})"
        for (method, path), ids in scope.items()
        if (method, path) not in seen
    )
    problems.extend(order_problems(inventory))
    return sorted(set(problems))


def failure_message(problems: list[str]) -> str:
    return (
        "IDOR inventory problems (tests/authz/idor.toml, see"
        " _docs/testing-guidelines.md 'IDOR inventory'):\n" + "\n".join(problems)
    )
