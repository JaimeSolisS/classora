"""``auth_sessions`` constraints with raw SQL (#23 A4)."""

import hashlib

import pytest
from sqlalchemy.exc import IntegrityError

from tests.integration.auth_support import create_user
from tests.integration.conftest import run_sql

INSERT = (
    "INSERT INTO auth_sessions (user_id, token_hash) VALUES (:user_id, :token_hash)"
)


def sqlstate(error: IntegrityError) -> str:
    return str(getattr(error.orig, "sqlstate", ""))


def test_a_session_row_with_defaults(seeded_url):
    user = create_user(seeded_url)

    run_sql(
        seeded_url,
        INSERT,
        {"user_id": user.id, "token_hash": hashlib.sha256(b"a").digest()},
    )

    [row] = run_sql(
        seeded_url,
        "SELECT octet_length(token_hash) AS n, last_seen_at IS NOT NULL AS seen,"
        " revoked_at FROM auth_sessions",
    )
    assert (row.n, row.seen, row.revoked_at) == (32, True, None)


def test_a_duplicate_token_hash_fails(seeded_url):
    user = create_user(seeded_url)
    token_hash = hashlib.sha256(b"same").digest()
    run_sql(seeded_url, INSERT, {"user_id": user.id, "token_hash": token_hash})

    with pytest.raises(IntegrityError) as caught:
        run_sql(seeded_url, INSERT, {"user_id": user.id, "token_hash": token_hash})

    assert sqlstate(caught.value) == "23505"  # unique_violation
    assert "uq_auth_sessions_token_hash" in str(caught.value.orig)


@pytest.mark.parametrize("length", [0, 31, 33, 64])
def test_a_token_hash_of_the_wrong_length_fails_the_check(seeded_url, length):
    user = create_user(seeded_url)

    with pytest.raises(IntegrityError) as caught:
        run_sql(seeded_url, INSERT, {"user_id": user.id, "token_hash": b"x" * length})

    assert sqlstate(caught.value) == "23514"  # check_violation
    assert "ck_auth_sessions_token_hash_length" in str(caught.value.orig)


def test_deleting_a_user_who_has_a_session_fails(seeded_url):
    user = create_user(seeded_url, roles=())
    run_sql(
        seeded_url,
        INSERT,
        {"user_id": user.id, "token_hash": hashlib.sha256(b"u").digest()},
    )

    with pytest.raises(IntegrityError) as caught:
        run_sql(seeded_url, "DELETE FROM users WHERE id = :id", {"id": user.id})

    assert sqlstate(caught.value) == "23001"  # restrict_violation
    assert "fk_auth_sessions_user_id_users" in str(caught.value.orig)
    assert run_sql(seeded_url, "SELECT count(*) FROM users")[0][0] == 1


def test_a_session_for_an_unknown_user_fails(seeded_url):
    with pytest.raises(IntegrityError) as caught:
        run_sql(
            seeded_url,
            INSERT,
            {
                "user_id": "00000000-0000-7000-8000-000000000001",
                "token_hash": hashlib.sha256(b"x").digest(),
            },
        )

    assert sqlstate(caught.value) == "23503"  # foreign_key_violation
