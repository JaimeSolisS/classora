"""The KPI performance budget (#126 C1-C4; ``_docs/kpis.md``).

- C1: each KPI service function sends a fixed number of statements, the
  same on the worked examples B1-B5 and with every row doubled (pinned).
  K1 sends exactly what #117 ``profitability_report`` sends on the same data
  and basis.
- C2: EXPLAIN (sequential scans off, literal binds, the V1 rows) of the K4,
  K5 and K6 session statements uses ``ix_class_sessions_starts_at`` (or the
  partial cancelled index) and never a sequential scan of
  ``class_sessions``; no filter applies a function to ``starts_at``.
- C3: the volume V1, inserted directly with ``INSERT … SELECT
  generate_series`` (not through the workflows): 12 months, 20 tutors, 300
  students, 10 active rooms (half with windows), operating hours for 6 days,
  5 closures, 10,000 individual sessions (85 % completed, 5 % confirmed,
  10 % cancelled after confirmation) with one participant each and 8,000
  attendance marks, 3,000 manual charges over 30 accounts and 2,000
  payments. Best of 3 warm in-process calls over 366 dates: K4, K5, K6
  ≤ 1.0 s each; K1 (both bases) and K2 ≤ 2.0 s each. The timings are
  printed. Nothing is cached, indexed or materialized for it (C4).
"""

import asyncio
import time as timer
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.reporting import finance as reports
from app.modules.reporting import kpis
from tests.conftest import frozen_clock
from tests.factories.billing import charge
from tests.factories.families import persist_student
from tests.integration import kpi_support as ks
from tests.integration import receipt_support as rs
from tests.integration.billing_ledger_support import count_statements
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")

# --- C1 ---------------------------------------------------------------------------

C1_RANGE = (date(2031, 1, 1), date(2031, 4, 1))
C1_NOW = ks.noon("2031-04-02")


def _calls() -> dict[str, Callable[[AsyncSession], Awaitable[Any]]]:
    start, end = C1_RANGE
    return {
        "profitability_accrual": lambda s: reports.profitability_report(
            s, basis="accrual", start=start, end=end
        ),
        "profitability_cash": lambda s: reports.profitability_report(
            s, basis="cash", start=start, end=end
        ),
        "k1_accrual": lambda s: kpis.revenue_trend(
            s, basis="accrual", start=start, end=end
        ),
        "k1_cash": lambda s: kpis.revenue_trend(s, basis="cash", start=start, end=end),
        "k2": lambda s: kpis.course_profitability(s, start=start, end=end),
        "k4": lambda s: kpis.room_utilization(s, start=start, end=end),
        "k5": lambda s: kpis.student_retention(s, start=start, end=end),
        "k6": lambda s: kpis.tutor_performance(s, start=start, end=end),
    }


def _counts(url: str) -> dict[str, int]:
    with frozen_clock(C1_NOW):
        return {name: count_statements(url, call)[0] for name, call in _calls().items()}


def _statements(url: str) -> list[str]:
    seen: list[str] = []

    async def _go() -> None:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            for call in _calls().values():
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    await call(uow.session)
        finally:
            await engine.dispose()

    with frozen_clock(C1_NOW):
        asyncio.run(_go())
    return seen


def _build_all(url: str, suffix: str) -> None:
    ks.build_b1(url)
    ks.build_b2(url, suffix)
    ks.build_b3(url, hours=suffix == "")
    ks.build_b4(url)
    ks.build_b5(url, suffix)


PINNED = {
    "profitability_accrual": 7,
    "profitability_cash": 3,
    "k1_accrual": 7,
    "k1_cash": 3,
    "k2": 7,
    "k4": 6,
    "k5": 2,
    "k6": 4,
}


def test_c1_fixed_statement_counts(seeded_url):
    url = seeded_url
    empty = _counts(url)
    _build_all(url, "")
    small = _counts(url)
    _build_all(url, "bis ")
    large = _counts(url)
    assert large == small == PINNED, (empty, small, large)
    # K1 is one #117 call; K2 is at most #117 accrual + 1; the ceilings.
    assert small["k1_accrual"] == small["profitability_accrual"]
    assert small["k1_cash"] == small["profitability_cash"]
    assert small["k2"] <= small["profitability_accrual"] + 1
    assert small["k4"] <= 7
    assert small["k5"] <= 4
    assert small["k6"] <= 4
    # Read-only: no lock, no write.
    for statement in _statements(url):
        upper = statement.upper()
        assert "FOR UPDATE" not in upper
        assert "FOR SHARE" not in upper
        assert not upper.lstrip().startswith(("INSERT", "UPDATE", "DELETE"))


# --- V1 ---------------------------------------------------------------------------

V1_NOW = ks.noon("2031-04-01")
V1_RANGE = (date(2030, 4, 1), date(2031, 4, 1))
"""366 dates (``to − from`` = 365)."""
V1_FIRST = ks.local("2030-04-01", 9)
SESSIONS = 10_000
PER_DAY = 28


def _ids(url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    return [row.id for row in run_sql(url, sql, params)]


def _seed_v1(url: str) -> dict[str, Any]:
    with frozen_clock(V1_NOW):
        who = ks.actor(url)

        async def _people(session: AsyncSession) -> dict[str, list[uuid.UUID]]:
            tutors = [await ks.user(session, f"Tutor V1 {n:02d}") for n in range(20)]
            students = [
                (await persist_student(session, contacts=0)).id for _ in range(300)
            ]
            rooms = []
            for n in range(10):
                windows = (
                    [(day, "09:00", "21:00") for day in range(1, 7)] if n % 2 else []
                )
                rooms.append(await ks.room(session, f"Aula V1 {n}", windows))
            for n in range(5):
                await ks.closure(session, f"2030-{6 + n:02d}-15")
            subject = await ks.persist_subject(session)
            return {
                "tutors": tutors,
                "students": students,
                "rooms": rooms,
                "subject": [subject.id],
            }

        made = in_uow(url, _people)
        ks.set_hours(url, ks.B3_HOURS)
        params = {
            "subject": made["subject"][0],
            "tutors": made["tutors"],
            "rooms": made["rooms"],
            "first": V1_FIRST,
            "n": SESSIONS,
            "per_day": PER_DAY,
        }
        run_sql(
            url,
            "INSERT INTO class_sessions (kind, state, subject_id, tutor_user_id,"
            " classroom_id, starts_at, ends_at, scheduled_minutes,"
            " first_confirmed_at, cancelled_at, cancellation_reason)"
            " SELECT 'individual', x.state, :subject,"
            " (CAST(:tutors AS uuid[]))[(x.k % 20) + 1],"
            " (CAST(:rooms AS uuid[]))[(x.k % 10) + 1],"
            " x.starts_at, x.starts_at + interval '60 minutes', 60,"
            " x.starts_at - interval '7 days',"
            " CASE WHEN x.state = 'cancelled' THEN x.starts_at - interval '1 day' END,"
            " CASE WHEN x.state = 'cancelled' THEN 'Cancelada V1' END"
            " FROM (SELECT g % :per_day AS k,"
            " CAST(:first AS timestamptz) + (g / :per_day) * interval '1 day'"
            " + ((g % :per_day) / 10) * interval '1 hour' AS starts_at,"
            " CASE WHEN g % 20 < 17 THEN 'completed' WHEN g % 20 = 17"
            " THEN 'confirmed' ELSE 'cancelled' END AS state"
            " FROM generate_series(0, :n - 1) AS g) AS x",
            params,
        )
        run_sql(
            url,
            "INSERT INTO class_session_participants (class_session_id, student_id)"
            " SELECT s.id, (CAST(:students AS uuid[]))[((s.rn - 1) % 300) + 1]"
            " FROM (SELECT id, row_number() OVER (ORDER BY starts_at, id) AS rn"
            " FROM class_sessions WHERE subject_id = :subject) AS s",
            {"students": made["students"], "subject": made["subject"][0]},
        )
        run_sql(
            url,
            "INSERT INTO class_session_attendance (class_session_id, student_id,"
            " status, marked_by_user_id, marked_at)"
            " SELECT p.class_session_id, p.student_id,"
            " (ARRAY['present', 'late', 'absent'])[(p.rn % 3) + 1], :who, p.ends_at"
            " FROM (SELECT cp.class_session_id, cp.student_id, s.ends_at,"
            " row_number() OVER (ORDER BY s.starts_at, s.id) AS rn"
            " FROM class_session_participants cp JOIN class_sessions s"
            " ON s.id = cp.class_session_id"
            " WHERE s.subject_id = :subject AND s.state = 'completed') AS p"
            " WHERE p.rn <= 8000",
            {"who": who, "subject": made["subject"][0]},
        )
        # Ledger: one manual charge and one payment per account through the
        # owners' functions, then copied by SQL to 3,000 and 2,000.
        templates_charges: list[uuid.UUID] = []
        templates_payments: list[uuid.UUID] = []
        for _ in range(30):
            _, account, [student] = rs.new_account(url)

            async def _one(session: AsyncSession, student: uuid.UUID = student) -> Any:
                return await charge(
                    session,
                    student,
                    "100.00",
                    service_date=date(2030, 5, 1),
                    due=date(2030, 5, 31),
                )

            templates_charges.append(in_uow(url, _one, actor_id=who).charge_id)
            templates_payments.append(
                ks.payment(url, who, account, "50.00", "2030-05-02")
            )
        _copy(url, "charges", templates_charges, 99, "service_date", "due_date")
        # 30 + 20 × 66 + 10 × 65 = 2,000.
        _copy(url, "payments", templates_payments[:20], 66, "received_on", None)
        _copy(url, "payments", templates_payments[20:], 65, "received_on", None)
        run_sql(url, "ANALYZE", autocommit=True)
        return made


def _columns(url: str, table: str) -> list[str]:
    rows = run_sql(
        url,
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
        " AND table_schema = 'public' AND is_generated = 'NEVER'"
        " AND column_name NOT IN ('id', 'created_at', 'updated_at')"
        " ORDER BY ordinal_position",
        {"t": table},
    )
    return [row.column_name for row in rows]


def _copy(
    url: str,
    table: str,
    templates: list[uuid.UUID],
    copies: int,
    dated: str,
    due: str | None,
) -> None:
    """``copies`` copies of each template row, ``dated`` (and ``due``)
    spread over a year."""
    columns = _columns(url, table)
    shifted = {dated: f"{dated} + ((g * 3) % 360)"}
    if due is not None:
        shifted[due] = f"{due} + ((g * 3) % 360)"
    select_list = ", ".join(shifted.get(c, c) for c in columns)
    run_sql(
        url,
        f"INSERT INTO {table} ({', '.join(columns)})"  # noqa: S608 (fixed names)
        f" SELECT {select_list} FROM {table}, generate_series(1, :copies) AS g"
        " WHERE id = ANY(CAST(:ids AS uuid[]))",
        {"copies": copies, "ids": templates},
    )


@pytest.fixture(scope="module")
def v1(module_seeded_url: str) -> dict[str, Any]:
    made = _seed_v1(module_seeded_url)
    counts = run_sql(
        module_seeded_url,
        "SELECT (SELECT count(*) FROM class_sessions) AS sessions,"
        " (SELECT count(*) FROM class_session_participants) AS participants,"
        " (SELECT count(*) FROM class_session_attendance) AS marks,"
        " (SELECT count(*) FROM charges) AS charges,"
        " (SELECT count(*) FROM payments) AS payments,"
        " (SELECT count(*) FROM class_sessions WHERE state = 'cancelled') AS cancelled",
    )[0]
    assert (
        counts.sessions,
        counts.participants,
        counts.marks,
        counts.charges,
        counts.payments,
        counts.cancelled,
    ) == (10_000, 10_000, 8_000, 3_000, 2_000, 1_000)
    return made


# --- C2 ---------------------------------------------------------------------------


def _week() -> tuple[Any, Any, Any]:
    low, high = kpis.instant_window(date(2030, 9, 2), date(2030, 9, 8), ZONE)
    return low, high, V1_NOW


STATEMENTS: dict[str, Callable[[], Any]] = {
    "k4_room_minutes": lambda: kpis._room_minutes_query(*_week()),
    "k5_retention": lambda: kpis._retention_query(
        ZONE,
        kpis.local_midnight(date(2030, 8, 26), ZONE),
        _week()[0],
        _week()[1],
        V1_NOW,
    ),
    "k6_sessions": lambda: kpis._tutor_sessions_query(*_week()),
    "k6_participations": lambda: kpis._tutor_participations_query(*_week()),
}


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_c2_session_statements_use_the_starts_at_index(module_seeded_url, v1, name):
    async def explain(session: AsyncSession) -> tuple[str, str]:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = str(
            STATEMENTS[name]().compile(
                dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
            )
        )
        rows = await session.execute(text(f"EXPLAIN {compiled}"))
        return compiled, "\n".join(row[0] for row in rows)

    compiled, plan = in_uow(module_seeded_url, explain)
    assert (
        "ix_class_sessions_starts_at" in plan
        or "ix_class_sessions_cancelled_tutor_user_id_starts_at" in plan
    ), plan
    assert "Seq Scan on class_sessions" not in plan, plan
    where = compiled.upper().split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "TIMEZONE(" not in where, compiled
    assert "DATE_TRUNC(" not in where, compiled


# --- C3 ---------------------------------------------------------------------------

BUDGETS = {
    "k1_accrual": 2.0,
    "k1_cash": 2.0,
    "k2": 2.0,
    "k4": 1.0,
    "k5": 1.0,
    "k6": 1.0,
}


def _best_of_three(url: str, call: Callable[[AsyncSession], Awaitable[Any]]) -> float:
    async def _go() -> float:
        engine = short_lived_engine(url)
        try:
            timings = []
            for run in range(4):
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    started = timer.perf_counter()
                    await call(uow.session)
                    elapsed = timer.perf_counter() - started
                if run:  # the first run warms the engine
                    timings.append(elapsed)
            return min(timings)
        finally:
            await engine.dispose()

    with frozen_clock(V1_NOW):
        return asyncio.run(_go())


def test_c3_volume_v1_budget(module_seeded_url, v1):
    url = module_seeded_url
    start, end = V1_RANGE
    assert (end - start) == timedelta(days=365)
    calls = {
        "k1_accrual": lambda s: kpis.revenue_trend(
            s, basis="accrual", start=start, end=end
        ),
        "k1_cash": lambda s: kpis.revenue_trend(s, basis="cash", start=start, end=end),
        "k2": lambda s: kpis.course_profitability(s, start=start, end=end),
        "k4": lambda s: kpis.room_utilization(s, start=start, end=end),
        "k5": lambda s: kpis.student_retention(s, start=start, end=end),
        "k6": lambda s: kpis.tutor_performance(s, start=start, end=end),
    }
    timings = {name: _best_of_three(url, call) for name, call in calls.items()}
    print(  # noqa: T201 (the C3 timings are part of the handoff)
        "C3 V1 best of 3 (s): "
        + ", ".join(f"{name} {seconds:.3f}" for name, seconds in timings.items())
    )
    with frozen_clock(V1_NOW):
        k6 = in_uow(url, lambda s: kpis.tutor_performance(s, start=start, end=end))
        k4 = in_uow(url, lambda s: kpis.room_utilization(s, start=start, end=end))
    assert k6.totals.sessions + k6.totals.cancelled == SESSIONS
    assert len(k6.tutors) == 20
    assert len(k4.rooms) == 10
    over = {n: t for n, t in timings.items() if t > BUDGETS[n]}
    assert not over, (over, timings)
