"""The codes the web must translate, as a generated file (#130 B1, B2; E2).

ADR-0015: the API sends stable codes and the client translates them. This
module lists every code the running app can send, so the web's tests can
require a label for each one in Spanish and English. Run from ``apps/api``::

    uv run python -m app.core.i18n_inventory \
        ../../packages/api-client/i18n-inventory.json

``make api-client`` writes the file next to ``openapi.json`` and CI's
``check-api-client`` fails when it is stale. The lists, each sorted and
unique:

- ``problem_codes``: the ``code`` of every :class:`AppError` subclass under
  ``app`` that defines one in its own body (an abstract base marks itself with
  ``abstract_problem = True`` and is left out), plus
  :data:`~app.core.errors.CORE_PROBLEM_CODES`;
- ``field_codes``: the field-error codes found statically in ``app/`` (see
  :func:`field_code_scan`);
- ``notification_types``: the notification type registry after wiring (#119);
- ``job_tasks``: the production job app's tasks, without Procrastinate's
  ``builtin:`` and the local ``demo:`` ones (#128 D3);
- ``import_row_codes``: :data:`IMPORT_ROW_CODES` (#122, #123).

Like :mod:`app.core.openapi`, it needs no settings, database or server, and
its output is deterministic (2-space JSON, sorted keys, trailing newline).
"""

import ast
import importlib
import json
import pkgutil
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

APP_ROOT: Final = Path(__file__).resolve().parents[1]
"""The ``app`` package directory."""

CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")
"""A string literal that can be a code (messages and field paths aren't)."""

_EXCLUDED_TASK_PREFIXES: Final = ("builtin:", "demo:")
_LOCAL_NOTIFICATION_PREFIX: Final = "dev."


def _import_app_modules() -> None:
    """Import every module of ``app`` (not the ``__main__`` entry points), so
    every ``AppError`` subclass exists, whether a router, a task or a seed
    defines it."""
    import app

    for module in pkgutil.walk_packages(app.__path__, "app."):
        if not module.name.endswith("__main__"):
            importlib.import_module(module.name)


def _subclasses[T](cls: type[T]) -> Iterator[type[T]]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def problem_codes() -> list[str]:
    """Every Problem ``code`` the app can send."""
    from app.core.errors import CORE_PROBLEM_CODES, AppError

    _import_app_modules()
    codes = set(CORE_PROBLEM_CODES)
    for cls in _subclasses(AppError):
        if not cls.__module__.startswith("app."):
            continue  # a test's own subclass
        if "code" in cls.__dict__ and not cls.__dict__.get("abstract_problem", False):
            codes.add(cls.code)
    return sorted(codes)


# --- Field codes (static scan) ---------------------------------------------------


@dataclass
class FieldCodeScan:
    """The result of :func:`field_code_scan`."""

    codes: set[str] = field(default_factory=set)
    dynamic_sites: set[str] = field(default_factory=set)
    """``<path>:<function>`` of each construction whose code the scan can't
    resolve to literals (an attribute, a subscript, a call)."""
    dynamic_expressions: dict[str, set[str]] = field(default_factory=dict)
    """The unresolved code expressions of each dynamic site (diagnostics)."""


type _Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class _Module:
    path: str
    tree: ast.Module
    parents: dict[ast.AST, ast.AST]
    dicts: dict[str, ast.Dict]
    constants: dict[str, str]


def _module(path: Path, base: Path) -> _Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    dicts: dict[str, ast.Dict] = {}
    constants: dict[str, str] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = statement.value.value
        if isinstance(statement, ast.Assign | ast.AnnAssign) and isinstance(
            statement.value, ast.Dict
        ):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    dicts[target.id] = statement.value
    return _Module(path.relative_to(base).as_posix(), tree, parents, dicts, constants)


def _enclosing(module: _Module, node: ast.AST) -> _Function | None:
    while node in module.parents:
        node = module.parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    return None


def _enclosing_class(module: _Module, node: ast.AST) -> str | None:
    while node in module.parents:
        node = module.parents[node]
        if isinstance(node, ast.ClassDef):
            return node.name
    return None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_message(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return " " in node.value
    return isinstance(node, ast.JoinedStr)


def _is_issue_tuple(node: ast.Tuple) -> bool:
    """A ``(path, code, message)`` issue tuple (the academics field errors):
    the code is a literal, and the path is a list starting with ``"body"`` or
    ``"query"``, or the message is a sentence."""
    if len(node.elts) != 3:
        return False
    path, code, message = node.elts
    if isinstance(code, ast.Name | ast.Attribute):
        # ``(path, code, message)`` in a helper, or ``rules.PERIOD_INVALID``.
        return (isinstance(message, ast.Name) and message.id == "message") or (
            _is_message(message)
        )
    if not (
        isinstance(code, ast.Constant)
        and isinstance(code.value, str)
        and CODE_PATTERN.fullmatch(code.value)
    ):
        return False
    if isinstance(path, ast.List | ast.Tuple) and path.elts:
        first = path.elts[0]
        if isinstance(first, ast.Constant) and first.value in ("body", "query"):
            return True
    return _is_message(message)


@dataclass(frozen=True)
class _Helper:
    """A function that passes its parameter on as a field code."""

    module: str
    callee: str  # the name call sites use (the class for ``__init__``)
    parameter: str
    index: int | None  # positional index at the call site


class _FieldScanner:
    def __init__(self, modules: list[_Module]) -> None:
        self.modules = modules
        self.dicts: dict[str, list[ast.Dict]] = {}
        self.constants: dict[str, set[str]] = {}
        for module in modules:
            for name, mapping in module.dicts.items():
                self.dicts.setdefault(name, []).append(mapping)
            for name, text in module.constants.items():
                self.constants.setdefault(name, set()).add(text)
        self.result = FieldCodeScan()
        self.helpers: set[_Helper] = set()

    def _site(self, module: _Module, node: ast.AST) -> str:
        function = _enclosing(module, node)
        return f"{module.path}:{function.name if function else '<module>'}"

    def _map_keys(self, module: _Module, name: str) -> bool:
        """Collect the code-like keys of the message map ``name``: the
        module's own, or a public one imported from another module."""
        own = module.dicts.get(name)
        mappings = [own] if own is not None else []
        if not mappings and not name.startswith("_"):
            mappings = self.dicts.get(name, [])
        for mapping in mappings:
            for key in mapping.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if CODE_PATTERN.fullmatch(key.value):
                        self.result.codes.add(key.value)
        return bool(mappings)

    def _message_keys(self, module: _Module, code: ast.expr, message: ast.expr) -> bool:
        # ``_MESSAGES[code]``: every key of that message map is a code the
        # construction can send.
        if (
            isinstance(message, ast.Subscript)
            and isinstance(message.value, ast.Name)
            and isinstance(code, ast.Name)
            and isinstance(message.slice, ast.Name)
            and message.slice.id == code.id
        ):
            return self._map_keys(module, message.value.id)
        return False

    def _helper(self, module: _Module, function: _Function, name: str) -> bool:
        arguments = function.args
        positional = [a.arg for a in arguments.posonlyargs + arguments.args]
        keyword = [a.arg for a in arguments.kwonlyargs]
        if name not in positional and name not in keyword:
            return False
        index = positional.index(name) if name in positional else None
        callee = function.name
        if callee == "__init__":
            owner = _enclosing_class(module, function)
            if owner is None:
                return False
            callee = owner
        if positional and positional[0] in ("self", "cls") and index is not None:
            index -= 1
        self.helpers.add(_Helper(module.path, callee, name, index))
        return True

    def resolve(self, module: _Module, node: ast.AST, expr: ast.expr) -> bool:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            if CODE_PATTERN.fullmatch(expr.value):
                self.result.codes.add(expr.value)
                return True
            return False
        if isinstance(expr, ast.IfExp):
            body = self.resolve(module, node, expr.body)
            orelse = self.resolve(module, node, expr.orelse)
            return body and orelse
        if isinstance(expr, ast.Attribute) and not expr.attr.startswith("_"):
            # ``rules.PERIOD_INVALID``: a public constant with one value.
            values = self.constants.get(expr.attr, set())
            if len(values) == 1:
                return self.resolve(module, node, ast.Constant(next(iter(values))))
            return False
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            # ``code = value_rule(...)``: the literals a same-module function
            # returns (``None`` means no error).
            return self._returns(module, expr.func.id)
        if isinstance(expr, ast.Name):
            # The innermost function first, then each enclosing one (a
            # validator defined inside a factory reads the factory's
            # parameter).
            function = _enclosing(module, node)
            while function is not None:
                if self._helper(module, function, expr.id):
                    return True
                if self._locals(module, function, expr.id):
                    return True
                function = _enclosing(module, function)
            # A module-level constant: the module's own, or a public one
            # (``MONEY_INVALID``) with one value across ``app``.
            if expr.id in module.constants:
                return self.resolve(
                    module, node, ast.Constant(module.constants[expr.id])
                )
            values = self.constants.get(expr.id, set())
            if not expr.id.startswith("_") and len(values) == 1:
                return self.resolve(module, node, ast.Constant(next(iter(values))))
            return False
        return False

    def _locals(self, module: _Module, function: _Function, name: str) -> bool:
        found = False
        for inner in ast.walk(function):
            if isinstance(inner, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in inner.targets
            ):
                found = self.resolve(module, inner, inner.value) or found
            elif (
                isinstance(inner, ast.For)
                and isinstance(inner.target, ast.Name)
                and inner.target.id == name
                and isinstance(inner.iter, ast.Tuple | ast.List | ast.Set)
            ):
                for element in inner.iter.elts:
                    found = self.resolve(module, inner, element) or found
        return found

    def _returns(self, module: _Module, name: str) -> bool:
        functions = [
            node
            for node in module.tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ]
        found = False
        for function in functions:
            for inner in ast.walk(function):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    value = inner.value
                    if isinstance(value, ast.Constant) and value.value is None:
                        continue
                    found = self.resolve(module, inner, value) or found
        return found

    def _sink(self, module: _Module, node: ast.AST, code: ast.expr) -> None:
        if not self.resolve(module, node, code):
            site = self._site(module, node)
            self.result.dynamic_sites.add(site)
            self.result.dynamic_expressions.setdefault(site, set()).add(
                ast.unparse(code)
            )

    def scan_sinks(self) -> None:
        for module in self.modules:
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Dict):
                    keys = [
                        k.value if isinstance(k, ast.Constant) else None
                        for k in node.keys
                    ]
                    if "path" in keys and "code" in keys:
                        code = node.values[keys.index("code")]
                        mapped = "message" in keys and self._message_keys(
                            module, code, node.values[keys.index("message")]
                        )
                        if not mapped:
                            self._sink(module, node, code)
                elif isinstance(node, ast.Tuple) and _is_issue_tuple(node):
                    self.resolve(module, node, node.elts[1])
                elif isinstance(node, ast.Call):
                    name = _call_name(node)
                    if name == "PydanticCustomError" and node.args:
                        self._sink(module, node, node.args[0])
                        if len(node.args) > 1:
                            self._message_keys(module, node.args[0], node.args[1])
                    elif name == "FieldError":
                        for keyword in node.keywords:
                            if keyword.arg == "code":
                                self._sink(module, node, keyword.value)

    def scan_helpers(self) -> None:
        done: set[_Helper] = set()
        while pending := self.helpers - done:
            for helper in sorted(pending, key=repr):
                done.add(helper)
                private = helper.callee.startswith("_")
                for module in self.modules:
                    if private and module.path != helper.module:
                        continue
                    for node in ast.walk(module.tree):
                        if not (
                            isinstance(node, ast.Call)
                            and _call_name(node) == helper.callee
                        ):
                            continue
                        argument = self._argument(node, helper)
                        if argument is None:
                            continue
                        if self._mapped_argument(module, node, argument):
                            continue
                        self._sink(module, node, argument)

    def _mapped_argument(
        self, module: _Module, call: ast.Call, argument: ast.expr
    ) -> bool:
        """``helper(field, exc.reason, _MESSAGES[exc.reason])``: the code is a
        key of that message map, so every key is a code."""
        before = len(self.result.codes)
        found = False
        for other in [*call.args, *(k.value for k in call.keywords)]:
            if (
                isinstance(other, ast.Subscript)
                and isinstance(other.value, ast.Name)
                and ast.unparse(other.slice) == ast.unparse(argument)
            ):
                found = self._map_keys(module, other.value.id) or found
        return found and len(self.result.codes) >= before

    @staticmethod
    def _argument(call: ast.Call, helper: _Helper) -> ast.expr | None:
        for keyword in call.keywords:
            if keyword.arg == helper.parameter:
                return keyword.value
        index = helper.index
        if index is None or index < 0 or index >= len(call.args):
            return None
        if any(isinstance(a, ast.Starred) for a in call.args[: index + 1]):
            return None
        return call.args[index]


def field_code_scan(root: Path = APP_ROOT) -> FieldCodeScan:
    """The field-error codes in the source under ``root``, found by AST.

    A field error is built as a ``{"path": ..., "code": ..., ...}`` dict
    literal (the :class:`~app.core.errors.FieldError` shape), a
    ``FieldError(code=...)`` call, or a ``PydanticCustomError(code, ...)``. The
    code is collected when it is a string literal, a conditional of literals,
    a local assigned from literals, or a parameter of a helper: then each call
    of that helper is resolved the same way (in its own module for a
    ``_private`` helper). When the message is ``<MAP>[code]``, every key of
    that module-level map is a code too. Anything else (``exc.reason``,
    ``item["code"]``) is a dynamic site; ``tests/unit/test_i18n_inventory.py``
    pins them.
    """
    modules = [_module(path, root.parent) for path in sorted(root.rglob("*.py"))]
    scanner = _FieldScanner(modules)
    scanner.scan_sinks()
    scanner.scan_helpers()
    return scanner.result


def _time_reasons() -> tuple[str, ...]:
    from app.modules.scheduling.errors import TIME_REASONS

    return TIME_REASONS


def _none() -> tuple[str, ...]:
    return ()


DYNAMIC_FIELD_CODE_SITES: Final[Mapping[str, Callable[[], Iterable[str]]]] = {
    # Pydantic's own error types: the web shows its generic validation
    # message for them (``GENERIC_FIELD_CODES``).
    "app/core/errors.py:_validation_errors": _none,
    # A log record that names the request path, not a field error.
    "app/core/ratelimit.py:_take_units": _none,
    # The ``(path, code, message)`` issues of the academics workflows; each
    # literal is collected where the tuple is written (the tuple rule).
    "app/modules/academics/errors.py:__init__": _none,
    # The error class's own Problem code (``problem_codes``).
    "app/modules/administration/errors.py:__init__": _none,
    "app/modules/billing/errors.py:__init__": _none,
    "app/modules/finance/errors.py:__init__": _none,
    "app/modules/payroll/errors.py:__init__": _none,
    # ``<field>_not_applicable`` for the two reschedule-only fields.
    "app/modules/scheduling/change_request_review.py:_not_applicable": lambda: (
        "classroom_not_applicable",
        "override_not_applicable",
    ),
    # ``InvalidSessionTimesError.reason``: one of ``TIME_REASONS``.
    "app/modules/scheduling/conflict_override.py:override_conflict_occurrence": (
        _time_reasons
    ),
    "app/modules/scheduling/occurrence_approval.py:approve": _time_reasons,
    # The create body's errors re-raised without the union tag (#592): the
    # same pydantic and body-validator codes, collected where they are raised.
    "app/modules/scheduling/schemas.py:_untagged_errors": _none,
    # ``search_range_error``'s ``PydanticCustomError`` types, collected there.
    "app/modules/scheduling/slot_search.py:_window": _none,
    # ``rules.RangeIssue`` tuples, collected where they are written.
    "app/workflows/progress_dashboard.py:__init__": _none,
}
"""The field-error sites whose code the scan can't resolve, with the codes
each one can send (pinned: ``tests/unit/test_i18n_inventory.py`` fails on a
new or removed dynamic site)."""


def field_codes() -> list[str]:
    codes = set(field_code_scan().codes)
    for source in DYNAMIC_FIELD_CODE_SITES.values():
        codes.update(source())
    return sorted(codes)


# --- Registries ------------------------------------------------------------------


def notification_types() -> list[str]:
    """The registered notification types once the app is built (#119),
    without the local-only ``dev.`` types and any type a test registered."""
    from app.main import build_app
    from app.modules.administration.notifications import (
        notification_params_model,
        registered_notification_types,
    )

    build_app()
    return sorted(
        code
        for code in registered_notification_types()
        if not code.startswith(_LOCAL_NOTIFICATION_PREFIX)
        and (model := notification_params_model(code)) is not None
        and model.__module__.startswith("app.")
    )


def job_tasks() -> list[str]:
    """The production job app's task names (#128 D3), without ``builtin:``
    and ``demo:`` tasks. The connector is never opened."""
    from procrastinate import PsycopgConnector

    from app.core.jobs import create_job_app

    job_app = create_job_app(PsycopgConnector(), app_env="prod")
    # A builtin task is also registered under an alias: check its own name.
    return sorted(
        name
        for name, task in job_app.tasks.items()
        if not task.name.startswith(_EXCLUDED_TASK_PREFIXES)
    )


def import_row_codes() -> list[str]:
    from app.modules.administration.imports.registry import IMPORT_ROW_CODES

    return sorted(IMPORT_ROW_CODES)


def build_inventory() -> dict[str, list[str]]:
    """Every code the web must label, by area."""
    return {
        "field_codes": field_codes(),
        "import_row_codes": import_row_codes(),
        "job_tasks": job_tasks(),
        "notification_types": notification_types(),
        "problem_codes": problem_codes(),
    }


def render_inventory(inventory: dict[str, list[str]]) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.core.i18n_inventory <output path>", file=sys.stderr)
        return 2
    Path(argv[0]).write_text(render_inventory(build_inventory()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
