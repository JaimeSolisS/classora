"""Concurrent offer writes (#52 A4, D4, I3), with two connections (the #47 D3
pattern of ``test_class_session_create_concurrency``).

Transaction A makes its change and keeps its transaction open for a moment;
transaction B starts once A's change is flushed and uncommitted, so B waits
on the session row lock (the offer lock order) and then sees A's result.

- **Accept vs decline** of one offer: one ``ok``, one ``offer_not_pending``.
- **Accept vs the expiry run** at the deadline, in both orders: the end state
  is ``accepted`` + ``confirmed`` or ``expired`` + ``unassigned``, never
  mixed.
- **Two staff offering one unassigned session**: one ``ok``, one
  ``version_conflict``.
- **Series offers (#233 I3)**: accept vs decline, accept vs the expiry run
  (both orders), accept vs ``materialize_series(mode="extend")`` (both
  orders: no ``offered`` row is ever left without a pending offer, and no
  deadlock), and two staff offering one series.

Each case runs 5 times, on new references.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    AssignmentOfferCreate,
    ClassSeriesCreate,
    ClassSeriesOfferCreate,
    ClassSessionCreate,
    SeriesRecurrence,
)
from tests.conftest import frozen_clock
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    START,
    _references,
)

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

HOLD_SECONDS = 0.5
REPEATS = 5


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            result = await change(uow.session)
            if after_change is not None:
                await after_change()
    except StaleVersionError:
        return "version_conflict"
    except AppError as exc:
        return exc.code
    return "skipped" if result is False else "ok"


async def race(url: str, first: Change, second: Change) -> list[str]:
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    changed = asyncio.Event()

    async def hold() -> None:
        changed.set()
        await asyncio.sleep(HOLD_SECONDS)

    async def run_a() -> str:
        try:
            return await _outcome(engine_a, first, hold)
        finally:
            changed.set()

    async def run_b() -> str:
        await changed.wait()
        return await _outcome(engine_b, second)

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


async def _in_uow(url: str, change: Change) -> Any:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await change(uow.session)
    finally:
        await engine.dispose()


def _body(refs: dict[str, uuid.UUID], *, tutor: bool) -> ClassSessionCreate:
    return ClassSessionCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"] if tutor else None,
        classroom_id=refs["room"],
        starts_at=START,
        ends_at=START + timedelta(minutes=60),
    )


async def _offered(url: str, refs: dict[str, uuid.UUID]) -> tuple[uuid.UUID, Any]:
    """A session offered to ``refs["tutor"]``: ``(session id, pending offer)``."""

    async def _create(session: AsyncSession) -> Any:
        return await scheduling.create_class_session(
            session, _body(refs, tutor=True), actor_id=refs["tutor"]
        )

    created = await _in_uow(url, _create)
    assert created.pending_offer is not None
    return created.id, created.pending_offer


def _answer(offer_id: uuid.UUID, tutor: uuid.UUID, *, accept: bool) -> Change:
    async def _run(session: AsyncSession) -> None:
        await scheduling.answer_offer(session, offer_id, caller_id=tutor, accept=accept)

    return _run


async def _state(
    url: str, session_id: uuid.UUID, offer_id: uuid.UUID
) -> tuple[str, str]:
    rows = await run_sql_async(
        url,
        "SELECT (SELECT state FROM class_sessions WHERE id = :s) AS state,"
        " (SELECT status FROM assignment_offers WHERE id = :o) AS status",
        {"s": session_id, "o": offer_id},
    )
    return rows[0].status, rows[0].state


async def test_accept_and_decline_of_one_offer_one_wins(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id, offer = await _offered(seeded_url, refs)

        outcomes = await race(
            seeded_url,
            _answer(offer.id, refs["tutor"], accept=True),
            _answer(offer.id, refs["tutor"], accept=False),
        )

        assert outcomes == ["ok", "offer_not_pending"]
        assert await _state(seeded_url, session_id, offer.id) == (
            "accepted",
            "confirmed",
        )


def _expire_at_deadline(offer: Any) -> Change:
    async def _run(session: AsyncSession) -> bool:
        # The run at the deadline (the accept runs on the real clock, before
        # it).
        return await scheduling.expire_offer(session, offer.id, offer.respond_by)

    return _run


@pytest.mark.parametrize("expiry_first", [False, True])
async def test_accept_and_expiry_never_leave_a_mixed_state(seeded_url, expiry_first):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id, offer = await _offered(seeded_url, refs)
        expire = _expire_at_deadline(offer)
        accept = _answer(offer.id, refs["tutor"], accept=True)
        first, second = (expire, accept) if expiry_first else (accept, expire)

        outcomes = await race(seeded_url, first, second)

        end = await _state(seeded_url, session_id, offer.id)
        if expiry_first:
            assert outcomes == ["ok", "offer_not_pending"]
            assert end == ("expired", "unassigned")
        else:
            assert outcomes == ["ok", "skipped"]
            assert end == ("accepted", "confirmed")


async def _unassigned(url: str, refs: dict[str, uuid.UUID]) -> uuid.UUID:
    async def _create(session: AsyncSession) -> Any:
        return await scheduling.create_class_session(
            session, _body(refs, tutor=False), actor_id=refs["tutor"]
        )

    created = await _in_uow(url, _create)
    return uuid.UUID(str(created.id))


def _offer_to(session_id: uuid.UUID, tutor: uuid.UUID, actor: uuid.UUID) -> Change:
    async def _run(session: AsyncSession) -> None:
        await scheduling.offer_class_session(
            session,
            session_id,
            AssignmentOfferCreate(tutor_user_id=tutor, version=1),
            actor_id=actor,
        )

    return _run


async def test_two_staff_offering_one_session_one_wins(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id = await _unassigned(seeded_url, refs)

        outcomes = await race(
            seeded_url,
            _offer_to(session_id, refs["tutor"], refs["tutor"]),
            _offer_to(session_id, refs["other_tutor"], refs["tutor"]),
        )

        assert outcomes == ["ok", "version_conflict"]
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM assignment_offer_sessions"
            " WHERE class_session_id = :id",
            {"id": session_id},
        )
        assert rows[0].n == 1


# --- Series offers (#233 D4, I3) -----------------------------------------------------

SERIES_NOW = datetime(2031, 1, 3, 12, 0, tzinfo=ZoneInfo("America/Mexico_City"))
"""A Friday; the series' first Monday is 2031-01-06."""
HORIZON = 12


def _series_body(refs: dict[str, uuid.UUID], *, tutor: bool) -> ClassSeriesCreate:
    return ClassSeriesCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"] if tutor else None,
        classroom_id=refs["room"],
        starts_on=date(2031, 1, 6),
        start_time=time(16),
        duration_minutes=60,
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
    )


async def _series(
    url: str, refs: dict[str, uuid.UUID], *, tutor: bool = True
) -> tuple[uuid.UUID, Any]:
    """A series created at :data:`SERIES_NOW`: ``(series id, its offer row or
    None)``."""

    async def _create(session: AsyncSession) -> Any:
        return await scheduling.create_class_series(
            session,
            _series_body(refs, tutor=tutor),
            horizon_weeks=HORIZON,
            actor_id=refs["tutor"],
        )

    with frozen_clock(SERIES_NOW):
        created = await _in_uow(url, _create)
    offers = await run_sql_async(
        url,
        "SELECT id, respond_by FROM assignment_offers WHERE class_series_id = :id",
        {"id": created.id},
    )
    return created.id, (offers[0] if offers else None)


async def _series_end(url: str, series_id: uuid.UUID) -> tuple[Any, set[str], int]:
    """``(offer statuses, row states, offered rows without a pending
    offer)``."""
    offers = await run_sql_async(
        url,
        "SELECT status FROM assignment_offers WHERE class_series_id = :id",
        {"id": series_id},
    )
    rows = await run_sql_async(
        url,
        "SELECT state FROM class_sessions WHERE series_id = :id",
        {"id": series_id},
    )
    [uncovered] = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_sessions s WHERE s.series_id = :id"
        " AND s.state = 'offered' AND NOT EXISTS (SELECT 1 FROM"
        " assignment_offer_sessions x JOIN assignment_offers o ON o.id = x.offer_id"
        " WHERE x.class_session_id = s.id AND o.status = 'pending')",
        {"id": series_id},
    )
    return (
        tuple(sorted(o.status for o in offers)),
        {r.state for r in rows},
        int(uncovered.n),
    )


async def test_accept_and_decline_of_one_series_offer_one_wins(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, offer = await _series(seeded_url, refs)

        outcomes = await race(
            seeded_url,
            _answer(offer.id, refs["tutor"], accept=True),
            _answer(offer.id, refs["tutor"], accept=False),
        )

        assert outcomes == ["ok", "offer_not_pending"]
        assert await _series_end(seeded_url, series_id) == (
            ("accepted",),
            {"confirmed"},
            0,
        )


@pytest.mark.parametrize("expiry_first", [False, True])
async def test_accept_and_expiry_of_a_series_offer_never_mix(seeded_url, expiry_first):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, offer = await _series(seeded_url, refs)
        expire = _expire_at_deadline(offer)
        accept = _answer(offer.id, refs["tutor"], accept=True)
        first, second = (expire, accept) if expiry_first else (accept, expire)

        outcomes = await race(seeded_url, first, second)

        end = await _series_end(seeded_url, series_id)
        if expiry_first:
            assert outcomes == ["ok", "offer_not_pending"]
            assert end == (("expired",), {"unassigned"}, 0)
        else:
            assert outcomes == ["ok", "skipped"]
            assert end == (("accepted",), {"confirmed"}, 0)


def _extend(series_id: uuid.UUID) -> Change:
    async def _run(session: AsyncSession) -> None:
        # Tuesday: the week-13 Monday enters the horizon.
        await scheduling.materialize_series(
            session,
            series_id,
            now=SERIES_NOW + timedelta(days=4),
            horizon_weeks=HORIZON,
            mode="extend",
        )

    return _run


@pytest.mark.parametrize("extend_first", [False, True])
async def test_accept_and_materialization_leave_no_uncovered_offered_row(
    seeded_url, extend_first
):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, offer = await _series(seeded_url, refs)
        extend = _extend(series_id)
        accept = _answer(offer.id, refs["tutor"], accept=True)
        first, second = (extend, accept) if extend_first else (accept, extend)

        outcomes = await race(seeded_url, first, second)

        assert outcomes == ["ok", "ok"]
        assert await _series_end(seeded_url, series_id) == (
            ("accepted",),
            {"confirmed"},
            0,
        )
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM class_sessions WHERE series_id = :id",
            {"id": series_id},
        )
        assert rows[0].n == 25


def _offer_series_to(
    series_id: uuid.UUID, tutor: uuid.UUID, actor: uuid.UUID
) -> Change:
    async def _run(session: AsyncSession) -> None:
        with frozen_clock(SERIES_NOW):
            await scheduling.offer_class_series(
                session,
                series_id,
                ClassSeriesOfferCreate(tutor_user_id=tutor, version=1),
                actor_id=actor,
            )

    return _run


async def test_two_staff_offering_one_series_one_wins(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        series_id, _ = await _series(seeded_url, refs, tutor=False)

        outcomes = await race(
            seeded_url,
            _offer_series_to(series_id, refs["tutor"], refs["tutor"]),
            _offer_series_to(series_id, refs["other_tutor"], refs["tutor"]),
        )

        assert outcomes[0] == "ok"
        assert outcomes[1] in {"version_conflict", "series_not_offerable"}
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM assignment_offers WHERE class_series_id = :id",
            {"id": series_id},
        )
        assert rows[0].n == 1
