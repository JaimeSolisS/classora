"""Helpers for the price catalog tests (#89)."""

from collections.abc import Iterator
from contextlib import contextmanager

from tests.integration.conftest import run_sql

PREDICATE = (
    "CREATE OR REPLACE FUNCTION price_version_is_referenced(version_id uuid)"
    " RETURNS boolean LANGUAGE sql VOLATILE AS $$ {body} $$"
)


@contextmanager
def every_version_referenced(url: str) -> Iterator[None]:
    """The guard's predicate answers ``true`` for every version (committed),
    as if every version were used by a charge (#89 E2, E6); the original
    body comes back afterwards, also when the block fails."""
    [original] = run_sql(
        url, "SELECT prosrc FROM pg_proc WHERE proname = 'price_version_is_referenced'"
    )
    run_sql(url, PREDICATE.format(body="SELECT true"))
    try:
        yield
    finally:
        run_sql(url, PREDICATE.format(body=original.prosrc))
