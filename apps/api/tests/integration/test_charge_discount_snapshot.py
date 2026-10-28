"""Posting priced charges and the discount snapshot (#95 B5, C1, H8, H4
tests): the sweeper posts a discounted (or custom-priced) session charge
with its ``charge_discounts`` lines; later changes to the discounts, the
assignment, the custom price or the catalog leave a posted charge and its
lines unchanged; the lines are append-only; a rerun or a concurrent
duplicate post writes no second set; the charge CHECKs.

Each test starts from ``reset`` (billing off, earlier uncharged sessions
cancelled). Every ``all`` discount is restricted to the test's subject.
"""

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.workflows import session_charges
from tests.factories.billing import (
    assign_discount,
    persist_custom_price,
    persist_discount,
)
from tests.factories.families import add_to_family, persist_student
from tests.integration.billing_ledger_support import fails_with
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    PRICE_FROM,
    Item,
    add_version,
    charges_of,
    item,
    local,
    reset,
    run,
    set_floor,
)

DAY = FLOOR + timedelta(days=9)
INSUFFICIENT_PRIVILEGE = "42501"
REPEATS = 5


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    reset(module_seeded_url)


def only_charge(url: str, it: Item) -> Any:
    [row] = charges_of(url, it.session)
    return row


def lines_of(url: str, charge_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charge_discounts WHERE charge_id = :c ORDER BY position",
        {"c": charge_id},
    )


def snapshot(url: str, charge_id: uuid.UUID) -> tuple[str, list[str]]:
    [charge] = run_sql(
        url,
        "SELECT row_to_json(c)::text AS j FROM charges c WHERE id = :c",
        {"c": charge_id},
    )
    lines = run_sql(
        url,
        "SELECT row_to_json(d)::text AS j FROM charge_discounts d"
        " WHERE charge_id = :c ORDER BY position",
        {"c": charge_id},
    )
    return charge.j, [line.j for line in lines]


def add_sibling(url: str, it: Item) -> uuid.UUID:
    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Family

        family = await session.get(Family, it.family)
        assert family is not None
        sibling = await persist_student(session, contacts=0)
        await add_to_family(session, family, sibling)
        return sibling.id

    return in_uow(url, _add)


def discount(url: str, it: Item, **values: Any) -> uuid.UUID:
    eligibility = {"subject_ids": [str(it.subject)], **values.pop("eligibility", {})}
    return in_uow(
        url, lambda s: persist_discount(s, eligibility=eligibility, **values)
    ).id


def four_discounts(url: str, it: Item) -> dict[str, uuid.UUID]:
    """The H1 worked example: one discount of each kind."""
    found = {
        "scholarship": discount(
            url, it, kind="scholarship", audience="assigned", percent="50"
        ),
        "sibling": discount(
            url,
            it,
            kind="sibling",
            percent="10",
            eligibility={"min_family_students": 2},
        ),
        "discount": discount(url, it, kind="discount", amount="100.00"),
        "promotion": discount(
            url,
            it,
            kind="promotion",
            percent="5",
            starts_on=FLOOR,
            expires_on=DAY + timedelta(days=30),
        ),
    }
    in_uow(
        url,
        lambda s: assign_discount(s, found["scholarship"], student_id=it.student),
    )
    return found


def test_the_sweeper_posts_the_discounted_charge_and_its_lines(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="350.00")
    add_sibling(url, it)
    ids = four_discounts(url, it)
    set_floor(url, FLOOR)

    assert run(url).posted == 1

    charge = only_charge(url, it)
    assert (charge.list_amount, charge.discount_amount, charge.amount) == (
        Money("350.00").amount,
        Money("295.38").amount,
        Money("54.62").amount,
    )
    assert charge.price_version_id is not None and charge.custom_price_id is None
    lines = lines_of(url, charge.id)
    assert [
        (line.position, line.kind, line.discount_id, str(line.amount)) for line in lines
    ] == [
        (1, "scholarship", ids["scholarship"], "175.00"),
        (2, "sibling", ids["sibling"], "17.50"),
        (3, "discount", ids["discount"], "100.00"),
        (4, "promotion", ids["promotion"], "2.88"),
    ]
    assert [(line.percent, line.fixed_amount) for line in lines] == [
        (Decimal("50"), None),
        (Decimal("10"), None),
        (None, Decimal("100.00")),
        (Decimal("5"), None),
    ]
    assert lines[0].name.startswith("scholarship")
    found = in_uow(url, lambda s: billing.charge_discount_lines(s, [charge.id]))
    assert [line.amount for line in found[charge.id]] == [
        Money("175.00"),
        Money("17.50"),
        Money("100.00"),
        Money("2.88"),
    ]
    created = events(url, record_type="charge_discounts", action="create")
    mine = [e for e in created if e.record_id in {line.id for line in lines}]
    assert len(mine) == 4
    assert all(e.actor_user_id is None for e in mine)


def test_a_custom_priced_charge_stores_its_custom_price(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="350.00")
    assert it.price is not None
    custom = in_uow(
        url,
        lambda s: persist_custom_price(
            s,
            service_price_id=it.price,
            amount="200.00",
            effective_from=PRICE_FROM,
            family_id=it.family,
        ),
    ).id
    set_floor(url, FLOOR)

    run(url)

    charge = only_charge(url, it)
    assert (charge.custom_price_id, charge.price_version_id) == (custom, None)
    assert charge.list_amount == Money("200.00").amount
    assert lines_of(url, charge.id) == []


def test_later_changes_never_alter_a_posted_charge(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="350.00")
    assert it.price is not None
    add_sibling(url, it)
    ids = four_discounts(url, it)
    custom = in_uow(
        url,
        lambda s: persist_custom_price(
            s,
            service_price_id=it.price,
            amount="300.00",
            effective_from=PRICE_FROM,
            student_id=it.student,
        ),
    ).id
    set_floor(url, FLOOR)
    run(url)
    charge = only_charge(url, it)
    before = snapshot(url, charge.id)
    assert charge.custom_price_id == custom

    run_sql(
        url,
        "UPDATE discounts SET name = 'Renombrado', percent = 20,"
        " version = version + 1 WHERE id = :d",
        {"d": ids["promotion"]},
    )
    run_sql(
        url,
        "UPDATE discounts SET is_active = false WHERE id = :d",
        {"d": ids["sibling"]},
    )
    run_sql(
        url,
        "DELETE FROM discount_assignments WHERE discount_id = :d",
        {"d": ids["scholarship"]},
    )
    run_sql(
        url,
        "UPDATE custom_prices SET expires_on = :e WHERE id = :c",
        {"e": DAY, "c": custom},
    )
    add_version(url, it.price, DAY - timedelta(days=1), "999.00")

    assert snapshot(url, charge.id) == before

    later = item(
        url,
        starts_at=local(DAY + timedelta(days=1)),
        subject=it.subject,
        student=it.student,
        family=False,
        price=None,
    )
    run(url)
    newer = only_charge(url, later)
    assert newer.custom_price_id is None
    assert newer.list_amount == Money("999.00").amount
    assert [
        (line.kind, line.name, str(line.amount)) for line in lines_of(url, newer.id)
    ] == [
        ("discount", lines_of(url, charge.id)[2].name, "100.00"),
        ("promotion", "Renombrado", "179.80"),
    ]


def test_charge_discounts_are_append_only(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="100.00")
    discount(url, it, percent="10")
    set_floor(url, FLOOR)
    run(url)
    [line] = lines_of(url, only_charge(url, it).id)

    for sql in (
        "UPDATE charge_discounts SET amount = 1 WHERE id = :id",
        "DELETE FROM charge_discounts WHERE id = :id",
    ):
        message = fails_with(url, sql, INSUFFICIENT_PRIVILEGE, id=line.id)
        assert "charge_discounts is append-only" in message
    message = fails_with(url, "TRUNCATE charge_discounts", INSUFFICIENT_PRIVILEGE)
    assert "charge_discounts is append-only" in message


def test_a_rerun_writes_no_second_set_of_lines(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="100.00")
    discount(url, it, percent="10")
    set_floor(url, FLOOR)

    run(url)
    assert (
        in_uow(
            url,
            lambda s: session_charges.post_one(
                s, it.session, it.student, zone="America/Mexico_City"
            ),
        )
        == "already_posted"
    )

    charge = only_charge(url, it)
    assert len(lines_of(url, charge.id)) == 1


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_concurrent_duplicate_post_writes_one_set_of_lines(
    module_seeded_url, attempt
):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="100.00")
    discount(url, it, percent="10")
    discount(url, it, kind="promotion", amount="5.00", starts_on=FLOOR, expires_on=DAY)
    set_floor(url, FLOOR)

    async def _post() -> list[str]:
        engines = [short_lived_engine(url), short_lived_engine(url)]

        async def _one(engine: Any) -> str:
            async with UnitOfWork(create_sessionmaker(engine), None) as uow:
                return await session_charges.post_one(
                    uow.session, it.session, it.student, zone="America/Mexico_City"
                )

        try:
            return list(await asyncio.gather(*(_one(e) for e in engines)))
        finally:
            for engine in engines:
                await engine.dispose()

    outcomes = asyncio.run(_post())

    assert sorted(outcomes) == ["already_posted", "posted"]
    charge = only_charge(url, it)
    assert [str(line.amount) for line in lines_of(url, charge.id)] == ["10.00", "5.00"]


def test_the_charge_checks_reject_bad_rows(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="100.00")
    assert it.price is not None
    other = item(
        url,
        starts_at=local(DAY, 12),
        subject=it.subject,
        student=it.student,
        family=False,
        price=None,
    )
    set_floor(url, FLOOR)
    run(url)
    charge = only_charge(url, it)
    reset(url)
    custom = in_uow(
        url,
        lambda s: persist_custom_price(
            s,
            service_price_id=it.price,
            amount="1.00",
            effective_from=PRICE_FROM,
            student_id=it.student,
        ),
    ).id
    values = {
        "a": charge.billing_account_id,
        "st": it.student,
        "cs": other.session,
        "d": DAY,
        "v": charge.price_version_id,
        "c": custom,
    }
    with pytest.raises(IntegrityError, match="ck_charges_one_price"):
        run_sql(
            url,
            "INSERT INTO charges (billing_account_id, student_id, kind,"
            " class_session_id, scheduled_minutes, service_date, list_amount,"
            " amount, due_date, posted_at, price_version_id, custom_price_id)"
            " VALUES (:a, :st, 'session', :cs, 60, :d, 1, 1, :d, now(), :v, :c)",
            values,
        )
    with pytest.raises(IntegrityError, match="ck_charges_manual_unpriced"):
        run_sql(
            url,
            "INSERT INTO charges (billing_account_id, student_id, kind,"
            " service_date, list_amount, amount, due_date, posted_at,"
            " description, custom_price_id)"
            " VALUES (:a, :st, 'manual', :d, 1, 1, :d, now(), 'Cargo', :c)",
            values,
        )
