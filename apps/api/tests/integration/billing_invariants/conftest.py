"""Fixtures of the billing invariant suite (#103).

One **world template** per pytest session: a database migrated to the
Alembic head, every reference seed applied and the shared world of
:mod:`tests.integration.billing_invariants.world` built in it (families,
guardians, prices, discounts, a package product, groups and a cohort, staff
users). Every randomized sequence, race attempt and job case then gets its
own copy (``CREATE DATABASE … TEMPLATE``, :func:`world_copy`), so each one
starts from the same world with an empty ledger and nothing it does reaches
another test.

The directory is excluded from ``make test-integration`` and run by ``make
test-billing-invariants`` (the ``api-billing-invariants`` CI job).
"""

from collections.abc import Callable, Iterator

import pytest

from tests.integration.billing_invariants import world as worlds


@pytest.fixture(scope="session")
def world_template(database_url: str) -> Iterator[worlds.Template]:
    """The migrated, seeded database holding the shared world (session)."""
    template = worlds.build_template(database_url)
    try:
        yield template
    finally:
        worlds.drop_database(database_url, template.url)


@pytest.fixture
def world_copy(
    database_url: str, world_template: worlds.Template
) -> Iterator[Callable[[], str]]:
    """``make()`` → a new copy of the world template (dropped after the
    test)."""
    made: list[str] = []

    def make() -> str:
        url = worlds.clone_database(database_url, world_template.url)
        made.append(url)
        return url

    try:
        yield make
    finally:
        for url in made:
            worlds.drop_database(database_url, url)
