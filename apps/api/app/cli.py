"""``python -m app.cli <command>``: operator commands run from the host shell.

Commands (one subparser each; #30 adds ``reset-owner-password``):

``bootstrap-owner --username <name> [--display-name <text>] [--password-stdin]``
    Creates the first Owner account (ADR-0007, D12). ``make bootstrap-owner
    USERNAME=<name>`` wraps it.

    1. Load settings (a configuration error prints the M1-05 message, exit 1).
    2. Connect **directly** to PostgreSQL (``DATABASE_DIRECT_URL``, ADR-0003),
       like ``make seed``; refuse unless the database is at the Alembic head,
       the ``owner`` role is seeded and no user holds it (in any state).
    3. Normalize the username; ``--display-name`` defaults to it.
    4. Read the password: an interactive prompt, twice (default), or the first
       line of stdin (``--password-stdin``). Never from argv or the
       environment. Apply the password policy.
    5. In one Unit of Work, under a transaction-scoped advisory lock, check
       again that no Owner exists, then create the user (``active``, Argon2id
       hash) with the ``owner`` role.
    6. Print one line naming the username; log ``owner_bootstrapped`` (user id
       and username). The password and its hash are never printed or logged.

    Runs in every ``APP_ENV``, including ``prod`` (go-live needs it). Exit 0 on
    success, 1 on any refusal. The audit event for this command arrives with
    the audit infrastructure (#22).
"""

import argparse
import asyncio
import getpass
import logging
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import TextIO

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import ConfigurationError, Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.logging import configure_logging
from app.core.uow import UnitOfWork
from app.modules.identity.models import Role, User, UserRole
from app.modules.identity.permissions import ACTIVE, OWNER
from app.modules.identity.service import (
    PasswordPolicyError,
    UsernameInvalidError,
    hash_password,
    normalize_username,
    validate_password,
)
from app.seeds.runner import (
    DatabaseNotAtHeadError,
    EngineFactory,
    create_seed_engine,
    ensure_at_head,
)

logger = logging.getLogger("app.cli")

# Key of the transaction-scoped advisory lock (pg_advisory_xact_lock) that
# serializes concurrent bootstrap-owner runs. Any fixed bigint works; no other
# code uses this one. (The ASCII bytes of "CLASOWNR".)
BOOTSTRAP_OWNER_LOCK_KEY = 0x434C41534F574E52

ROLES_NOT_SEEDED_MESSAGE = (
    "The owner role does not exist: run `make seed` first (after `make migrate`)."
)
OWNER_EXISTS_MESSAGE = "An Owner account already exists."
MISMATCH_MESSAGE = "The passwords do not match."
DISPLAY_NAME_MAX_LENGTH = 200
DISPLAY_NAME_TOO_LONG_MESSAGE = "The display name must have at most 200 characters."

PasswordPrompt = Callable[[str], str]
Command = Callable[..., int]


class Refusal(Exception):
    """A refusal with a one-line message for stderr (exit 1)."""


async def _owner_exists(session: AsyncSession) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(UserRole.role_id == Role.id).where(Role.code == OWNER)
            )
        )
    )


async def _owner_role_id(session: AsyncSession) -> uuid.UUID:
    role_id = await session.scalar(select(Role.id).where(Role.code == OWNER))
    if role_id is None:
        raise Refusal(ROLES_NOT_SEEDED_MESSAGE)
    return role_id


async def _precheck(engine: AsyncEngine) -> None:
    """Fail fast, before asking for a password (read only)."""
    async with UnitOfWork(create_sessionmaker(engine)) as uow:
        await ensure_at_head(uow.session)
        await _owner_role_id(uow.session)
        if await _owner_exists(uow.session):
            raise Refusal(OWNER_EXISTS_MESSAGE)


async def _create_owner(
    engine: AsyncEngine, *, username: str, display_name: str, password_hash: str
) -> uuid.UUID:
    async with UnitOfWork(create_sessionmaker(engine)) as uow:
        session = uow.session
        await session.execute(
            select(func.pg_advisory_xact_lock(BOOTSTRAP_OWNER_LOCK_KEY))
        )
        await ensure_at_head(session)
        role_id = await _owner_role_id(session)
        if await _owner_exists(session):
            raise Refusal(OWNER_EXISTS_MESSAGE)
        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            state=ACTIVE,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=role_id))
        await session.flush()
        return user.id


def _read_password(*, from_stdin: bool, stdin: TextIO, prompt: PasswordPrompt) -> str:
    if from_stdin:
        line = stdin.readline()
        return line.removesuffix("\n").removesuffix("\r")
    first = prompt("Password: ")
    second = prompt("Repeat the password: ")
    if first != second:
        raise Refusal(MISMATCH_MESSAGE)
    return first


def bootstrap_owner(
    args: argparse.Namespace,
    settings: Settings,
    *,
    engine_factory: EngineFactory,
    stdin: TextIO,
    prompt: PasswordPrompt,
) -> int:
    async def _precheck_run() -> None:
        engine = engine_factory(settings)
        try:
            await _precheck(engine)
        finally:
            await engine.dispose()

    asyncio.run(_precheck_run())

    try:
        username = normalize_username(args.username)
    except UsernameInvalidError as exc:
        raise Refusal(str(exc.detail)) from None
    display_name = (args.display_name or "").strip() or username
    if len(display_name) > DISPLAY_NAME_MAX_LENGTH:
        raise Refusal(DISPLAY_NAME_TOO_LONG_MESSAGE)

    password = _read_password(
        from_stdin=args.password_stdin, stdin=stdin, prompt=prompt
    )
    try:
        validate_password(password, username=username)
    except PasswordPolicyError as exc:
        raise Refusal(str(exc.detail)) from None
    password_hash = hash_password(password)

    async def _create_run() -> uuid.UUID:
        engine = engine_factory(settings)
        try:
            return await _create_owner(
                engine,
                username=username,
                display_name=display_name,
                password_hash=password_hash,
            )
        finally:
            await engine.dispose()

    user_id = asyncio.run(_create_run())
    logger.info(
        "owner_bootstrapped",
        extra={
            "event": "owner_bootstrapped",
            "user_id": str(user_id),
            "username": username,
        },
    )
    print(f"Created the Owner account '{username}'.")
    return 0


COMMANDS: dict[str, Command] = {"bootstrap-owner": bootstrap_owner}
"""Subcommand name -> handler(args, settings, *, engine_factory, stdin, prompt)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Classora operator commands."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-owner",
        help="Create the first Owner account (only while no Owner exists).",
    )
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument(
        "--display-name", help="Shown name (default: the normalized username)."
    )
    bootstrap.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin instead of a prompt.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: EngineFactory = create_seed_engine,
    stdin: TextIO | None = None,
    prompt: PasswordPrompt = getpass.getpass,
) -> int:
    """Run a command; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (M1-05).
        print(exc, file=sys.stderr)
        return 1
    configure_logging()
    command: Command = COMMANDS[args.command]
    try:
        return command(
            args,
            settings,
            engine_factory=engine_factory,
            stdin=stdin if stdin is not None else sys.stdin,
            prompt=prompt,
        )
    except (Refusal, DatabaseNotAtHeadError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
