"""Prove that a transaction doesn't wait for another one's locks (#427).

Comparing when two transactions ended proves nothing: both run on one event
loop, so a pause of that loop (a full garbage collection, a slow CI runner)
can push B past a timed hold of A with no lock involved. Here A has no timed
hold: it stays open until B has committed. If B needed anything A holds, B
could never commit, A would give up after :data:`BLOCKED_AFTER_SECONDS` and
the test fails. The limit only bounds that failure: a passing run never waits
for it, however slow the machine is.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from tests.integration.conftest import short_lived_engine

BLOCKED_AFTER_SECONDS = 30.0

Step = Callable[[UnitOfWork], Awaitable[Any]]


def on_session(step: Callable[[AsyncSession], Awaitable[Any]]) -> Step:
    """A step written against the session, as a :data:`Step`."""

    async def _step(uow: UnitOfWork) -> Any:
        return await step(uow.session)

    return _step


async def _commit_b_inside_a(
    url: str, actor: uuid.UUID, first: Step, second: Step
) -> tuple[Any, Any]:
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    b_finished = asyncio.Event()

    async def run_a() -> Any:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a), actor) as uow:
                result = await first(uow)
                holding.set()
                async with asyncio.timeout(BLOCKED_AFTER_SECONDS):
                    await b_finished.wait()
            return result
        finally:
            holding.set()

    async def run_b() -> Any:
        await holding.wait()
        try:
            async with UnitOfWork(create_sessionmaker(engine_b), actor) as uow:
                return await second(uow)
        finally:
            b_finished.set()

    try:
        # Both run to the end first: A's timeout rolls A back, which frees B.
        results = await asyncio.gather(run_a(), run_b(), return_exceptions=True)
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    a, b = results
    if isinstance(a, TimeoutError):
        pytest.fail(f"B waited for A: not committed {BLOCKED_AFTER_SECONDS} s after A")
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return a, b


def commit_b_inside_a(
    url: str, actor: uuid.UUID, first: Step, second: Step
) -> tuple[Any, Any]:
    """A runs ``first`` and stays open until B has run ``second`` and committed;
    A then commits. Returns both results; fails the test if B can't commit
    while A is open."""
    return asyncio.run(_commit_b_inside_a(url, actor, first, second))
