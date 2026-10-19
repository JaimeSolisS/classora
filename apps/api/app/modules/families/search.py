"""The name search of the families lists (ISSUE-D6, accepted on #33; #34 C5
uses the same predicate for guardians).

With ``n(x) = public.f_unaccent(lower(x))`` and ``name = given_names || ' ' ||
family_names``, a row matches ``q`` when every whitespace-separated term of
``q`` (at most five) is a substring of ``n(name)`` (``%``, ``_`` and ``\\``
match literally, so terms match in any order), **or** ``n(q) <% n(name)``
(``pg_trgm`` word similarity at its default threshold, which tolerates
typos). Results are ordered by ``word_similarity(n(q), n(name))``, then the
default order ``n(family_names)``, ``n(given_names)``, ``id``. Both predicates
use the table's GIN trigram expression index on ``n(name)``.
"""

from typing import Any

from sqlalchemy import (
    ColumnElement,
    Float,
    String,
    and_,
    func,
    literal,
    literal_column,
    or_,
)

from app.modules.families.schemas import search_terms


def normalized(expression: Any) -> ColumnElement[str]:
    """``public.f_unaccent(lower(expression))``."""
    return func.public.f_unaccent(func.lower(expression), type_=String)


def search_name(given_names: Any, family_names: Any) -> ColumnElement[str]:
    """``n(given_names || ' ' || family_names)``: exactly the indexed
    expression (the separator is an SQL literal, not a bound parameter, so the
    planner can match the GIN index)."""
    full_name = given_names.op("||")(literal_column("' '")).op("||")(family_names)
    return normalized(full_name)


def escape_like(term: str) -> str:
    """``term`` for a ``LIKE`` pattern with PostgreSQL's default escape
    character (``\\``): ``%``, ``_`` and ``\\`` match literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def name_search_condition(
    q: str, given_names: Any, family_names: Any
) -> ColumnElement[bool]:
    """The predicate of ``q`` (already trimmed, at most five terms)."""
    name = search_name(given_names, family_names)
    every_term = and_(
        *(
            name.like(
                literal("%") + normalized(literal(escape_like(term))) + literal("%")
            )
            for term in search_terms(q)
        )
    )
    similar = normalized(literal(q)).op("<%", is_comparison=True)(name)
    return or_(every_term, similar)


def relevance(q: str, given_names: Any, family_names: Any) -> ColumnElement[float]:
    """``word_similarity(n(q), n(name))``, the first sort key of a search."""
    return func.word_similarity(
        normalized(literal(q)), search_name(given_names, family_names), type_=Float
    )


def default_order(given_names: Any, family_names: Any, row_id: Any) -> tuple[Any, ...]:
    """``n(family_names)``, ``n(given_names)``, ``id``."""
    return (normalized(family_names), normalized(given_names), row_id)
