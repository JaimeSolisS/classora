"""Key parity of the server message catalogs (#98a C5; ADR-0015 E14).

``catalog_problems`` checks a ``locales`` directory:

- es and en have the same namespaces and the same keys;
- each key has the same placeholder set in both languages;
- every leaf is a non-empty string;
- no raw JSON object repeats a key (the #242 precedent);
- every ``{...}`` is a well-formed placeholder (``i18n.PLACEHOLDER_NAME``).

The real catalogs must have no problem; each broken fixture under
``i18n_fixtures/`` must fail exactly its own check.
"""

import re
from pathlib import Path

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
