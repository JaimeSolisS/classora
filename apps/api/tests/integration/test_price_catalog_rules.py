"""Price catalog constraints, the guard and the version race (#89 E1, E5,
E6) on real PostgreSQL.

- **E1:** the version key (no two versions of an item on one date), the
  catalog key with ``NULLS NOT DISTINCT`` (and ``package`` rows outside it),
  each ``service_prices`` CHECK, ``amount >= 0``. PostgreSQL checks CHECKs
  in name order, so ``one_scope`` (implied by ``cohort_scope`` and
  ``group_scope``) and ``pricing_model_valid`` (implied by
  ``pricing_model_allowed``) may be reported under the other name.
- **E6:** an unreferenced version can be updated and deleted at the SQL
  level; ``TRUNCATE`` is always refused; with the predicate answering
  ``true`` both are refused (42501).
- **E5:** two connections add a version to the same item at once. The item
  row lock serializes them: the second sees the first's version, so a date
  that is not later is ``422``.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.models import PriceVersion
from tests.conftest import frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.courses import persist_course_cohort
from tests.factories.scheduling import persist_class_group, persist_subject
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow
from tests.integration.price_catalog_support import every_version_referenced

TODAY = date(2031, 3, 10)
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZoneInfo("America/Mexico_City"))
HOLD_SECONDS = 0.5
REPEATS = 3


def _references(url: str) -> dict[str, uuid.UUID]:
    async def _make(session: AsyncSession) -> dict[str, uuid.UUID]:
        subject = await persist_subject(session)
        return {
            "subject": subject.id,
            "level": subject.education_level_id,
            "cohort": (await persist_course_cohort(session, subject_id=subject.id)).id,
            "group": (await persist_class_group(session, subject_id=subject.id)).id,
        }

    return in_uow(url, _make)


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


# --- E1: constraints -----------------------------------------------------------------


def test_two_versions_of_an_item_never_share_a_date(module_seeded_url):
    refs = _references(module_seeded_url)
    item = in_uow(
        module_seeded_url,
        lambda s: persist_service_price(
            s, subject_id=refs["subject"], versions=[(TODAY, "100.00")]
        ),
    )

    async def _again(session: AsyncSession) -> None:
        session.add(
            PriceVersion(
                service_price_id=item.id, effective_from=TODAY, amount=Money("1.00")
            )
        )
        await session.flush()

    with pytest.raises(
        IntegrityError, match="uq_price_versions_service_price_id_effective_from"
    ):
        in_uow(module_seeded_url, _again)


def test_the_catalog_key_treats_nulls_as_equal(module_seeded_url):
    url = module_seeded_url
    refs = _references(url)

    def add(**key: Any) -> Any:
        return in_uow(
            url, lambda s: persist_service_price(s, subject_id=refs["subject"], **key)
        )

    add()
    with pytest.raises(IntegrityError, match="uq_service_prices_catalog_key"):
        add()
    # A level, a duration or a scope is another key.
    add(education_level_id=refs["level"])
    add(duration_minutes=60)
    add(class_type="group")
    add(class_type="group", group_id=refs["group"])
    with pytest.raises(IntegrityError, match="uq_service_prices_catalog_key"):
        add(class_type="group", group_id=refs["group"])
    add(class_type="course", pricing_model="full_course", cohort_id=refs["cohort"])
    # Package rows are outside the key (identified by their product, #94).
    add(pricing_model="package")
    add(pricing_model="package")
    in_uow(
        url,
        lambda s: persist_service_price(s, subject_id=None, pricing_model="package"),
    )
    in_uow(
        url,
        lambda s: persist_service_price(s, subject_id=None, pricing_model="package"),
    )


PRICE_INSERT = (
    "INSERT INTO service_prices (class_type, pricing_model, subject_id,"
    " education_level_id, duration_minutes, cohort_id, group_id, period_frequency)"
    " VALUES (:class_type, :pricing_model, :subject_id, :education_level_id,"
    " :duration_minutes, :cohort_id, :group_id, :period_frequency)"
)


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        pytest.param({"class_type": "tutoring"}, "class_type_valid", id="class-type"),
        pytest.param(
            {"pricing_model": "hourly"},
            "pricing_model_(allowed|valid)",
            id="model",
        ),
        pytest.param(
            {"class_type": "course", "pricing_model": "per_session"},
            "pricing_model_allowed",
            id="course-per-session",
        ),
        pytest.param(
            {"pricing_model": "full_course"},
            "pricing_model_allowed",
            id="individual-full-course",
        ),
        pytest.param(
            {
                "class_type": "course",
                "pricing_model": "per_period",
                "period_frequency": "monthly",
            },
            "pricing_model_allowed",
            id="course-per-period",
        ),
        pytest.param(
            {"pricing_model": "per_period"},
            "period_frequency",
            id="per-period-without-frequency",
        ),
        pytest.param(
            {"period_frequency": "weekly"},
            "period_frequency",
            id="frequency-on-per-session",
        ),
        pytest.param(
            {"pricing_model": "per_period", "period_frequency": "daily"},
            "period_frequency",
            id="unknown-frequency",
        ),
        pytest.param(
            {"class_type": "group", "pricing_model": "package"},
            "package_individual",
            id="group-package",
        ),
        pytest.param(
            {
                "class_type": "course",
                "pricing_model": "full_course",
                "cohort_id": "cohort",
                "group_id": "group",
            },
            "(group_scope|one_scope)",
            id="both-scopes",
        ),
        pytest.param(
            {"class_type": "group", "cohort_id": "cohort"},
            "cohort_scope",
            id="cohort-on-group",
        ),
        pytest.param({"group_id": "group"}, "group_scope", id="group-on-individual"),
        pytest.param({"subject_id": None}, "subject_required", id="no-subject"),
        pytest.param({"duration_minutes": 14}, "duration_valid", id="duration-14"),
        pytest.param({"duration_minutes": 481}, "duration_valid", id="duration-481"),
        pytest.param(
            {
                "class_type": "group",
                "pricing_model": "per_period",
                "duration_minutes": 60,
            },
            "duration_valid",
            id="duration-per-period",
        ),
        pytest.param(
            {"class_type": "group", "group_id": "group", "education_level_id": "level"},
            "scoped_any_level_duration",
            id="scoped-with-level",
        ),
        pytest.param(
            {"class_type": "group", "group_id": "group", "duration_minutes": 60},
            "scoped_any_level_duration",
            id="scoped-with-duration",
        ),
    ],
)
def test_each_check_rejects_a_bad_row(module_seeded_url, row, constraint):
    refs = _references(module_seeded_url)
    values = {
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": refs["subject"],
        "education_level_id": None,
        "duration_minutes": None,
        "cohort_id": None,
        "group_id": None,
        "period_frequency": None,
        **{
            k: refs.get(v, v) if isinstance(v, str) and k.endswith("_id") else v
            for k, v in row.items()
        },
    }

    with pytest.raises(IntegrityError, match=f"ck_service_prices_{constraint}"):
        run_sql(module_seeded_url, PRICE_INSERT, values)


def test_a_negative_amount_is_rejected(module_seeded_url):
    refs = _references(module_seeded_url)

    with pytest.raises(IntegrityError, match="ck_price_versions_amount_non_negative"):
        in_uow(
            module_seeded_url,
            lambda s: persist_service_price(
                s, subject_id=refs["subject"], versions=[(TODAY, "-0.01")]
            ),
        )


# --- E6: the guard -------------------------------------------------------------------


def _version(url: str) -> uuid.UUID:
    refs = _references(url)
    item = in_uow(
        url,
        lambda s: persist_service_price(
            s, subject_id=refs["subject"], versions=[(TODAY, "100.00")]
        ),
    )
    [row] = run_sql(
        url, "SELECT id FROM price_versions WHERE service_price_id = :p", {"p": item.id}
    )
    return uuid.UUID(str(row.id))


def test_an_unreferenced_version_can_be_corrected_or_removed(module_seeded_url):
    url = module_seeded_url
    version = _version(url)

    run_sql(url, "UPDATE price_versions SET amount = 120 WHERE id = :v", {"v": version})
    [row] = run_sql(
        url, "SELECT amount FROM price_versions WHERE id = :v", {"v": version}
    )
    assert str(row.amount) == "120.00"
    run_sql(url, "DELETE FROM price_versions WHERE id = :v", {"v": version})
    assert (
        run_sql(url, "SELECT id FROM price_versions WHERE id = :v", {"v": version})
        == []
    )


def test_truncate_is_refused(module_seeded_url):
    _version(module_seeded_url)

    with pytest.raises(DBAPIError) as raised:
        # Since #90 ``charges`` references the table, so a TRUNCATE must
        # name the referencing tables too; price_versions comes first, so
        # its own trigger is the one that refuses it. Since #95
        # charge_discounts references charges too; since #94 packages
        # reference price_versions and package_credit_transactions packages.
        run_sql(
            module_seeded_url,
            "TRUNCATE price_versions, charges, charge_adjustments,"
            " payment_allocations, allocation_releases, charge_discounts,"
            " packages, package_credit_transactions",
        )

    assert _sqlstate(raised.value) == "42501"
    assert "never truncated" in str(raised.value.orig)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE price_versions SET amount = 1 WHERE id = :v",
        "UPDATE price_versions SET effective_from = '2040-01-01' WHERE id = :v",
        "DELETE FROM price_versions WHERE id = :v",
    ],
)
def test_a_referenced_version_never_changes(module_seeded_url, statement):
    url = module_seeded_url
    version = _version(url)

    with every_version_referenced(url), pytest.raises(DBAPIError) as raised:
        run_sql(url, statement, {"v": version})

    assert _sqlstate(raised.value) == "42501"
    assert "price_versions row is referenced" in str(raised.value.orig)
    [row] = run_sql(
        url,
        "SELECT amount, effective_from FROM price_versions WHERE id = :v",
        {"v": version},
    )
    assert (str(row.amount), row.effective_from) == ("100.00", TODAY)
    # The original predicate is back: the version can change again.
    run_sql(url, "DELETE FROM price_versions WHERE id = :v", {"v": version})


# --- E5: the version race ------------------------------------------------------------

Change = Callable[[AsyncSession], Awaitable[Any]]


async def _outcome(engine: Any, change: Change, after: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after is not None:
                await after()
    except billing.PriceEffectiveFromInvalidError:
        return "422"
    return "201"


async def _race(url: str, first: Change, second: Change) -> list[str]:
    """Outcomes of ``first`` (A) and ``second`` (B, started once A's version
    is flushed and uncommitted)."""
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


def _add(item_id: uuid.UUID, on: date) -> Change:
    async def _change(session: AsyncSession) -> None:
        await billing.add_price_version(
            session, item_id, effective_from=on, amount=Money("10.00"), today=TODAY
        )

    return _change


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param(date(2031, 5, 1), date(2031, 6, 1), ["201", "201"], id="later"),
        pytest.param(date(2031, 6, 1), date(2031, 5, 1), ["201", "422"], id="earlier"),
        pytest.param(date(2031, 6, 1), date(2031, 6, 1), ["201", "422"], id="same"),
    ],
)
def test_concurrent_versions_follow_the_latest(
    module_seeded_url, first, second, expected
):
    url = module_seeded_url
    for _ in range(REPEATS):
        refs = _references(url)
        item = in_uow(
            url,
            lambda s, refs=refs: persist_service_price(
                s, subject_id=refs["subject"], versions=[(date(2031, 1, 1), "9.00")]
            ),
        )

        with frozen_clock(NOW):
            outcomes = asyncio.run(
                _race(url, _add(item.id, first), _add(item.id, second))
            )

        assert outcomes == expected
        dates = [
            row.effective_from
            for row in run_sql(
                url,
                "SELECT effective_from FROM price_versions WHERE service_price_id = :p"
                " ORDER BY created_at, id",
                {"p": item.id},
            )
        ]
        assert dates == sorted(dates)
        assert len(dates) == 1 + expected.count("201")


def test_per_period_items_differing_only_by_frequency_coexist(module_seeded_url):
    # #93 A2, K2: the frequency is part of the catalog key.
    refs = _references(module_seeded_url)
    values = {
        "class_type": "individual",
        "pricing_model": "per_period",
        "subject_id": refs["subject"],
        "education_level_id": None,
        "duration_minutes": None,
        "cohort_id": None,
        "group_id": None,
    }
    run_sql(module_seeded_url, PRICE_INSERT, {**values, "period_frequency": "weekly"})
    run_sql(module_seeded_url, PRICE_INSERT, {**values, "period_frequency": "monthly"})

    with pytest.raises(IntegrityError, match="uq_service_prices_catalog_key"):
        run_sql(
            module_seeded_url, PRICE_INSERT, {**values, "period_frequency": "weekly"}
        )
