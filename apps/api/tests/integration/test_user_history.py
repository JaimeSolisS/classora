"""Archived users keep their history (#25 G; spec §2, §16)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.identity import service as identity
from app.modules.identity import tasks
from app.modules.identity.permissions import ARCHIVED, GUARDIAN, TUTOR
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events, in_uow, signed_in


def test_no_foreign_key_to_users_cascades_or_nulls(seeded_url):
    """Every foreign key that references ``users``, now and in later tables."""
    rows = run_sql(
        seeded_url,
        "SELECT conrelid::regclass::text AS tbl, conname,"
        " confdeltype::text AS on_delete FROM pg_constraint"
        " WHERE contype = 'f' AND confrelid = 'users'::regclass ORDER BY 1, 2",
    )

    # The list is not empty, and includes the tables known today.
    assert {row.tbl for row in rows} >= {
        "auth_sessions",
        "user_roles",
        "user_delegations",
    }
    # c = CASCADE, n = SET NULL, d = SET DEFAULT; r/a (RESTRICT, NO ACTION) only.
    offending = [
        (row.tbl, row.conname) for row in rows if row.on_delete in {"c", "n", "d"}
    ]
    assert offending == []


def test_archiving_then_purging_keeps_the_user_roles_and_audit_events(
    seeded_url, clock
):
    user = create_user(seeded_url, roles=(TUTOR, GUARDIAN))
    with auth_client(seeded_url) as client:
        signed_in(client, user.username)
        signed_in(client, user.username)
    audit_before = {e.id for e in events(seeded_url, record_id=user.id)}
    assert audit_before

    in_uow(
        seeded_url,
        lambda s: identity.change_user_state(s, user_id=user.id, new_state=ARCHIVED),
    )
    clock.advance(timedelta(hours=24))
    deleted = asyncio.run(tasks.run_purge(settings_for(seeded_url)))

    assert deleted == 2
    [row] = run_sql(
        seeded_url, "SELECT state FROM users WHERE id = :id", {"id": user.id}
    )
    assert row.state == ARCHIVED
    roles = run_sql(
        seeded_url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :id",
        {"id": user.id},
    )
    assert {r.code for r in roles} == {TUTOR, GUARDIAN}
    audit_after = {e.id for e in events(seeded_url, record_id=user.id)}
    assert audit_before < audit_after  # nothing lost; state change and revocation added

    with pytest.raises(IntegrityError) as caught:
        run_sql(seeded_url, "DELETE FROM users WHERE id = :id", {"id": user.id})
    assert getattr(caught.value.orig, "sqlstate", "") in {"23001", "23503"}
    assert (
        run_sql(
            seeded_url,
            "SELECT count(*) AS n FROM users WHERE id = :id",
            {"id": user.id},
        )[0].n
        == 1
    )
