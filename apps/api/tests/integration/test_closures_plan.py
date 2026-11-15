"""The closure reads use the partial GiST index ``ix_closures_active_range``
(#204).

The index is ``WHERE is_active``; a filter written ``is_active IS true``
cannot use it. The test EXPLAINs the service's own statements (the private
builders in :mod:`app.modules.catalog.periods`), with sequential scans
disabled, on a fresh database with enough closures for the index to be worth
it.
"""

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from sqlalchemy import Select, text

from app.modules.catalog import periods
from app.modules.catalog.models import Closure
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

DAY = date(2031, 6, 15)

STATEMENTS: dict[str, Callable[[], Select]] = {
    "closures_between": lambda: periods._closures_between_query(
        DAY, DAY + timedelta(days=30)
    ),
    "is_center_closed_on": lambda: periods._closed_on_query(DAY),
}


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_an_explain_of_the_closure_read_uses_the_partial_index(seeded_url, name):
    async def many(session) -> None:
        for n in range(600):
            starts_on = date(2000, 1, 1) + timedelta(days=n * 23)
            session.add(
                Closure(
                    name=f"Cierre {n % 50}",
                    closure_type="holiday",
                    starts_on=starts_on,
                    ends_on=starts_on + timedelta(days=n % 10),
                    is_active=n % 5 != 0,
                )
            )
        await session.flush()

    in_uow(seeded_url, many)
    run_sql(seeded_url, "ANALYZE closures", autocommit=True)

    async def explain(session) -> str:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = STATEMENTS[name]().compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )
        rows = await session.execute(text(f"EXPLAIN {compiled}"))
        return "\n".join(row[0] for row in rows)

    plan = in_uow(seeded_url, explain)

    assert "ix_closures_active_range" in plan, plan
    assert "Seq Scan" not in plan, plan
