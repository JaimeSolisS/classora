"""Package constraints by direct SQL (#94 A2-A6, G10; I6, I11): the
append-only triggers of ``packages`` and ``package_credit_transactions``
(42501), each CHECK, each natural key, the ``charges`` composite foreign
key and natural key for ``package_purchase``, and the CHECKs migration 0066
replaced (a purchase charge needs its package; the ``tuition_period`` and
manual implications still hold)."""

import uuid
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import service as billing
from app.modules.billing.models import PackageProduct
from tests.factories.scheduling import persist_class_session
from tests.integration import package_support as ps
from tests.integration.billing_ledger_support import fails_with
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

CHECK = "23514"
UNIQUE = "23505"
FOREIGN_KEY = "23503"
APPEND_ONLY = "42501"
SOLD = date(2031, 3, 1)
POSTED_AT = datetime(2031, 3, 1, 12, 0, tzinfo=ps.ZONE)


@pytest.fixture(scope="module")
def sold(module_seeded_url: str) -> dict[str, Any]:
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url)
    package = ps.sell(url, w.student, product, SOLD)

    async def _session(session: AsyncSession) -> uuid.UUID:
        return (await persist_class_session(session)).id

    [row] = run_sql(
        url,
        "SELECT billing_account_id, price_version_id FROM packages WHERE id = :p",
        {"p": package},
    )
    return {
        "world": w,
        "product": product,
        "package": package,
        "account": row.billing_account_id,
        "version": row.price_version_id,
        "session": in_uow(url, _session),
    }


TX_INSERT = (
    "INSERT INTO package_credit_transactions (package_id, kind, units,"
    " class_session_id, student_id, reverses_transaction_id)"
    " VALUES (:package_id, :kind, :units, :class_session_id, :student_id,"
    " :reverses_transaction_id)"
)


def tx(sold: dict[str, Any], **values: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "package_id": sold["package"],
        "kind": "expire",
        "units": -1,
        "class_session_id": None,
        "student_id": None,
        "reverses_transaction_id": None,
    }
    row.update(values)
    return row


@pytest.mark.parametrize("table", ["packages", "package_credit_transactions"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE {table} SET created_at = now()",
        "DELETE FROM {table}",
        "TRUNCATE {table} CASCADE",
    ],
)
def test_the_package_tables_are_append_only(module_seeded_url, sold, table, statement):
    message = fails_with(module_seeded_url, statement.format(table=table), APPEND_ONLY)
    assert f"{table} is append-only" in message


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"kind": "refund"}, "ck_package_credit_transactions_kind"),
        ({"kind": "grant", "units": 0}, "ck_package_credit_transactions_units_sign"),
        ({"kind": "grant", "units": -1}, "ck_package_credit_transactions_units_sign"),
        ({"kind": "expire", "units": 1}, "ck_package_credit_transactions_units_sign"),
        (
            {"kind": "consume", "units": 1, "class_session_id": "S", "student_id": "T"},
            "ck_package_credit_transactions_units_sign",
        ),
        (
            {"kind": "reversal", "units": 0, "reverses_transaction_id": "R"},
            "ck_package_credit_transactions_units_sign",
        ),
        (
            {"kind": "consume", "units": -1, "student_id": "T"},
            "ck_package_credit_transactions_consume_session",
        ),
        (
            {"kind": "consume", "units": -1, "class_session_id": "S"},
            "ck_package_credit_transactions_consume_student",
        ),
        (
            {"class_session_id": "S", "student_id": "T"},
            "ck_package_credit_transactions_consume_session",
        ),
        (
            {"kind": "reversal", "units": 1},
            "ck_package_credit_transactions_reversal_target",
        ),
        (
            {"reverses_transaction_id": "R"},
            "ck_package_credit_transactions_reversal_target",
        ),
    ],
)
def test_transaction_checks(module_seeded_url, sold, values, constraint):
    w = sold["world"]
    resolved = {
        key: {"S": sold["session"], "T": w.student, "R": uuid.uuid4()}.get(value, value)
        if isinstance(value, str)
        else value
        for key, value in values.items()
    }
    message = fails_with(module_seeded_url, TX_INSERT, CHECK, **tx(sold, **resolved))
    assert constraint in message


def test_a_zero_reversal_is_refused_by_the_sign(module_seeded_url, sold):
    [grant] = run_sql(
        module_seeded_url,
        "SELECT id FROM package_credit_transactions WHERE package_id = :p"
        " AND kind = 'grant'",
        {"p": sold["package"]},
    )
    message = fails_with(
        module_seeded_url,
        TX_INSERT,
        CHECK,
        **tx(sold, kind="reversal", units=0, reverses_transaction_id=grant.id),
    )
    assert "ck_package_credit_transactions_units_sign" in message


def test_the_natural_keys(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, date(2031, 3, 10))
    base = {"package_id": package, "reverses_transaction_id": None}
    second_grant = fails_with(
        url,
        TX_INSERT,
        UNIQUE,
        **base,
        kind="grant",
        units=1,
        class_session_id=None,
        student_id=None,
    )
    assert "uq_package_credit_transactions_grant" in second_grant
    consume = {
        **base,
        "kind": "consume",
        "units": -1,
        "class_session_id": session,
        "student_id": w.student,
    }
    run_sql(url, TX_INSERT, consume)
    assert "uq_package_credit_transactions_consume" in fails_with(
        url, TX_INSERT, UNIQUE, **consume
    )
    expire = {**base, "kind": "expire", "units": -1, "class_session_id": None}
    expire["student_id"] = None
    run_sql(url, TX_INSERT, expire)
    assert "uq_package_credit_transactions_expire" in fails_with(
        url, TX_INSERT, UNIQUE, **expire
    )
    [consumed] = run_sql(
        url,
        "SELECT id FROM package_credit_transactions WHERE package_id = :p"
        " AND kind = 'consume'",
        {"p": package},
    )
    reversal = {
        "package_id": package,
        "kind": "reversal",
        "units": 1,
        "class_session_id": None,
        "student_id": None,
        "reverses_transaction_id": consumed.id,
    }
    run_sql(url, TX_INSERT, reversal)
    assert "uq_package_credit_transactions_reversal" in fails_with(
        url, TX_INSERT, UNIQUE, **reversal
    )


PACKAGE_INSERT = (
    "INSERT INTO packages (billing_account_id, student_id, package_product_id,"
    " price_version_id, unit, units_granted, subject_id, purchased_on, expires_on)"
    " VALUES (:account, :student, :product, :version, :unit, :units, NULL,"
    " :purchased_on, :expires_on)"
)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"unit": "hours"}, "ck_packages_unit_valid"),
        ({"units": 0}, "ck_packages_units_granted_positive"),
        ({"expires_on": SOLD}, "ck_packages_dates"),
    ],
)
def test_package_checks(module_seeded_url, sold, values, constraint):
    row = {
        "account": sold["account"],
        "student": sold["world"].student,
        "product": sold["product"],
        "version": sold["version"],
        "unit": "sessions",
        "units": 10,
        "purchased_on": SOLD,
        "expires_on": date(2031, 5, 30),
        **values,
    }
    assert constraint in fails_with(module_seeded_url, PACKAGE_INSERT, CHECK, **row)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"name": " Paquete"}, "ck_package_products_name_valid"),
        ({"unit": "hours"}, "ck_package_products_unit_valid"),
        ({"quantity": 0}, "ck_package_products_quantity_positive"),
        ({"validity_days": 0}, "ck_package_products_validity_days_positive"),
    ],
)
def test_product_checks(module_seeded_url, sold, values, constraint):
    column, value = next(iter(values.items()))
    message = fails_with(
        module_seeded_url,
        f"UPDATE package_products SET {column} = :v WHERE id = :p",  # noqa: S608
        CHECK,
        v=value,
        p=sold["product"],
    )
    assert constraint in message


CHARGE_INSERT = (
    "INSERT INTO charges (billing_account_id, student_id, kind, package_id,"
    " service_date, list_amount, discount_amount, amount, due_date, posted_at)"
    " VALUES (:account, :student, 'package_purchase', :package, :day, 100, 0, 100,"
    " :day, :posted_at)"
)


def _bare_package(url: str, sold: dict[str, Any]) -> uuid.UUID:
    """A package row without its purchase charge (billing layer)."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        product = await session.get(PackageProduct, sold["product"])
        assert product is not None
        package = await billing.insert_package(
            session,
            billing_account_id=sold["account"],
            student_id=sold["world"].student,
            product=product,
            price_version_id=sold["version"],
            purchased_on=SOLD,
        )
        return package.id

    return in_uow(url, _make)


def test_a_purchase_charge_row_is_accepted_once(module_seeded_url, sold):
    url = module_seeded_url
    package = _bare_package(url, sold)
    row = {
        "account": sold["account"],
        "student": sold["world"].student,
        "package": package,
        "day": SOLD,
        "posted_at": POSTED_AT,
    }

    run_sql(url, CHARGE_INSERT, row)

    assert "uq_charges_package_purchase" in fails_with(
        url, CHARGE_INSERT, UNIQUE, **row
    )


def test_a_purchase_charge_stays_on_its_packages_account_and_student(
    module_seeded_url, sold
):
    url = module_seeded_url
    package = _bare_package(url, sold)
    other = ps.world(url)
    other_account = in_uow(
        url, lambda s: billing.ensure_billing_account(s, other.family)
    )
    for account, student in (
        (other_account, sold["world"].student),
        (sold["account"], other.student),
    ):
        message = fails_with(
            url,
            CHARGE_INSERT,
            FOREIGN_KEY,
            account=account,
            student=student,
            package=package,
            day=SOLD,
            posted_at=POSTED_AT,
        )
        assert "fk_charges_package" in message


def test_the_purchase_charge_amount_rules_still_hold(module_seeded_url, sold):
    url = module_seeded_url
    package = _bare_package(url, sold)
    message = fails_with(
        url,
        CHARGE_INSERT.replace("100, 0, 100", "100, 0, 90"),
        CHECK,
        account=sold["account"],
        student=sold["world"].student,
        package=package,
        day=SOLD,
        posted_at=POSTED_AT,
    )
    assert "ck_charges_amount_equation" in message
