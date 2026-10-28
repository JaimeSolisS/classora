"""The catalog price lookup (#89 C1-C4, E2; #86 E9) on real PostgreSQL.

One test per rule of ``app.modules.billing.pricing``: the ranks (scoped,
exact level and duration, exact level, exact duration, any), "the most
specific candidate with a version in effect decides", the ``NULL`` rule
without fall-through, the version boundaries, the ``ValueError`` cases and
the single statement. Every test builds its own subject, so the module
database is shared safely.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.catalog.models import EducationLevel
from tests.factories.billing import persist_service_price
from tests.factories.courses import persist_course_cohort
from tests.factories.scheduling import persist_class_group, persist_subject
from tests.integration.catalog_support import count_statements
from tests.integration.lifecycle_support import in_uow

ON = date(2031, 3, 10)
EARLIER = date(2031, 1, 1)
LATER = date(2031, 6, 1)


@dataclass(frozen=True)
class World:
    subject: uuid.UUID
    level: uuid.UUID
    other_level: uuid.UUID
    cohort: uuid.UUID
    group: uuid.UUID


@pytest.fixture
def world(module_seeded_url: str) -> World:
    async def _make(session: AsyncSession) -> World:
        subject = await persist_subject(session)
        suffix = uuid.uuid4().hex[:8]
        level = EducationLevel(name=f"Nivel A {suffix}", sort_order=20)
        other = EducationLevel(name=f"Nivel B {suffix}", sort_order=30)
        session.add_all([level, other])
        await session.flush()
        cohort = await persist_course_cohort(session, subject_id=subject.id)
        group = await persist_class_group(session, subject_id=subject.id)
        return World(subject.id, level.id, other.id, cohort.id, group.id)

    return in_uow(module_seeded_url, _make)


def price(
    url: str, world: World, *versions: tuple[date, str | None], **key: Any
) -> Any:
    async def _make(session: AsyncSession) -> Any:
        return await persist_service_price(
            session, subject_id=world.subject, versions=versions, **key
        )

    return in_uow(url, _make)


def lookup(url: str, world: World, **query: Any) -> billing.PriceInEffect | None:
    values: dict[str, Any] = {
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": world.subject,
        "on": ON,
        **query,
    }

    async def _lookup(session: AsyncSession) -> billing.PriceInEffect | None:
        return await billing.lookup_price(session, **values)

    return in_uow(url, _lookup)


def amount_of(found: billing.PriceInEffect | None) -> tuple[str, int] | None:
    return None if found is None else (str(found.amount), found.rank)


def test_nothing_priced_is_none(module_seeded_url, world):
    assert lookup(module_seeded_url, world) is None


def test_an_any_level_price_is_rank_4(module_seeded_url, world):
    price(module_seeded_url, world, (EARLIER, "300.00"))

    assert amount_of(lookup(module_seeded_url, world)) == ("300.00", 4)
    assert amount_of(
        lookup(
            module_seeded_url,
            world,
            education_level_id=world.level,
            duration_minutes=60,
        )
    ) == ("300.00", 4)


def test_the_exact_level_beats_any_level(module_seeded_url, world):
    url = module_seeded_url
    price(url, world, (EARLIER, "300.00"))
    price(url, world, (EARLIER, "350.00"), education_level_id=world.level)

    assert amount_of(lookup(url, world, education_level_id=world.level)) == (
        "350.00",
        2,
    )
    assert amount_of(lookup(url, world, education_level_id=world.other_level)) == (
        "300.00",
        4,
    )


def test_the_exact_duration_beats_any_duration(module_seeded_url, world):
    url = module_seeded_url
    price(url, world, (EARLIER, "300.00"))
    price(url, world, (EARLIER, "200.00"), duration_minutes=45)
    price(url, world, (EARLIER, "350.00"), education_level_id=world.level)
    price(
        url,
        world,
        (EARLIER, "260.00"),
        education_level_id=world.level,
        duration_minutes=45,
    )

    exact = lookup(url, world, education_level_id=world.level, duration_minutes=45)
    assert amount_of(exact) == ("260.00", 1)
    assert amount_of(
        lookup(url, world, education_level_id=world.other_level, duration_minutes=45)
    ) == ("200.00", 3)
    assert amount_of(
        lookup(url, world, education_level_id=world.level, duration_minutes=90)
    ) == ("350.00", 2)


def test_the_exact_level_with_any_duration_beats_any_level_with_the_duration(
    module_seeded_url, world
):
    url = module_seeded_url
    price(url, world, (EARLIER, "200.00"), duration_minutes=45)
    price(url, world, (EARLIER, "350.00"), education_level_id=world.level)

    assert amount_of(
        lookup(url, world, education_level_id=world.level, duration_minutes=45)
    ) == ("350.00", 2)


def test_a_cohort_price_beats_the_catalog(module_seeded_url, world):
    url = module_seeded_url
    course = {"class_type": "course", "pricing_model": "full_course"}
    price(url, world, (EARLIER, "5000.00"), **course)
    price(url, world, (EARLIER, "5100.00"), education_level_id=world.level, **course)
    price(url, world, (EARLIER, "4500.00"), cohort_id=world.cohort, **course)

    assert amount_of(
        lookup(
            url, world, cohort_id=world.cohort, education_level_id=world.level, **course
        )
    ) == ("4500.00", 0)
    assert amount_of(
        lookup(
            url, world, cohort_id=uuid.uuid4(), education_level_id=world.level, **course
        )
    ) == ("5100.00", 2)


@pytest.mark.parametrize("model", ["per_session", "full_course"])
def test_a_group_price_beats_the_catalog(module_seeded_url, world, model):
    url = module_seeded_url
    group = {"class_type": "group", "pricing_model": model}
    price(url, world, (EARLIER, "180.00"), **group)
    price(url, world, (EARLIER, "150.00"), group_id=world.group, **group)

    assert amount_of(lookup(url, world, group_id=world.group, **group)) == (
        "150.00",
        0,
    )
    assert amount_of(lookup(url, world, **group)) == ("180.00", 4)


def test_a_scoped_price_that_starts_later_is_skipped(module_seeded_url, world):
    url = module_seeded_url
    group = {"class_type": "group", "pricing_model": "per_session"}
    price(url, world, (EARLIER, "180.00"), **group)
    price(url, world, (LATER, "150.00"), group_id=world.group, **group)

    assert amount_of(lookup(url, world, group_id=world.group, **group)) == (
        "180.00",
        4,
    )
    assert amount_of(lookup(url, world, group_id=world.group, on=LATER, **group)) == (
        "150.00",
        0,
    )


def test_the_version_boundaries(module_seeded_url, world):
    url = module_seeded_url
    item = price(url, world, (EARLIER, "300.00"), (LATER, "320.00"))

    before = lookup(url, world, on=LATER - timedelta(days=1))
    assert amount_of(before) == ("300.00", 4)
    assert before is not None and before.effective_from == EARLIER
    on_the_day = lookup(url, world, on=LATER)
    assert amount_of(on_the_day) == ("320.00", 4)
    assert on_the_day is not None and on_the_day.service_price_id == item.id
    assert amount_of(lookup(url, world, on=date(2099, 12, 31))) == ("320.00", 4)
    assert lookup(url, world, on=EARLIER - timedelta(days=1)) is None


def test_not_offered_at_the_deciding_rank_never_falls_through(module_seeded_url, world):
    url = module_seeded_url
    price(url, world, (EARLIER, "300.00"))
    price(
        url,
        world,
        (EARLIER, "350.00"),
        (ON, None),
        (LATER, "360.00"),
        education_level_id=world.level,
    )

    level = {"education_level_id": world.level}
    assert lookup(url, world, **level) is None
    assert amount_of(lookup(url, world, on=ON - timedelta(days=1), **level)) == (
        "350.00",
        2,
    )
    assert amount_of(lookup(url, world, on=LATER, **level)) == ("360.00", 2)
    assert amount_of(lookup(url, world, education_level_id=world.other_level)) == (
        "300.00",
        4,
    )


def test_no_level_matches_only_any_level_prices(module_seeded_url, world):
    url = module_seeded_url
    price(url, world, (EARLIER, "350.00"), education_level_id=world.level)

    assert lookup(url, world) is None
    price(url, world, (EARLIER, "300.00"))
    assert amount_of(lookup(url, world)) == ("300.00", 4)


def test_no_duration_matches_only_any_duration_prices(module_seeded_url, world):
    url = module_seeded_url
    price(url, world, (EARLIER, "200.00"), duration_minutes=45)

    assert lookup(url, world) is None
    assert lookup(url, world, education_level_id=world.level) is None
    price(url, world, (EARLIER, "300.00"))
    assert amount_of(lookup(url, world)) == ("300.00", 4)


def test_the_result_carries_money_and_the_version(module_seeded_url, world):
    url = module_seeded_url
    item = price(url, world, (EARLIER, "0.00"))

    found = lookup(url, world)

    assert found is not None
    assert isinstance(found.amount, Money)
    assert found.amount == Money("0.00")
    assert found.service_price_id == item.id
    [version_id] = [
        row.id
        for row in in_uow(url, lambda s: billing.list_versions(s, item.id, today=ON))
    ]
    assert found.price_version_id == version_id
    assert found.effective_from == EARLIER


@pytest.mark.parametrize(
    "query",
    [
        pytest.param({"pricing_model": "package"}, id="package"),
        pytest.param({"cohort_id": uuid.uuid4()}, id="cohort-on-individual"),
        pytest.param(
            {"class_type": "group", "cohort_id": uuid.uuid4()}, id="cohort-on-group"
        ),
        pytest.param({"group_id": uuid.uuid4()}, id="group-on-individual"),
        pytest.param(
            {"class_type": "course", "group_id": uuid.uuid4()}, id="group-on-course"
        ),
        pytest.param(
            {
                "class_type": "group",
                "group_id": uuid.uuid4(),
                "cohort_id": uuid.uuid4(),
            },
            id="both-scopes",
        ),
    ],
)
def test_programming_errors_raise_value_error(module_seeded_url, world, query):
    with pytest.raises(ValueError):
        lookup(module_seeded_url, world, **query)


def test_the_lookup_is_one_statement(module_seeded_url, world):
    url = module_seeded_url
    group = {"class_type": "group", "pricing_model": "per_session"}
    price(url, world, (EARLIER, "180.00"), **group)
    price(url, world, (EARLIER, "190.00"), education_level_id=world.level, **group)
    price(url, world, (EARLIER, "150.00"), group_id=world.group, **group)

    count, found = count_statements(
        url,
        lambda session: billing.lookup_price(
            session,
            subject_id=world.subject,
            on=ON,
            education_level_id=world.level,
            duration_minutes=60,
            group_id=world.group,
            **group,
        ),
    )

    assert count == 1
    assert amount_of(found) == ("150.00", 0)
