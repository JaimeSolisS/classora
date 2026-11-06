"""Key parity of the server message catalogs (#98a C5; ADR-0015 E14).

``catalog_problems`` checks a ``locales`` directory:

- es and en have the same namespaces and the same keys;
- each key has the same placeholder set in both languages;
- every leaf is a non-empty string;
- no raw JSON object repeats a key (the #242 precedent);
- every ``{...}`` is a well-formed placeholder (``i18n.PLACEHOLDER_NAME``).

The real catalogs must have no problem; each broken fixture under
``i18n_fixtures/`` must fail exactly its own check.

#130 B4: every key the server asks for (a literal in a template or a
``t``/``message`` call, or each key a pinned dynamic site can build) exists in
both languages.
"""

import ast
import re
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from app.core import i18n

FIXTURES = Path(__file__).resolve().parent / "i18n_fixtures"
BRACES = re.compile(r"[{}]")


def _leaves(node: object, prefix: str, problems: list[str]) -> dict[str, str]:
    leaves: dict[str, str] = {}
    if not isinstance(node, dict):
        problems.append(f"{prefix}: not an object or string")
        return leaves
    if not node:
        problems.append(f"{prefix}: empty object")
    for key, value in node.items():
        path = f"{prefix}.{key}"
        if isinstance(value, dict):
            leaves |= _leaves(value, path, problems)
        elif isinstance(value, str):
            if not value.strip():
                problems.append(f"{path}: empty string")
            leaves[path] = value
        else:
            problems.append(f"{path}: non-string leaf {type(value).__name__}")
    return leaves


def _placeholders(text: str, where: str, problems: list[str]) -> frozenset[str]:
    if BRACES.search(i18n.PLACEHOLDER.sub("", text)):
        problems.append(f"{where}: malformed placeholder in {text!r}")
    return frozenset(i18n.PLACEHOLDER.findall(text))


def catalog_problems(root: Path) -> list[str]:
    """Every parity or format problem of the catalogs under ``root``."""
    problems: list[str] = []
    languages = sorted(p.name for p in root.iterdir() if p.is_dir())
    if languages != sorted(i18n.LANGUAGES):
        problems.append(f"languages {languages} != {sorted(i18n.LANGUAGES)}")
        return problems
    keys: dict[str, dict[str, str]] = {}
    namespaces: dict[str, set[str]] = {}
    for lang in i18n.LANGUAGES:
        namespaces[lang] = {p.stem for p in (root / lang).glob("*.json")}
        keys[lang] = {}
        for namespace in sorted(namespaces[lang]):
            source = root / lang / f"{namespace}.json"
            try:
                data = i18n.read_catalog(source)
            except ValueError as exc:
                problems.append(f"{lang}/{namespace}.json: {exc}")
                continue
            keys[lang] |= _leaves(data, f"{lang}:{namespace}", problems)
    es, en = i18n.LANGUAGES
    if namespaces[es] != namespaces[en]:
        problems.append(f"namespaces differ: {sorted(namespaces[es] ^ namespaces[en])}")
    es_keys = {k.split(":", 1)[1]: v for k, v in keys[es].items()}
    en_keys = {k.split(":", 1)[1]: v for k, v in keys[en].items()}
    for key in sorted(es_keys.keys() ^ en_keys.keys()):
        problems.append(f"{key}: only in {es if key in es_keys else en}")
    for key in sorted(es_keys.keys() & en_keys.keys()):
        es_names = _placeholders(es_keys[key], f"{es}:{key}", problems)
        en_names = _placeholders(en_keys[key], f"{en}:{key}", problems)
        if es_names != en_names:
            problems.append(
                f"{key}: placeholders differ ({sorted(es_names)} vs {sorted(en_names)})"
            )
    return problems


def test_the_server_catalogs_have_parity():
    root = Path(str(i18n.catalog_root()))
    assert catalog_problems(root) == []
    assert {p.stem for p in (root / "es").glob("*.json")} >= {"common"}


def test_the_valid_fixture_has_no_problem():
    assert catalog_problems(FIXTURES / "valid") == []


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing_namespace", "namespaces differ"),
        ("missing_key", "common.document.title: only in es"),
        ("placeholder_mismatch", "common.document.page: placeholders differ"),
        ("empty_string", "en:common.document.title: empty string"),
        ("non_string_leaf", "en:common.document.copies: non-string leaf int"),
        ("duplicate_key", "es/common.json: duplicate catalog key 'title'"),
        ("bad_placeholder", "es:common.document.page: malformed placeholder"),
    ],
)
def test_each_broken_fixture_fails_its_check(case, expected):
    problems = catalog_problems(FIXTURES / case)
    assert any(problem.startswith(expected) for problem in problems), problems


def test_read_catalog_refuses_a_duplicate_key_at_any_depth(tmp_path):
    source = tmp_path / "common.json"
    source.write_text('{"a": {"b": "x", "b": "y"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate catalog key 'b'"):
        i18n.read_catalog(source)


# --- B4: the keys the server uses exist (#130) ---------------------------------------

APP_ROOT = Path(i18n.__file__).resolve().parents[1]
TEMPLATE_KEY = re.compile(r"""\bt\(\s*(?:"([^"]*)"\s*[,)]|'([^']*)'\s*[,)])?""")
I18N_FUNCTIONS = frozenset({"t", "message"})


def _i18n_names(tree: ast.Module, path: Path) -> set[str]:
    """The names a module calls ``app.core.i18n``'s ``t``/``message`` by."""
    if path == Path(i18n.__file__).resolve():
        return set(I18N_FUNCTIONS)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.i18n":
            names |= {
                a.asname or a.name for a in node.names if a.name in I18N_FUNCTIONS
            }
    return names


def _enclosing_function(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return "<module>"


def _constant_strings(tree: ast.AST) -> dict[str, str]:
    """``NAME = "text"`` assignments directly in ``tree``'s body (module
    constants such as ``PREFIX``, or a function's ``prefix``)."""
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _literal_key(expr: ast.expr, constants: dict[str, str]) -> str | None:
    """The key when it is a literal, or an f-string of literals and string
    constants (``f"{PREFIX}.rules.none"``); else ``None`` (dynamic)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        parts: list[str] = []
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif (
                isinstance(value, ast.FormattedValue)
                and isinstance(value.value, ast.Name)
                and value.value.id in constants
            ):
                parts.append(constants[value.value.id])
            else:
                return None
        return "".join(parts)
    return None


def server_key_usage(root: Path = APP_ROOT) -> tuple[set[str], set[str]]:
    """``(literal keys, dynamic sites)`` of every ``t``/``message`` call in
    the Python under ``root`` and every ``t(...)`` in its templates. A site
    is ``<path from app>:<function>``."""
    keys: set[str] = set()
    dynamic: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _i18n_names(tree, path.resolve())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        module_constants = _constant_strings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            direct = isinstance(func, ast.Name) and func.id in names
            qualified = (
                isinstance(func, ast.Attribute)
                and func.attr in I18N_FUNCTIONS
                and isinstance(func.value, ast.Name)
                and func.value.id == "i18n"
            )
            if not (direct or qualified):
                continue
            constants = dict(module_constants)
            owner: ast.AST = node
            while owner in parents:
                owner = parents[owner]
                if isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef):
                    constants |= _constant_strings(owner)
                    break
            argument = node.args[1]
            if (
                isinstance(owner, ast.FunctionDef)
                and isinstance(argument, ast.Name)
                and argument.id in {a.arg for a in owner.args.args}
            ):
                continue  # a wrapper passing its own key parameter on (``i18n.t``)
            key = _literal_key(argument, constants)
            if key is None:
                site = path.relative_to(root.parent).as_posix()
                dynamic.add(f"{site}:{_enclosing_function(parents, node)}")
            else:
                keys.add(key)
    for path in sorted((root / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in TEMPLATE_KEY.finditer(text):
            key = match.group(1) or match.group(2)
            if key:
                keys.add(key)
            else:
                dynamic.add(f"{path.relative_to(root.parent).as_posix()}:<template>")
    return keys, dynamic


def _payslip_class_keys() -> set[str]:
    from app.modules.payroll.models import CLASS_KINDS, RATE_MODELS, SESSION_LINE_KINDS

    return (
        {f"payslips.v1.kinds.{kind}" for kind in SESSION_LINE_KINDS}
        | {f"payslips.v1.models.{model}" for model in RATE_MODELS}
        | {f"payslips.v1.class_kinds.{kind}" for kind in CLASS_KINDS}
    )


def _payslip_salary_keys() -> set[str]:
    from app.modules.payroll.models import PRORATION_BASES

    return {f"payslips.v1.bases.{basis}" for basis in PRORATION_BASES}


def _payslip_adjustment_keys() -> set[str]:
    from app.modules.payroll.models import ADJUSTMENT_KINDS, CLASS_KINDS

    return {f"payslips.v1.adjustment_kinds.{kind}" for kind in ADJUSTMENT_KINDS} | {
        f"payslips.v1.class_kinds.{kind}" for kind in CLASS_KINDS
    }


def _payslip_context_keys() -> set[str]:
    from app.modules.payroll.models import PAY_FREQUENCIES

    return {f"payslips.v1.frequencies.{frequency}" for frequency in PAY_FREQUENCIES}


def _receipt_allocation_keys() -> set[str]:
    # The match arm ``case "group_enrollment" | "cohort_enrollment"``.
    return {f"receipts.v1.kinds.{k}" for k in ("group_enrollment", "cohort_enrollment")}


def _receipt_method_keys() -> set[str]:
    # The receipt v1 snapshot's method: the methods seeded by migration 0061.
    return {f"receipts.v1.methods.{code}" for code in ("cash", "transfer", "other")}


def _export_header_keys() -> set[str]:
    from app.modules.reporting.exports.datasets import BUILT_IN_DATASETS

    return {
        f"exports.columns.{dataset.code}.{column.code}"
        for dataset in BUILT_IN_DATASETS
        for column in dataset.columns
    }


def _export_dataset_keys() -> set[str]:
    from app.modules.reporting.exports.datasets import BUILT_IN_DATASETS

    return {f"exports.datasets.{dataset.code}" for dataset in BUILT_IN_DATASETS}


def _export_boolean_keys() -> set[str]:
    return {"exports.values.boolean.true", "exports.values.boolean.false"}


def _export_enum_keys() -> set[str]:
    from app.modules.finance.schemas import ExpenseStatus
    from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode

    groups = {
        "session_kind": get_args(SessionKindCode.__value__),
        "session_state": get_args(SessionStateCode.__value__),
        "expense_status": get_args(ExpenseStatus.__value__),
    }
    return {
        f"exports.values.{group}.{value}"
        for group, values in groups.items()
        for value in values
    }


def _system_category_keys() -> set[str]:
    from app.modules.finance.models import SYSTEM_KEYS

    return {f"finance.categories.system.{key}" for key in SYSTEM_KEYS}


def _month_keys() -> set[str]:
    return {f"common.months.{month}" for month in range(1, 13)}


def _payslip_item_section_keys() -> set[str]:
    # ``{% for key in ("bonuses", "deductions") %}`` in the payslip template.
    return {f"payslips.v1.sections.{key}" for key in ("bonuses", "deductions")}


DYNAMIC_KEY_SITES: dict[str, Callable[[], set[str]]] = {
    "app/core/i18n.py:format_date": _month_keys,
    "app/templates/payslips/v1/payslip.html:<template>": _payslip_item_section_keys,
    "app/modules/payroll/payslips.py:_class_row": _payslip_class_keys,
    "app/modules/payroll/payslips.py:_salary_row": _payslip_salary_keys,
    "app/modules/payroll/payslips.py:_adjustment_row": _payslip_adjustment_keys,
    "app/modules/payroll/payslips.py:payslip_context": _payslip_context_keys,
    "app/modules/billing/receipts.py:allocation_label": _receipt_allocation_keys,
    "app/modules/billing/receipts.py:receipt_context": _receipt_method_keys,
    "app/modules/reporting/exports/render.py:header_label": _export_header_keys,
    "app/modules/reporting/exports/render.py:dataset_label": _export_dataset_keys,
    "app/modules/reporting/exports/render.py:_boolean": _export_boolean_keys,
    "app/modules/reporting/exports/render.py:_enum": _export_enum_keys,
    "app/modules/finance/categories.py:system_label": _system_category_keys,
    "app/modules/reporting/exports/datasets.py:_category_name": _system_category_keys,
}
"""Every ``t``/``message`` call whose key isn't a literal, with the keys it
can ask for (expanded from the enum or constant its values come from)."""


def _missing(keys: set[str]) -> list[str]:
    missing: list[str] = []
    for key in sorted(keys):
        for lang in i18n.LANGUAGES:
            try:
                i18n.message(lang, key)
            except KeyError:
                missing.append(f"{lang}: {key}")
    return missing


def test_every_literal_key_the_server_uses_exists_in_both_languages():
    keys, _ = server_key_usage()
    assert len(keys) > 50  # the scan found the templates and the modules
    assert {"common.smoke.title", "receipts.v1.title", "payslips.v1.rules.none"} <= keys
    assert _missing(keys) == []


def test_every_dynamic_key_site_is_pinned():
    _, dynamic = server_key_usage()
    assert dynamic == set(DYNAMIC_KEY_SITES), (
        "A t()/message() call with a computed key: add its site to "
        "DYNAMIC_KEY_SITES with the keys it can ask for."
    )


@pytest.mark.parametrize("site", sorted(DYNAMIC_KEY_SITES))
def test_every_key_of_a_dynamic_site_exists_in_both_languages(site):
    keys = DYNAMIC_KEY_SITES[site]()
    assert keys
    assert _missing(keys) == []


def test_the_key_scan_finds_literal_and_dynamic_calls(tmp_path):
    root = tmp_path / "app"
    (root / "templates" / "x").mkdir(parents=True)
    (root / "mod.py").write_text(
        "from app.core.i18n import message\n"
        "from app.core import i18n\n"
        'PREFIX = "ns.a"\n'
        "def literal(lang):\n"
        '    return i18n.t(lang, "ns.literal"), message(lang, f"{PREFIX}.b")\n'
        "def dynamic(lang, kind):\n"
        '    return i18n.t(lang, f"ns.kinds.{kind}")\n'
        "def unrelated(error):\n"
        '    return error.message(1, "not.a.key")\n',
        encoding="utf-8",
    )
    (root / "templates" / "x" / "page.html").write_text(
        '{{ t("ns.template") }} {{ t(name) }}', encoding="utf-8"
    )
    keys, dynamic = server_key_usage(root)
    assert keys == {"ns.literal", "ns.a.b", "ns.template"}
    assert dynamic == {"app/mod.py:dynamic", "app/templates/x/page.html:<template>"}
