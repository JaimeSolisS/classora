"""Scrub a simulated database before it leaves this machine (#152, M10-04).

Run by ``prepare-dataset.sh`` from ``apps/api`` with ``uv run``:

    PYTHONPATH=. uv run python ../../infrastructure/load/scrub.py \
        <owner database URL> <accounts file>

As the database owner on the given (local, never ``classora``) database:

1. every user that can sign in gets a new random password, hashed with the
   app's own hasher (``app.modules.identity.passwords``). The usernames, role
   codes and passwords go ONLY to ``<accounts file>`` (gitignored, mode 0600);
2. ``auth_sessions``, ``account_tokens``, ``idempotency_keys`` and
   ``rate_limit_counters`` are emptied;
3. every user that can sign in accepts the current privacy notice;
4. the README's documented ``classora-sim-<role>`` passwords are checked
   against every stored hash and must all fail.

It prints counts only, never a password or hash. Exit 0 when every check
passed, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from app.modules.identity.passwords import hash_password, verify_password

README_PASSWORDS = tuple(
    f"classora-sim-{role}"
    for role in (
        "owner",
        "admin",
        "coordinator",
        "finance",
        "tutor",
        "guardian",
        "student",
    )
)
EMPTIED = ("auth_sessions", "account_tokens", "idempotency_keys", "rate_limit_counters")


async def scrub(url: str, accounts_path: Path) -> int:
    target = urlparse(url)
    database = target.path.lstrip("/")
    if database == "classora" or target.port in (5432, 6432):
        print(f"scrub: refusing {target.hostname}:{target.port}/{database}", file=sys.stderr)
        return 1
    print(f"scrub: target {target.hostname}:{target.port}/{database}")
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT u.id, u.username, array_agg(r.code ORDER BY r.code) AS roles
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.password_hash IS NOT NULL AND u.state = 'active'
                GROUP BY u.id, u.username
                ORDER BY u.username
                """
            )
            accounts = []
            for row in rows:
                password = secrets.token_urlsafe(24)
                await conn.execute(
                    "UPDATE users SET password_hash = $1 WHERE id = $2",
                    hash_password(password),
                    row["id"],
                )
                accounts.append(
                    {
                        "username": row["username"],
                        "roles": list(row["roles"]),
                        "password": password,
                    }
                )
            # Any other user (pending, suspended, archived) keeps no usable hash.
            cleared = await conn.execute(
                "UPDATE users SET password_hash = NULL"
                " WHERE password_hash IS NOT NULL AND state <> 'active'"
            )
            for table in EMPTIED:
                await conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names
            accepted = await conn.execute(
                """
                INSERT INTO privacy_notice_acceptances (user_id, notice_version_id, accepted_at)
                SELECT u.id, v.id, now()
                FROM users u
                CROSS JOIN (
                    SELECT id FROM privacy_notice_versions
                    WHERE published_at <= now()
                    ORDER BY version DESC LIMIT 1
                ) v
                WHERE u.password_hash IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )
        hashes = [
            r["password_hash"]
            for r in await conn.fetch(
                "SELECT password_hash FROM users WHERE password_hash IS NOT NULL"
            )
        ]
        leftovers = {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in EMPTIED}  # noqa: S608
    finally:
        await conn.close()

    readme_hits = sum(
        1 for h in hashes for password in README_PASSWORDS if verify_password(h, password)
    )
    accounts_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(accounts_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"database": database, "accounts": accounts}, handle, indent=1)

    by_role: dict[str, int] = {}
    for account in accounts:
        for role in account["roles"]:
            by_role[role] = by_role.get(role, 0) + 1
    print(f"scrub: {len(accounts)} sign-in accounts re-passworded, by role {by_role}")
    print(f"scrub: non-active users cleared: {cleared}; privacy acceptances added: {accepted}")
    print(f"scrub: rows left in {leftovers}")
    print(f"scrub: README passwords matching any hash: {readme_hits} (must be 0)")
    print(f"scrub: accounts written to {accounts_path} (mode 0600, gitignored)")
    ok = readme_hits == 0 and not any(leftovers.values()) and len(accounts) > 0
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: scrub.py <owner database URL> <accounts file>", file=sys.stderr)
        return 2
    return asyncio.run(scrub(sys.argv[1], Path(sys.argv[2]).resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
