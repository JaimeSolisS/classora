"""The registry of migrated databases shared by several tests (#27 G5).

Kept out of ``conftest.py``: pytest may import a conftest module under a
second name, which would give it a second, empty registry. Every importer of
this module sees the same set.
"""

SHARED_DATABASES: set[str] = set()
"""URLs of migrated databases that outlive one test."""


def share_database(url: str) -> None:
    """Register a migrated database used by several tests, so the autouse
    ``empty_rate_limit_counters`` fixture empties its counters before each
    test."""
    SHARED_DATABASES.add(url)


def unshare_database(url: str) -> None:
    SHARED_DATABASES.discard(url)
