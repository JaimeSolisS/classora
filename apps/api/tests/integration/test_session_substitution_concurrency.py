"""Concurrent substitution and cancel writes (#55 G1, I4), with two
connections (the #47 D3 pattern; ``race`` from
``test_assignment_offer_concurrency``).

Transaction A makes its change and keeps its transaction open for a moment;
transaction B starts once A's change is flushed and uncommitted, so B waits
on A's row locks and then sees A's result.

- **Cancel vs the substitute's accept**, both orders: the row ends
  ``cancelled`` and no pending offer is left on it; either the substitution
  was withdrawn by the cancel, or the substitute took the session and it was
  then cancelled.
- **Two substitutions of one session**: one ``ok``, one refused.
- **Cancel of a series row vs** ``materialize_series(mode="extend")`` and vs
  a series-offer accept, both orders: no deadlock, a consistent end state.

Each case runs 5 times, on new references.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.offer_flow import lock_sessions
from app.modules.scheduling.schemas import SubstitutionCreate
from tests.integration.conftest import run_sql_async
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _answer,
    _extend,
    _in_uow,
    _offered,
    _series,
    race,
)
from tests.integration.test_class_session_create_concurrency import _references

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]


async def _confirmed_with_substitution(
    url: str, refs: dict[str, uuid.UUID]
) -> tuple[uuid.UUID, uuid.UUID]:
    """A session confirmed by ``refs["tutor"]`` with a pending substitution
    to ``refs["other_tutor"]``: ``(session id, substitution offer id)``."""
    session_id, offer = await _offered(url, refs)
    await _in_uow(
        url,
        lambda db: scheduling.answer_offer(
            db, offer.id, caller_id=refs["tutor"], accept=True
        ),
    )

    async def _substitute(db: AsyncSession) -> Any:
        [row] = await lock_sessions(db, [session_id])
        return await scheduling.substitute_class_session(
            db,
            session_id,
            SubstitutionCreate(
                substitute_tutor_user_id=refs["other_tutor"],
                version=row.version,
                reason="Enfermedad",
            ),
            actor_id=refs["tutor"],
        )

    created = await _in_uow(url, _substitute)
    return session_id, created.substitution.offer_id


def _cancel_current(session_id: uuid.UUID, actor: uuid.UUID) -> Change:
    """Cancel with the version read under the row lock (a one-off session:
    no series lock to take first), as a user who re-reads."""

    async def _run(db: AsyncSession) -> None:
        [row] = await lock_sessions(db, [session_id])
        await scheduling.cancel_class_session(
            db,
            session_id,
            expected_version=row.version,
            reason="Cierre",
            by_user_id=actor,
        )

    return _run


def _cancel_at(session_id: uuid.UUID, version: int, actor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.cancel_class_session(
            db, session_id, expected_version=version, reason="Cierre", by_user_id=actor
        )

    return _run


async def _end(url: str, session_id: uuid.UUID, offer_id: uuid.UUID) -> Any:
    [row] = await run_sql_async(
        url,
        "SELECT s.state, s.tutor_user_id, o.status,"
        " (SELECT count(*) FROM assignment_offer_sessions x"
        "  JOIN assignment_offers p ON p.id = x.offer_id"
        "  WHERE x.class_session_id = s.id AND p.status = 'pending') AS pending"
        " FROM class_sessions s, assignment_offers o"
        " WHERE s.id = :s AND o.id = :o",
        {"s": session_id, "o": offer_id},
    )
    return row


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_cancel_and_the_substitutes_accept(seeded_url, cancel_first):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id, offer_id = await _confirmed_with_substitution(seeded_url, refs)
        cancel = _cancel_current(session_id, refs["tutor"])
        accept = _answer(offer_id, refs["other_tutor"], accept=True)
        first, second = (cancel, accept) if cancel_first else (accept, cancel)

        outcomes = await race(seeded_url, first, second)

        end = await _end(seeded_url, session_id, offer_id)
        assert (end.state, end.pending) == ("cancelled", 0)
        if cancel_first:
            assert outcomes == ["ok", "offer_not_pending"]
            assert (end.status, end.tutor_user_id) == ("withdrawn", refs["tutor"])
        else:
            assert outcomes == ["ok", "ok"]
            assert (end.status, end.tutor_user_id) == ("accepted", refs["other_tutor"])


def _substitute_to(session_id: uuid.UUID, tutor: uuid.UUID, actor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        [row] = await lock_sessions(db, [session_id])
        await scheduling.substitute_class_session(
            db,
            session_id,
            SubstitutionCreate(
                substitute_tutor_user_id=tutor, version=row.version, reason="Viaje"
            ),
            actor_id=actor,
        )

    return _run


async def test_two_substitutions_of_one_session_one_wins(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id, offer = await _offered(seeded_url, refs)
        await _in_uow(
            seeded_url,
            lambda db, offer_id=offer.id, tutor=refs["tutor"]: scheduling.answer_offer(
                db, offer_id, caller_id=tutor, accept=True
            ),
        )
        # B is refused at its state check (A's substitution is pending), so
        # the third tutor's own qualifications never matter.
        third = (await _references(seeded_url))["tutor"]

        outcomes = await race(
            seeded_url,
            _substitute_to(session_id, refs["other_tutor"], refs["tutor"]),
            _substitute_to(session_id, third, refs["tutor"]),
        )

        assert outcomes[0] == "ok"
        assert outcomes[1] in {"version_conflict", "class_session_not_substitutable"}
        [row] = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM class_session_substitutions"
            " WHERE class_session_id = :id",
            {"id": session_id},
        )
        assert row.n == 1


async def _first_row(url: str, series_id: uuid.UUID) -> Any:
    rows = await run_sql_async(
        url,
        "SELECT id, version, original_start FROM class_sessions WHERE series_id = :id"
        " ORDER BY starts_at LIMIT 1",
        {"id": series_id},
    )
    return rows[0]


async def _series_state(url: str, series_id: uuid.UUID) -> Any:
    rows = await run_sql_async(
        url,
        "SELECT state, count(*) AS n FROM class_sessions WHERE series_id = :id"
        " GROUP BY state ORDER BY state",
        {"id": series_id},
    )
    return {row.state: int(row.n) for row in rows}


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_cancel_of_a_series_row_and_the_nightly_extension(
    seeded_url, cancel_first
):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, _ = await _series(seeded_url, refs, tutor=False)
        row = await _first_row(seeded_url, series_id)
        cancel = _cancel_at(row.id, row.version, refs["tutor"])
        extend = _extend(series_id)
        first, second = (cancel, extend) if cancel_first else (extend, cancel)

        outcomes = await race(seeded_url, first, second)

        assert outcomes == ["ok", "ok"]
        # 24 rows in the horizon, one more from the extension; the cancelled
        # date is never re-created.
        assert await _series_state(seeded_url, series_id) == {
            "cancelled": 1,
            "unassigned": 24,
        }


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_cancel_of_a_covered_row_and_the_series_offer_accept(
    seeded_url, cancel_first
):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, offer = await _series(seeded_url, refs)
        row = await _first_row(seeded_url, series_id)
        cancel = _cancel_at(row.id, row.version, refs["other_tutor"])
        accept = _answer(offer.id, refs["tutor"], accept=True)
        first, second = (cancel, accept) if cancel_first else (accept, cancel)

        outcomes = await race(seeded_url, first, second)

        states = await _series_state(seeded_url, series_id)
        if cancel_first:
            # B6: the row stays covered; the accept confirms the others.
            assert outcomes == ["ok", "ok"]
            assert states == {"cancelled": 1, "confirmed": 23}
        else:
            # The accept bumped the row's version: the stale cancel is
            # refused, nothing is half done.
            assert outcomes == ["ok", "version_conflict"]
            assert states == {"confirmed": 24}
