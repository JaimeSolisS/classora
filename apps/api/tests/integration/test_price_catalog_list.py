"""The price catalog list (#89 B2): the human order, the filters, the
pagination, ``current``/``next``, hidden ``package``/``per_period`` rows
and the statement budget (the same for 1 and 13 items), on a database of
its own; the query ``422``s through the real app.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import OffsetParams
from app.modules.catalog.models import EducationLevel, Subject
from app.modules.identity.permissions import ADMINISTRATOR
from app.workflows import price_catalog
from tests.conftest import frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.courses import persist_course_cohort
from tests.factories.scheduling import persist_class_group
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
PAST = date(2031, 1, 1)
CURRENT = date(2031, 3, 1)
SOON = date(2031, 4, 1)
LATER = date(2031, 5, 1)
PRICES = "/api/v1/billing/service-prices"


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    with frozen_clock(NOW):
        yield


def _catalog(url: str) -> dict[str, Any]:
    """Thirteen listed items, numbered in their expected order, plus a
    ``package`` and a ``per_period`` row; names chosen so that the level
    order and the name order disagree."""

    async def _make(session: AsyncSession) -> dict[str, Any]:
        primary = EducationLevel(name="Primaria", sort_order=10)
        secondary = EducationLevel(name="Bachillerato", sort_order=20)
        session.add_all([primary, secondary])
        await session.flush()
        biology = Subject(education_level_id=primary.id, name="Biología")
        calculus = Subject(education_level_id=primary.id, name="cálculo")
        algebra = Subject(education_level_id=secondary.id, name="Álgebra")
        session.add_all([biology, calculus, algebra])
        await session.flush()
        zeta = await persist_class_group(session, subject_id=biology.id)
        zeta.name = "Zeta"
        alfa = await persist_class_group(session, subject_id=biology.id)
        alfa.name = "alfa"
        cohort = await persist_course_cohort(session, subject_id=biology.id)
        await session.flush()

        async def item(number: int, **key: Any) -> tuple[int, uuid.UUID]:
            key.setdefault("subject_id", biology.id)
            versions = key.pop("versions", [(CURRENT, f"{number}.00")])
            row = await persist_service_price(session, versions=versions, **key)
            return number, row.id

        group = {"class_type": "group"}
        course = {"class_type": "course", "pricing_model": "full_course"}
        numbered = dict(
            [
                await item(13, cohort_id=cohort.id, **course),
                await item(5, education_level_id=secondary.id),
                await item(
                    1,
                    versions=[
                        (PAST, "1.00"),
                        (CURRENT, "1.50"),
                        (SOON, None),
                        (LATER, "2.00"),
                    ],
                ),
                await item(10, group_id=zeta.id, **group),
                await item(3, duration_minutes=90),
                await item(7, subject_id=algebra.id),
                await item(9, group_id=alfa.id, **group),
                await item(2, duration_minutes=45, versions=[(SOON, "2.00")]),
                await item(4, education_level_id=primary.id, duration_minutes=60),
                await item(11, pricing_model="full_course", **group),
                await item(6, subject_id=calculus.id),
                await item(12, **course),
                await item(8, **group),
            ]
        )
        await persist_service_price(
            session, subject_id=biology.id, pricing_model="package"
        )
        await persist_service_price(
            session, subject_id=None, pricing_model="package", versions=[(PAST, "9.00")]
        )
        await persist_service_price(
            session, subject_id=biology.id, pricing_model="per_period"
        )
        return {
            "ids": numbered,
            "biology": biology.id,
            "algebra": algebra.id,
        }

    return in_uow(url, _make)


def listed(url: str, *, limit: int = 100, offset: int = 0, **filters: Any) -> Any:
    query = {"class_type": None, "pricing_model": None, "subject_id": None, **filters}
    return in_uow(
        url,
        lambda s: price_catalog.list_service_prices(
            s, page=OffsetParams(limit=limit, offset=offset), **query
        ),
    )


def numbers(catalog: dict[str, Any], page: Any) -> list[int]:
    by_id = {item_id: number for number, item_id in catalog["ids"].items()}
    return [by_id[item.id] for item in page.items]


def test_the_list_order_filters_and_pages(head_url):
    catalog = _catalog(head_url)

    page = listed(head_url)
    assert numbers(catalog, page) == list(range(1, 14))
    assert page.total == 13

    assert numbers(catalog, listed(head_url, class_type="group")) == [8, 9, 10, 11]
    assert numbers(catalog, listed(head_url, pricing_model="full_course")) == [
        11,
        12,
        13,
    ]
    assert numbers(catalog, listed(head_url, subject_id=catalog["algebra"])) == [7]
    unknown = listed(head_url, subject_id=uuid.uuid4())
    assert unknown.items == [] and unknown.total == 0

    second = listed(head_url, limit=5, offset=10)
    assert numbers(catalog, second) == [11, 12, 13]
    assert (second.total, second.limit, second.offset) == (13, 5, 10)
    past_the_end = listed(head_url, limit=5, offset=20)
    assert past_the_end.items == [] and past_the_end.total == 13


def test_items_carry_current_and_next(head_url):
    catalog = _catalog(head_url)

    items = {item.id: item for item in listed(head_url).items}

    first = items[catalog["ids"][1]]
    assert first.current is not None and first.next is not None
    assert (first.current.effective_from, str(first.current.amount)) == (
        CURRENT,
        "1.50",
    )
    assert first.current.status == "current"
    # The earliest upcoming version, a "not offered" one.
    assert (first.next.effective_from, first.next.amount) == (SOON, None)
    assert first.next.status == "upcoming"
    second = items[catalog["ids"][2]]
    assert second.current is None
    assert second.next is not None and second.next.effective_from == SOON
    assert items[catalog["ids"][12]].next is None


def test_the_statement_budget_does_not_grow_with_the_page(head_url):
    _catalog(head_url)

    def page(limit: int) -> Any:
        return lambda session: price_catalog.list_service_prices(
            session,
            class_type=None,
            pricing_model=None,
            subject_id=None,
            page=OffsetParams(limit=limit, offset=0),
        )

    one, found_one = count_statements(head_url, page(1))
    many, found_many = count_statements(head_url, page(100))

    assert len(found_one.items) == 1 and len(found_many.items) == 13
    # The zone, the page with its total, the versions.
    assert one == many == 3


@pytest.mark.parametrize(
    "query",
    [
        {"class_type": "tutoring"},
        {"pricing_model": "package"},
        {"pricing_model": "per_period"},
        {"subject_id": "abc"},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_bad_queries_are_422(module_seeded_url, query):
    with auth_client(module_seeded_url) as client:
        cookie = sign_in(
            client, create_user(module_seeded_url, roles=(ADMINISTRATOR,)).username
        )
        response = get(client, cookie, PRICES, **query)
        assert code_of(response) == (422, "validation_error"), response.text
        assert code_of(get(client, cookie, PRICES)) == (200, None)
