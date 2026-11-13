"""DEV's public visitor login (#674 D6):
``python -m tests.factories.simulation.visitor``.

Run once after ``python -m tests.factories.simulation --language en`` by
deploy-dev's ``dev-data`` job (``_docs/runbooks/dev-data.md``), on the same
database, before the dump is taken. It adds one more Owner to the English
simulated center:

- username ``visitor``, display name "Demo visitor", password
  ``visitor-classora`` (published in the root README for graders);
- role ``owner`` only, ``active``, language ``en``, the current privacy
  notice accepted.

Steps:

1. **Guard.** ``APP_ENV`` is read from the environment first: any value but
   ``local`` or ``dev`` prints ``Refusing to create the visitor: APP_ENV=<value>
   (local and dev only)`` and exits 2 before any settings, engine or
   connection. PROD never gets the visitor.
2. Load the settings (a configuration error exits 1).
3. In one Unit of Work, as the database owner (``DATABASE_MIGRATION_URL``,
   else ``DATABASE_DIRECT_URL``), with the clock at the simulation's "now"
   (:data:`~.anchor.SIM_NOW`), like the rest of the dataset:
   - the database must be at the Alembic head (exit 1 otherwise);
   - ``visitor`` already an active Owner with no other role: nothing changes,
     exit 0 (a rerun is a no-op); present in any other shape: exit 3,
     nothing changes;
   - the English simulated Owner (``owner``) must be active (exit 3
     otherwise: run the English simulation first);
   - that Owner invites ``visitor`` with the ``owner`` role, the invitation
     is redeemed with the password, the language is stored and the current
     privacy notice is accepted, all through the identity services.

The other simulated accounts keep their published passwords
(``_docs/Full_README.md``). Prints one line; never a password or hash.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import clock
from app.core.config import ConfigurationError, get_settings
from app.core.db import create_sessionmaker
from app.core.reauth import RecentAuthGuard
from app.core.uow import UnitOfWork
from app.modules.families import service as families_service
from app.modules.identity import invitations
from app.modules.identity import service as identity
from app.modules.identity.models import User
from app.modules.identity.permissions import OWNER
from app.seeds.runner import (
    DatabaseNotAtHeadError,
    EngineFactory,
    create_seed_engine,
    ensure_at_head,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_NOW, sim_clock
from tests.factories.simulation.plan import STAFF_USERNAMES

ALLOWED_APP_ENV: Final = frozenset({"local", "dev"})
VISITOR_USERNAME: Final = "visitor"
VISITOR_DISPLAY_NAME: Final = "Demo visitor"
VISITOR_PASSWORD: Final = "visitor-classora"  # noqa: S105 - public by design (#671)
VISITOR_LANGUAGE: Final = "en"
INVITER_USERNAME: Final = STAFF_USERNAMES["en"]["owner"]
INVITE_ACTION: Final = "user.invitation.staff_role"
INVITATION_ORIGIN: Final = "http://localhost:5173"
"""Only the token is read back from the (never shown) invitation link."""
DESCRIPTION: Final = (
    "Add the DEV visitor Owner (visitor / visitor-classora) to the English"
    " simulated center. Local and DEV only (APP_ENV=local or dev; never"
    " prod). Run it after the English simulation; a rerun changes nothing."
)

CREATED: Final = "visitor: created (owner, active, en, privacy notice accepted)"
UNCHANGED: Final = "visitor: already there (nothing changed)"


class VisitorRefusedError(Exception):
    """The database can't take the visitor; nothing was written."""


def refusal(app_env: str) -> str | None:
    """The refusal line for ``app_env``, or ``None`` for ``local`` and ``dev``."""
    if app_env in ALLOWED_APP_ENV:
        return None
    return f"Refusing to create the visitor: APP_ENV={app_env} (local and dev only)"


async def _user(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username))


async def ensure_visitor(session: AsyncSession) -> bool:
    """Create the visitor in the caller's transaction; ``False`` when it
    already exists as an active Owner (nothing written). Raises
    :class:`VisitorRefusedError` otherwise."""
    existing = await _user(session, VISITOR_USERNAME)
    if existing is not None:
        loaded = await identity.load_principal(session, existing.id)
        if loaded is None or loaded.roles != frozenset({OWNER}):
            raise VisitorRefusedError(
                f"{VISITOR_USERNAME} exists but is not an active Owner with no"
                " other role; nothing changed"
            )
        return False

    owner = await _user(session, INVITER_USERNAME)
    inviter = (
        None if owner is None else await identity.load_principal(session, owner.id)
    )
    if inviter is None or OWNER not in inviter.roles:
        raise VisitorRefusedError(
            f"no active simulated Owner '{INVITER_USERNAME}': run"
            " `python -m tests.factories.simulation --language en` first;"
            " nothing changed"
        )

    with seed_actor(session, inviter.user_id):
        issued = await invitations.create_invitation(
            session,
            principal=inviter,
            guard=RecentAuthGuard(INVITE_ACTION, clock.now()),
            username=VISITOR_USERNAME,
            display_name=VISITOR_DISPLAY_NAME,
            role=OWNER,
            app_origin=INVITATION_ORIGIN,
        )
    raw_token = issued.invitation_url.split("#token=", 1)[1]
    # No actor, as the public redeem route.
    await invitations.redeem_invitation(
        session, raw_token=raw_token, password=VISITOR_PASSWORD
    )
    with seed_actor(session, issued.user_id):
        await identity.update_user_preferences(
            session, issued.user_id, language=VISITOR_LANGUAGE
        )
        current = await identity.current_privacy_notice(session)
        await identity.accept_privacy_notice(
            session, user_id=issued.user_id, version=current.version
        )
    created = await identity.load_principal(session, issued.user_id)
    if created is None or created.roles != frozenset({OWNER}):  # pragma: no cover
        raise VisitorRefusedError("the visitor did not end up an active Owner")
    if not created.privacy_notice_accepted:  # pragma: no cover
        raise VisitorRefusedError("the visitor did not accept the privacy notice")
    return True


async def _run(engine: AsyncEngine) -> bool:
    # As app.main, app.cli and app.worker (and run_simulation): activation
    # through an invitation goes through the families gate.
    identity.register_student_login_gate(families_service.STUDENT_LOGIN_GATE)
    try:
        with sim_clock(SIM_NOW):
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await ensure_at_head(uow.session)
                return await ensure_visitor(uow.session)
    finally:
        await engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: EngineFactory = create_seed_engine,
) -> int:
    """Run the command; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.factories.simulation.visitor", description=DESCRIPTION
    )
    parser.parse_args(argv)

    raw_env = os.environ.get("APP_ENV")
    if raw_env is not None and raw_env != "":
        message = refusal(raw_env)
        if message is not None:
            print(message, file=sys.stderr)
            return 2

    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        return 1
    message = refusal(settings.app_env)
    if message is not None:  # pragma: no cover - the environment check above
        print(message, file=sys.stderr)
        return 2

    try:
        created = asyncio.run(_run(engine_factory(settings)))
    except DatabaseNotAtHeadError as exc:
        print(exc, file=sys.stderr)
        return 1
    except VisitorRefusedError as exc:
        print(f"visitor: {exc}", file=sys.stderr)
        return 3
    print(CREATED if created else UNCHANGED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
