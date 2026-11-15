"""The shared price calculation (#95 B2-B4, H3 tests): custom prices above
the #89 catalog lookup (student before family, then the lowest rank, a
custom price beating a catalog ``NULL`` or a missing version, inclusive
dates), the discounts (audience, assignments, window, active flag, every
criterion, the sibling count, no family), discounts on top of a custom
price, and the statement bounds.

Every ``all`` discount here is restricted to the test's own subject, so the
module database's tests never reach each other.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from tests.factories.billing import (
    assign_discount,
    persist_custom_price,
    persist_discount,
    persist_service_price,
)
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.scheduling import persist_subject
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

DAY = date(2031, 3, 10)
FROM = date(2030, 1, 1)


@dataclass(frozen=True)
class World:
    subject: uuid.UUID
    level: uuid.UUID
    item: uuid.UUID
    """The any-level, any-duration item (rank 4), 300.00."""
    student: uuid.UUID
    sibling: uuid.UUID
    family: uuid.UUID
    other_student: uuid.UUID
    other_family: uuid.UUID


def make_world(url: str, *, catalog: str | None = "300.00") -> World:
    async def _make(session: AsyncSession) -> World:
        subject = await persist_subject(session)
        level = subject.education_level_id
        item = await persist_service_price(
            session,
            subject_id=subject.id,
            versions=[] if catalog is None else [(FROM, catalog)],
        )
        student = await persist_student(session, contacts=0, education_level_id=level)
        sibling = await persist_student(session, contacts=0, education_level_id=level)
        other = await persist_student(session, contacts=0, education_level_id=level)
        family = FamilyFactory.build()
        other_family = FamilyFactory.build()
        session.add_all([family, other_family])
        await session.flush()
        await add_to_family(session, family, student, sibling)
        await add_to_family(session, other_family, other)
        return World(
            subject=subject.id,
            level=level,
            item=item.id,
            student=student.id,
            sibling=sibling.id,
            family=family.id,
            other_student=other.id,
            other_family=other_family.id,
        )

    return in_uow(url, _make)


def query(w: World, **values: Any) -> billing.PriceQuery:
    base: dict[str, Any] = {
        "student_id": w.student,
        "family_id": w.family,
        "charge_kind": "session",
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": w.subject,
        "on": DAY,
        "education_level_id": w.level,
        "duration_minutes": 60,
    }
    return billing.PriceQuery(**{**base, **values})


def quote(url: str, q: billing.PriceQuery) -> billing.Quote | None:
    return in_uow(url, lambda s: billing.quote(s, q))


def add(url: str, call: Any) -> Any:
    return in_uow(url, call)


def custom(url: str, item: uuid.UUID, amount: str, **values: Any) -> uuid.UUID:
    values.setdefault("effective_from", FROM)
    return add(
        url,
        lambda s: persist_custom_price(
            s, service_price_id=item, amount=amount, **values
        ),
    ).id


def discount(url: str, w: World, **values: Any) -> uuid.UUID:
    eligibility = {"subject_ids": [str(w.subject)], **values.pop("eligibility", {})}
    return add(url, lambda s: persist_discount(s, eligibility=eligibility, **values)).id


def item(url: str, w: World, versions: list[Any], **key: Any) -> uuid.UUID:
    return add(
        url,
        lambda s: persist_service_price(
            s, subject_id=w.subject, versions=versions, **key
        ),
    ).id


# --- Base price (H6) -----------------------------------------------------------------


def test_no_custom_price_is_exactly_the_catalog(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)

    found = quote(url, query(w))

    assert found is not None
    assert (found.source, found.list_amount, found.rank) == (
        "catalog",
        Money("300.00"),
        4,
    )
    assert found.custom_price_id is None and found.price_version_id is not None
    assert found.discounts == () and found.amount == Money("300.00")


def test_a_student_price_beats_a_family_price_beats_the_catalog(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    family_price = custom(url, w.item, "250.00", family_id=w.family)

    found = quote(url, query(w))
    assert found is not None
    assert (found.source, found.custom_price_id, found.list_amount) == (
        "custom_family",
        family_price,
        Money("250.00"),
    )
    assert found.price_version_id is None

    student_price = custom(url, w.item, "200.00", student_id=w.student)
    found = quote(url, query(w))
    assert found is not None
    assert (found.source, found.custom_price_id) == ("custom_student", student_price)

    # The sibling has only the family price; another family none.
    sibling = quote(url, query(w, student_id=w.sibling))
    assert sibling is not None and sibling.custom_price_id == family_price
    other = quote(url, query(w, student_id=w.other_student, family_id=w.other_family))
    assert other is not None and other.source == "catalog"


def test_a_custom_price_on_a_less_specific_item_beats_a_specific_catalog(
    module_seeded_url,
):
    url = module_seeded_url
    w = make_world(url)
    item(url, w, [(FROM, "280.00")], education_level_id=w.level, duration_minutes=60)
    assert quote(url, query(w)).rank == 1  # type: ignore[union-attr]
    mine = custom(url, w.item, "150.00", student_id=w.student)

    found = quote(url, query(w))

    assert found is not None
    assert (found.custom_price_id, found.rank) == (mine, 4)


def test_between_two_student_prices_the_lower_rank_wins(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    exact = item(url, w, [(FROM, "280.00")], education_level_id=w.level)
    custom(url, w.item, "150.00", student_id=w.student)
    better = custom(url, exact, "170.00", student_id=w.student)
    # A family price on the exact item still loses to any student price.
    custom(url, exact, "100.00", family_id=w.family)

    found = quote(url, query(w))

    assert found is not None
    assert (found.custom_price_id, found.rank, found.list_amount) == (
        better,
        2,
        Money("170.00"),
    )


def test_a_custom_price_beats_not_offered_and_no_version(module_seeded_url):
    url = module_seeded_url
    w = make_world(url, catalog=None)
    assert quote(url, query(w)) is None
    mine = custom(url, w.item, "120.00", student_id=w.student)
    assert quote(url, query(w)).custom_price_id == mine  # type: ignore[union-attr]

    w2 = make_world(url, catalog=None)
    item(url, w2, [(FROM, None)], education_level_id=w2.level)
    assert quote(url, query(w2)) is None
    family_price = custom(url, w2.item, "90.00", family_id=w2.family)
    found = quote(url, query(w2))
    assert found is not None and found.custom_price_id == family_price


def test_custom_price_dates_are_inclusive(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    mine = custom(
        url,
        w.item,
        "100.00",
        student_id=w.student,
        effective_from=DAY,
        expires_on=DAY + timedelta(days=20),
    )

    def source(on: date) -> uuid.UUID | None:
        found = quote(url, query(w, on=on))
        assert found is not None
        return found.custom_price_id

    assert source(DAY - timedelta(days=1)) is None
    assert source(DAY) == mine
    assert source(DAY + timedelta(days=20)) == mine
    assert source(DAY + timedelta(days=21)) is None


# --- Discounts (H2, H3, H5, H9) ------------------------------------------------------


def lines(url: str, q: billing.PriceQuery) -> list[tuple[str, str]]:
    found = quote(url, q)
    assert found is not None
    return [(line.kind, str(line.amount)) for line in found.discounts]


def test_audience_all_and_assigned(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    discount(url, w, kind="discount", percent="10")
    by_student = discount(url, w, kind="scholarship", audience="assigned", percent="50")
    add(url, lambda s: assign_discount(s, by_student, student_id=w.student))

    assert lines(url, query(w)) == [("scholarship", "150.00"), ("discount", "15.00")]
    # The sibling and another family get only the general discount.
    assert lines(url, query(w, student_id=w.sibling)) == [("discount", "30.00")]

    by_family = discount(
        url,
        w,
        kind="promotion",
        audience="assigned",
        percent="5",
        starts_on=FROM,
        expires_on=DAY,
    )
    add(url, lambda s: assign_discount(s, by_family, family_id=w.family))
    assert lines(url, query(w, student_id=w.sibling)) == [
        ("discount", "30.00"),
        ("promotion", "13.50"),
    ]
    other = query(w, student_id=w.other_student, family_id=w.other_family)
    assert lines(url, other) == [("discount", "30.00")]


def test_the_window_against_the_service_date_and_the_active_flag(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    discount(
        url,
        w,
        kind="promotion",
        percent="10",
        starts_on=DAY,
        expires_on=DAY + timedelta(days=5),
    )
    discount(url, w, kind="discount", percent="10", is_active=False)

    assert lines(url, query(w, on=DAY - timedelta(days=1))) == []
    assert lines(url, query(w, on=DAY)) == [("promotion", "30.00")]
    assert lines(url, query(w, on=DAY + timedelta(days=5))) == [("promotion", "30.00")]
    assert lines(url, query(w, on=DAY + timedelta(days=6))) == []


def test_every_criterion(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    discount(url, w, percent="10", eligibility={"charge_kinds": ["group_enrollment"]})
    discount(url, w, percent="11", eligibility={"class_types": ["group"]})
    discount(
        url, w, percent="12", eligibility={"education_level_ids": [str(uuid.uuid4())]}
    )
    assert lines(url, query(w)) == []

    levelled = discount(
        url, w, percent="20", eligibility={"education_level_ids": [str(w.level)]}
    )
    found = quote(url, query(w))
    assert found is not None and [d.discount_id for d in found.discounts] == [levelled]
    # A student without a level doesn't get a level-restricted discount.
    assert lines(url, query(w, education_level_id=None)) == []


def test_the_sibling_count_ignores_inactive_students(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    discount(
        url,
        w,
        kind="sibling",
        percent="10",
        eligibility={"min_family_students": 2},
    )
    assert lines(url, query(w)) == [("sibling", "30.00")]
    assert lines(url, query(w, family_id=None)) == []

    run_sql(
        url, "UPDATE students SET is_active = false WHERE id = :s", {"s": w.sibling}
    )
    assert lines(url, query(w)) == []


def test_discounts_apply_on_top_of_a_custom_price(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    custom(url, w.item, "200.00", student_id=w.student)
    discount(url, w, percent="10")

    found = quote(url, query(w))

    assert found is not None
    assert (found.list_amount, found.discount_amount, found.amount) == (
        Money("200.00"),
        Money("20.00"),
        Money("180.00"),
    )


def test_no_family_means_no_family_assignment_and_no_sibling(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    by_family = discount(url, w, kind="scholarship", audience="assigned", percent="50")
    add(url, lambda s: assign_discount(s, by_family, family_id=w.family))

    assert lines(url, query(w)) == [("scholarship", "150.00")]
    assert lines(url, query(w, family_id=None)) == []


# --- Statement bounds (B2-B4) --------------------------------------------------------


def test_the_statement_bounds(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    discount(url, w, percent="10")
    q = query(w)

    assert count_statements(url, lambda s: billing.base_price(s, q))[0] <= 2
    assert (
        count_statements(url, lambda s: billing.discounts_for(s, q, Money("100.00")))[0]
        == 1
    )
    discount(
        url, w, kind="sibling", percent="5", eligibility={"min_family_students": 2}
    )
    assert (
        count_statements(url, lambda s: billing.discounts_for(s, q, Money("100.00")))[0]
        <= 2
    )
    count, found = count_statements(url, lambda s: billing.quote(s, q))
    assert count <= 4 and found is not None
    custom(url, w.item, "250.00", student_id=w.student)
    assert count_statements(url, lambda s: billing.base_price(s, q))[0] == 1


def test_custom_prices_for_matches_base_price(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    exact = item(url, w, [(FROM, "280.00")], education_level_id=w.level)
    custom(url, w.item, "150.00", student_id=w.student)
    custom(url, exact, "170.00", family_id=w.family)
    queries = [
        query(w),
        query(w, student_id=w.sibling),
        query(w, student_id=w.other_student, family_id=w.other_family),
        query(w, on=FROM - timedelta(days=1)),
    ]

    count, batched = count_statements(
        url, lambda s: billing.custom_prices_for(s, queries)
    )

    assert count == 1
    for q in queries:
        single = in_uow(url, lambda s, q=q: billing.base_price(s, q))
        expected = None if single is None or single.source == "catalog" else single
        assert batched.get(q) == expected, q


# --- The item pin (#93 B4, H8) -------------------------------------------------------


def test_a_pinned_query_prices_exactly_its_item(module_seeded_url):
    url = module_seeded_url
    w = make_world(url)
    weekly = item(
        url,
        w,
        [(FROM, "250.00"), (DAY + timedelta(days=1), "260.00")],
        pricing_model="per_period",
        period_frequency="weekly",
    )
    monthly = item(
        url,
        w,
        [(FROM, "900.00"), (DAY, None)],
        pricing_model="per_period",
        period_frequency="monthly",
    )
    discount(url, w, kind="discount", percent="10")
    pinned = query(
        w,
        charge_kind="tuition_period",
        pricing_model="per_period",
        duration_minutes=None,
        service_price_id=weekly,
    )

    count, found = count_statements(url, lambda s: billing.quote(s, pinned))

    assert count <= 4
    assert found is not None
    assert (str(found.list_amount), str(found.amount)) == ("250.00", "225.00")
    assert found.price_version_id is not None and found.rank == 0
    later = quote(url, query(w, **{**vars_of(pinned), "on": DAY + timedelta(days=1)}))
    assert later is not None and str(later.list_amount) == "260.00"
    # The monthly item's version in effect on DAY says "not offered".
    not_offered = billing.PriceQuery(**{**vars_of(pinned), "service_price_id": monthly})
    assert quote(url, not_offered) is None
    before = billing.PriceQuery(
        **{**vars_of(not_offered), "on": DAY - timedelta(days=1)}
    )
    found_before = quote(url, before)
    assert found_before is not None and str(found_before.list_amount) == "900.00"
    batched = in_uow(
        url, lambda s: billing.pinned_base_prices(s, [pinned, not_offered, before])
    )
    assert batched[not_offered] is None
    assert str(batched[pinned].list_amount) == "250.00"  # type: ignore[union-attr]
    assert str(batched[before].list_amount) == "900.00"  # type: ignore[union-attr]


def test_an_unpinned_per_period_query_is_a_programming_error(module_seeded_url):
    w = make_world(module_seeded_url)
    unpinned = query(w, charge_kind="tuition_period", pricing_model="per_period")

    with pytest.raises(ValueError, match="read through the plan"):
        quote(module_seeded_url, unpinned)


def vars_of(q: billing.PriceQuery) -> dict[str, Any]:
    return {field: getattr(q, field) for field in q.__dataclass_fields__}
