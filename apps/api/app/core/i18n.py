"""Server message catalogs and formatting without Babel (ADR-0015 E14, #98a).

Server-rendered text (PDF documents: receipts #98, payslips M7, exports M8-07)
comes from JSON catalogs, one file per language and namespace:
``app/i18n/locales/{es,en}/<namespace>.json``, nested objects of strings (the
web catalog format). The rules (#98a C1-C5):

- **Keys** are ``"<namespace>.<dotted.path>"``, e.g. ``common.document.page``.
  Catalogs are read once through :mod:`importlib.resources` (so they work from
  the image) and cached.
- **No silent fallback.** An unknown language is a :class:`ValueError`; a
  missing key, or a key that names an object instead of a string, is a
  :class:`KeyError` naming the language and the key. Never the other
  language, never the key itself.
- **Placeholders** are named ``{name}`` (:data:`PLACEHOLDER_NAME`) and are
  substituted by a regex, never ``str.format``, so a template can't reach
  attributes or indexes. A missing parameter is a :class:`KeyError`, an unused
  extra one a :class:`ValueError`.
- **Values are returned unescaped.** HTML escaping is Jinja2's autoescape
  (:mod:`app.core.pdf`), never this module's.
- **Parity.** ``es`` and ``en`` have the same namespaces, keys and
  placeholders (``tests/unit/test_i18n_catalogs.py``). A catalog file with a
  duplicate key is refused when read.
- **Formatting** needs no locale: :func:`format_money` writes ``$1,250.00`` in
  both languages (the web's ``Intl.NumberFormat('es-MX')``); :func:`format_date`
  uses ``common.date.long`` and ``common.months.*``.

This module is shared infrastructure (ADR-0002): it imports no domain module.
"""

import json
import re
from collections.abc import Mapping
from datetime import date
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final, Literal, cast

from app.core.money import Money

type Language = Literal["es", "en"]

LANGUAGES: Final[tuple[Language, ...]] = ("es", "en")
"""The document languages; equal to ``identity.preferences.LANGUAGES``."""

DEFAULT_LANGUAGE: Final[Language] = "es"
"""The center default (ADR-0015 document language; the web ``DEFAULT_LANGUAGE``)."""

PLACEHOLDER_NAME: Final = r"[a-z_][a-z0-9_]*"
"""The name of a ``{placeholder}`` in a catalog message."""

PLACEHOLDER: Final = re.compile(r"\{(" + PLACEHOLDER_NAME + r")\}", re.ASCII)
"""A well-formed ``{name}`` placeholder; group 1 is the name."""

_NAMESPACE: Final = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)

type CatalogNode = str | dict[str, CatalogNode]


def catalog_root() -> Traversable:
    """The ``locales`` directory of the :mod:`app.i18n` package."""
    return resources.files("app.i18n").joinpath("locales")


def _refuse_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate catalog key {key!r}")
        result[key] = value
    return result


def read_catalog(source: Traversable) -> dict[str, object]:
    """Parse one catalog file; a duplicate key (at any depth) is a
    :class:`ValueError`. The structure is not validated here (the parity test
    checks every leaf)."""
    data = json.loads(
        source.read_text(encoding="utf-8"), object_pairs_hook=_refuse_duplicates
    )
    if not isinstance(data, dict):
        raise ValueError(f"catalog {source.name!r} is not a JSON object")
    return cast(dict[str, object], data)


def _check_language(lang: str) -> Language:
    if lang not in LANGUAGES:
        raise ValueError(f"unknown language {lang!r}")
    return lang


@cache
def _namespace(lang: Language, namespace: str) -> Mapping[str, object] | None:
    source = catalog_root().joinpath(lang, f"{namespace}.json")
    if not source.is_file():
        return None
    return read_catalog(source)


def message(lang: str, key: str) -> str:
    """The raw catalog text of ``key`` in ``lang``, placeholders unsubstituted."""
    language = _check_language(lang)
    namespace, _, path = key.partition(".")
    missing = KeyError(f"no message {key!r} in language {language!r}")
    if not _NAMESPACE.fullmatch(namespace) or not path:
        raise missing
    node: object = _namespace(language, namespace)
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise missing
        node = node[part]
    if not isinstance(node, str):
        raise missing
    return node


def t(lang: str, key: str, **params: object) -> str:
    """The message ``key`` in ``lang`` with its ``{name}`` placeholders
    replaced by ``str(params[name])``. Unescaped (see the module rules)."""
    text = message(lang, key)
    names = set(PLACEHOLDER.findall(text))
    missing = names - params.keys()
    if missing:
        raise KeyError(f"message {key!r} needs the parameter(s) {sorted(missing)}")
    extra = params.keys() - names
    if extra:
        raise ValueError(f"message {key!r} takes no parameter(s) {sorted(extra)}")
    return PLACEHOLDER.sub(lambda match: str(params[match[1]]), text)


def resolve_language(stored: str | None) -> Language:
    """A stored language preference as a document language: ``es`` or ``en``;
    ``None`` or anything else is the center default ``es`` (ADR-0015)."""
    if stored in LANGUAGES:
        return stored
    return DEFAULT_LANGUAGE


def format_money(value: Money) -> str:
    """``$1,250.00``, ``$0.00``, ``-$1,250.00``: the same in es and en, comma
    thousands and a point for decimals, from :attr:`Money.amount` (#87)."""
    if not isinstance(value, Money):
        raise TypeError("format_money takes a Money")
    amount = value.amount
    sign = "-" if value.is_negative() else ""
    return f"{sign}${abs(amount):,.2f}"


def format_date(lang: str, value: date) -> str:
    """A long date from the catalogs: ``28 de octubre de 2026`` (es),
    ``October 28, 2026`` (en)."""
    if not isinstance(value, date):
        raise TypeError("format_date takes a date")
    return t(
        lang,
        "common.date.long",
        day=value.day,
        month=t(lang, f"common.months.{value.month}"),
        year=value.year,
    )
