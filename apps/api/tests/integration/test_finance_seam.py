"""The finance seam and the races (#114 E, F; ADR-0017 E15).

``usable_category`` / ``usable_vendor`` (E1), their race with a deactivation on
two real connections (E2), ``category_label`` (E3), concurrent creates of one
name (F1) and two ``PATCH``es from one version (F2).

In a race, transaction A makes its change and holds its transaction open for a
moment; transaction B starts once A's change is flushed, so B meets A's
uncommitted row and its locks. A commits, then B finishes.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.finance import service as finance
from app.modules.finance.schemas import (
    ExpenseCategoryCreate,
    ExpenseCategoryUpdate,
    VendorCreate,
    VendorUpdate,
)
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.finance_support import reset, unique

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]
HOLD_SECONDS = 0.5
LABELS = {
    "rent": ("Renta", "Rent"),
    "utilities": ("Servicios", "Utilities"),
    "supplies": ("Insumos", "Supplies"),
    "equipment": ("Equipo", "Equipment"),
    "maintenance": ("Mantenimiento", "Maintenance"),
}


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    reset(module_seeded_url)


async def _outcome(engine: Any, change: Change, after: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after is not None:
                await after()
    except StaleVersionError:
        return "version_conflict"
    except AppError as exc:
        return exc.code
    return "ok"


async def race(
    url: str, first: Change, second: Change, *, while_held: Any = None
) -> list[str]:
    """Outcomes of ``first`` (A) and ``second`` (B, started once A's change is
    flushed and still uncommitted). ``while_held`` runs at the end of A's hold,
    before A commits."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    changed = asyncio.Event()

    async def hold() -> None:
        changed.set()
        await asyncio.sleep(HOLD_SECONDS)
        if while_held is not None:
            await while_held()

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


async def _new_category(url: str, name: str | None = None) -> uuid.UUID:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            item = await finance.add_category(
                uow.session, ExpenseCategoryCreate(name=name or unique("Cat"))
            )
    finally:
        await engine.dispose()
    return item.id


async def _new_vendor(url: str) -> uuid.UUID:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            item = await finance.add_vendor(
                uow.session, VendorCreate(name=unique("Proveedor"))
            )
    finally:
        await engine.dispose()
    return item.id


async def _active(url: str, table: str, row_id: uuid.UUID) -> bool:
    [row] = await run_sql_async(
        url,
        f"SELECT is_active FROM {table} WHERE id = :id",  # noqa: S608
        {"id": str(row_id)},
    )
    return bool(row.is_active)


async def _seeded(url: str, key: str) -> uuid.UUID:
    [row] = await run_sql_async(
        url, "SELECT id FROM expense_categories WHERE system_key = :k", {"k": key}
    )
    return uuid.UUID(str(row.id))


# --- E1: usable -----


async def test_e1_usable_category_returns_an_active_row(module_seeded_url):
    url = module_seeded_url
    rent = await _seeded(url, "rent")

    async def use(session: AsyncSession) -> tuple[Any, ...]:
        row = await finance.usable_category(session, rent)
        return row.id, row.name, row.system_key

    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            assert await use(uow.session) == (rent, "Renta", "rent")
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("which", "missing", "inactive"),
    [
        (
            "category",
            (422, "expense_category_unknown"),
            (409, "expense_category_inactive"),
        ),
        ("vendor", (422, "vendor_unknown"), (409, "vendor_inactive")),
    ],
)
async def test_e1_unknown_is_422_and_inactive_409(
    module_seeded_url, which, missing, inactive
):
    url = module_seeded_url
    if which == "category":
        row_id = await _new_category(url)
        usable = finance.usable_category
        deactivate: Change = lambda s: finance.change_category(  # noqa: E731
            s, row_id, ExpenseCategoryUpdate(version=1, is_active=False)
        )
    else:
        row_id = await _new_vendor(url)
        usable = finance.usable_vendor
        deactivate = lambda s: finance.change_vendor(  # noqa: E731
            s, row_id, VendorUpdate(version=1, is_active=False)
        )

    async def attempt(target: uuid.UUID) -> tuple[int, str]:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await usable(uow.session, target)
        except AppError as exc:
            return exc.status, exc.code
        finally:
            await engine.dispose()
        return 200, "ok"

    assert await attempt(row_id) == (200, "ok")
    assert await attempt(uuid.uuid4()) == missing
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await deactivate(uow.session)
    finally:
        await engine.dispose()
    assert await attempt(row_id) == inactive


# --- E2: the race with a deactivation -----


@pytest.mark.parametrize("which", ["category", "vendor"])
async def test_e2_a_deactivation_first_makes_the_reader_refuse(
    module_seeded_url, which
):
    url = module_seeded_url
    if which == "category":
        row_id = await _new_category(url)
        first: Change = lambda s: finance.change_category(  # noqa: E731
            s, row_id, ExpenseCategoryUpdate(version=1, is_active=False)
        )
        second: Change = lambda s: finance.usable_category(s, row_id)  # noqa: E731
        refused = "expense_category_inactive"
    else:
        row_id = await _new_vendor(url)
        first = lambda s: finance.change_vendor(  # noqa: E731
            s, row_id, VendorUpdate(version=1, is_active=False)
        )
        second = lambda s: finance.usable_vendor(s, row_id)  # noqa: E731
        refused = "vendor_inactive"

    assert await race(url, first, second) == ["ok", refused]


@pytest.mark.parametrize("which", ["category", "vendor"])
async def test_e2_a_reader_first_makes_the_deactivation_wait_for_its_commit(
    module_seeded_url, which
):
    url = module_seeded_url
    table = "expense_categories" if which == "category" else "vendors"
    if which == "category":
        row_id = await _new_category(url)
        first: Change = lambda s: finance.usable_category(s, row_id)  # noqa: E731
        second: Change = lambda s: finance.change_category(  # noqa: E731
            s, row_id, ExpenseCategoryUpdate(version=1, is_active=False)
        )
    else:
        row_id = await _new_vendor(url)
        first = lambda s: finance.usable_vendor(s, row_id)  # noqa: E731
        second = lambda s: finance.change_vendor(  # noqa: E731
            s, row_id, VendorUpdate(version=1, is_active=False)
        )
    seen_while_held: list[bool] = []

    async def still_active() -> None:
        seen_while_held.append(await _active(url, table, row_id))

    outcomes = await race(url, first, second, while_held=still_active)

    assert outcomes == ["ok", "ok"]
    # B's deactivation was blocked by A's FOR SHARE until A committed.
    assert seen_while_held == [True]
    assert await _active(url, table, row_id) is False


# --- E3: labels -----


@pytest.mark.parametrize("key", sorted(LABELS))
async def test_e3_a_keyed_row_shows_the_catalog_label(key):
    row = SimpleNamespace(name="ignored", system_key=key)

    assert (
        finance.category_label(row, "es"),
        finance.category_label(row, "en"),
    ) == LABELS[key]


async def test_e3_after_a_rename_the_stored_name_shows_in_both_languages(
    module_seeded_url,
):
    url = module_seeded_url
    rent = await _seeded(url, "rent")
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            row = await finance.usable_category(uow.session, rent)
            assert (
                finance.category_label(row, "es"),
                finance.category_label(row, "en"),
            ) == ("Renta", "Rent")
            await finance.change_category(
                uow.session, rent, ExpenseCategoryUpdate(version=1, name="Alquiler")
            )
            row = await finance.usable_category(uow.session, rent)
            assert (
                finance.category_label(row, "es"),
                finance.category_label(row, "en"),
            ) == ("Alquiler", "Alquiler")
    finally:
        await engine.dispose()


# --- F1, F2: concurrency -----


async def test_f1_two_creates_of_one_category_name_give_one_row_and_one_409(
    module_seeded_url,
):
    url = module_seeded_url
    name = unique("Gas")

    outcomes = await race(
        url,
        lambda s: finance.add_category(s, ExpenseCategoryCreate(name=name)),
        lambda s: finance.add_category(s, ExpenseCategoryCreate(name=name.upper())),
    )

    assert outcomes == ["ok", "expense_category_exists"]
    rows = await run_sql_async(
        url,
        "SELECT name FROM expense_categories WHERE lower(name) = lower(:n)",
        {"n": name},
    )
    assert [row.name for row in rows] == [name]


async def test_f1_two_creates_of_one_vendor_name_give_one_row_and_one_409(
    module_seeded_url,
):
    url = module_seeded_url
    name = unique("Acme")

    outcomes = await race(
        url,
        lambda s: finance.add_vendor(s, VendorCreate(name=name)),
        lambda s: finance.add_vendor(s, VendorCreate(name=name.lower())),
    )

    assert outcomes == ["ok", "vendor_exists"]
    rows = await run_sql_async(url, "SELECT name FROM vendors")
    assert [row.name for row in rows] == [name]


async def test_f2_two_category_patches_from_one_version_give_one_200_and_one_409(
    module_seeded_url,
):
    url = module_seeded_url
    row_id = await _new_category(url)

    outcomes = await race(
        url,
        lambda s: finance.change_category(
            s, row_id, ExpenseCategoryUpdate(version=1, name=unique("Primero"))
        ),
        lambda s: finance.change_category(
            s, row_id, ExpenseCategoryUpdate(version=1, is_active=False)
        ),
    )

    assert outcomes == ["ok", "version_conflict"]
    [row] = await run_sql_async(
        url,
        "SELECT is_active, version FROM expense_categories WHERE id = :id",
        {"id": str(row_id)},
    )
    assert tuple(row) == (True, 2)


async def test_f2_two_vendor_patches_from_one_version_give_one_200_and_one_409(
    module_seeded_url,
):
    url = module_seeded_url
    row_id = await _new_vendor(url)

    outcomes = await race(
        url,
        lambda s: finance.change_vendor(
            s, row_id, VendorUpdate(version=1, contact="55")
        ),
        lambda s: finance.change_vendor(
            s, row_id, VendorUpdate(version=1, is_active=False)
        ),
    )

    assert outcomes == ["ok", "version_conflict"]
    [row] = await run_sql_async(
        url,
        "SELECT contact, is_active, version FROM vendors WHERE id = :id",
        {"id": str(row_id)},
    )
    assert tuple(row) == ("55", True, 2)
