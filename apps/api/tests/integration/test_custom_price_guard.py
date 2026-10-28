"""The custom price guard and constraints (#95 A2, A7, H7; H5 tests) at the
SQL level: once a charge references a custom price only ``expires_on`` may
change and the row can't be deleted (42501); ``TRUNCATE`` is always refused;
an unreferenced row may be changed or deleted; the exclusion constraints
per student and per family (inclusive ranges). The route rules on top
(``custom_price_expiry_invalid``, ``custom_price_in_use``) are in
``test_billing_custom_prices_routes.py``."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from tests.factories.billing import persist_custom_price
from tests.integration.billing_ledger_support import fails_with
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    PRICE_FROM,
    Item,
    charges_of,
    item,
    local,
    reset,
    run,
    set_floor,
)

DAY = FLOOR + timedelta(days=9)
INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    reset(module_seeded_url)


def custom(url: str, it: Item, **values: Any) -> uuid.UUID:
    assert it.price is not None
    values.setdefault("student_id", it.student)
    values.setdefault("effective_from", PRICE_FROM)
    values.setdefault("amount", "200.00")
    return in_uow(
        url, lambda s: persist_custom_price(s, service_price_id=it.price, **values)
    ).id


def referenced(url: str) -> tuple[Item, uuid.UUID]:
    it = item(url, starts_at=local(DAY), price="300.00")
    custom_id = custom(url, it)
    set_floor(url, FLOOR)
    run(url)
    [charge] = charges_of(url, it.session)
    assert charge.custom_price_id == custom_id
    return it, custom_id


def test_a_referenced_custom_price_can_only_be_ended(module_seeded_url):
    url = module_seeded_url
    _, custom_id = referenced(url)

    for sql in (
        "UPDATE custom_prices SET amount = 1 WHERE id = :id",
        "UPDATE custom_prices SET effective_from = effective_from + 1 WHERE id = :id",
        "UPDATE custom_prices SET student_id = NULL, family_id = (SELECT id FROM"
        " families LIMIT 1) WHERE id = :id",
        "DELETE FROM custom_prices WHERE id = :id",
    ):
        message = fails_with(url, sql, INSUFFICIENT_PRIVILEGE, id=custom_id)
        assert "custom_prices row is referenced" in message

    run_sql(
        url,
        "UPDATE custom_prices SET expires_on = :e, version = version + 1,"
        " updated_at = now() WHERE id = :id",
        {"e": DAY + timedelta(days=3), "id": custom_id},
    )
    [row] = run_sql(
        url, "SELECT expires_on FROM custom_prices WHERE id = :id", {"id": custom_id}
    )
    assert row.expires_on == DAY + timedelta(days=3)


def test_truncate_is_always_refused(module_seeded_url):
    message = fails_with(
        module_seeded_url, "TRUNCATE custom_prices CASCADE", INSUFFICIENT_PRIVILEGE
    )
    assert "custom_prices is never truncated" in message


def test_an_unreferenced_custom_price_can_be_changed_and_deleted(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="300.00")
    custom_id = custom(url, it)

    run_sql(
        url, "UPDATE custom_prices SET amount = 1 WHERE id = :id", {"id": custom_id}
    )
    run_sql(url, "DELETE FROM custom_prices WHERE id = :id", {"id": custom_id})

    assert (
        run_sql(url, "SELECT id FROM custom_prices WHERE id = :id", {"id": custom_id})
        == []
    )


@pytest.mark.parametrize("target", ["student", "family"])
def test_ranges_of_one_target_and_item_never_overlap(module_seeded_url, target):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="300.00")
    key = (
        {"student_id": it.student}
        if target == "student"
        else {"student_id": None, "family_id": it.family}
    )
    a, b = date(2031, 1, 1), date(2031, 1, 31)
    custom(url, it, effective_from=a, expires_on=b, **key)

    # Adjacent ranges are allowed; sharing one day is an overlap.
    custom(
        url,
        it,
        effective_from=b + timedelta(days=1),
        expires_on=date(2031, 2, 28),
        **key,
    )
    with pytest.raises(IntegrityError, match=f"ex_custom_prices_{target}_overlap"):
        custom(url, it, effective_from=date(2031, 2, 28), **key)
    with pytest.raises(IntegrityError, match=f"ex_custom_prices_{target}_overlap"):
        custom(url, it, effective_from=b, expires_on=b, **key)
    # Open-ended overlaps everything after it.
    custom(url, it, effective_from=date(2031, 3, 1), **key)
    with pytest.raises(IntegrityError, match=f"ex_custom_prices_{target}_overlap"):
        custom(
            url, it, effective_from=date(2040, 1, 1), expires_on=date(2040, 1, 2), **key
        )


def test_one_target_and_ordered_dates(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="300.00")

    with pytest.raises(IntegrityError, match="ck_custom_prices_one_target"):
        custom(url, it, family_id=it.family)
    with pytest.raises(IntegrityError, match="ck_custom_prices_dates"):
        custom(url, it, effective_from=DAY, expires_on=DAY - timedelta(days=1))
    with pytest.raises(IntegrityError, match="ck_custom_prices_amount_non_negative"):
        custom(url, it, amount="-1.00")
