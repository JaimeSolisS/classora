"""Registration never links or enrolls anyone (#169 C4; spec §9, ADR-0007 D13).

Counts the rows of every table in the ``public`` schema (from ``pg_tables``,
so tables added by later issues are covered automatically) before and after
a submit, an approval and a rejection. Only the tables below may change.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.consent_support import adult_profile_for
from tests.integration.lifecycle_support import code_of, signed_in
from tests.integration.registration_support import (
    approve,
    register,
    reject,
    request_row,
    unique_username,
)

MAY_CHANGE = frozenset(
    {
        "users",
        "user_roles",
        "registration_requests",
        "audit_events",
        "rate_limit_counters",
    }
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def row_counts(url: str) -> dict[str, int]:
    tables = [
        row.tablename
        for row in run_sql(
            url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    ]
    return {
        table: int(run_sql(url, f'SELECT count(*) FROM "{table}"')[0][0])  # noqa: S608
        for table in tables
    }


def changed(before: dict[str, int], after: dict[str, int]) -> set[str]:
    assert set(before) == set(after)
    return {table for table in before if before[table] != after[table]}


def test_the_table_list_comes_from_the_catalog(module_seeded_url):
    counts = row_counts(module_seeded_url)

    # Guards against a check that silently counts nothing.
    assert MAY_CHANGE <= set(counts)
    assert {"roles", "permissions", "auth_sessions", "account_tokens"} <= set(counts)


@pytest.mark.parametrize("role", ["guardian", "student"])
def test_submit_approve_and_reject_change_only_the_allowed_tables(
    module_seeded_url, client, clock, role
):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    # Signed in before counting: the login's own session row is not part of
    # what registration does.
    cookie = signed_in(client, owner.username)
    approved, rejected = unique_username(), unique_username()

    before = row_counts(module_seeded_url)
    assert code_of(register(client, username=approved, role=role)) == (202, None)
    assert code_of(register(client, username=rejected, role=role)) == (202, None)
    submitted = row_counts(module_seeded_url)
    assert changed(before, submitted) <= MAY_CHANGE
    assert submitted["users"] == before["users"] + 2
    assert submitted["registration_requests"] == before["registration_requests"] + 2
    assert submitted["user_roles"] == before["user_roles"]

    approved_row = request_row(module_seeded_url, approved)
    if role == "student":
        # A Student approval needs a linked profile (#35 H5): an adult's,
        # created outside the counted steps.
        adult_profile_for(module_seeded_url, approved_row.user_id)
        submitted = row_counts(module_seeded_url)
    assert code_of(approve(client, cookie, approved_row.id)) == (200, None)
    after_approval = row_counts(module_seeded_url)
    assert changed(submitted, after_approval) <= MAY_CHANGE
    assert after_approval["user_roles"] == submitted["user_roles"] + 1

    rejected_id = request_row(module_seeded_url, rejected).id
    assert code_of(reject(client, cookie, rejected_id)) == (200, None)
    after_rejection = row_counts(module_seeded_url)
    assert changed(after_approval, after_rejection) <= MAY_CHANGE
    assert after_rejection["user_roles"] == after_approval["user_roles"]
