"""Operating expenses through the real routes: creating and reading (#115 A2,
A4, B, C, G; ADR-0017 E12, E16; data-model §12.3, §13.2). Payments and
reversals are ``test_finance_expense_payments.py``, adjustments
``test_finance_expense_adjustments.py``, the tables ``test_migrations.py -k
0084``, the roles ``tests/authz/matrix.toml``.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Money
from app.core.pagination import OffsetParams
from app.modules.finance import categories as finance_categories
from app.modules.finance import expenses as finance_expenses
from app.modules.finance import service as finance
from app.modules.finance.schemas import ExpenseCategoryUpdate, ExpenseCreate
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import auth_client, cookie_header, signed_headers
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.expense_support import (
    CREATED,
    CREATED_KEYS,
    DENIED,
    EXPENSES,
    GONE,
    INVALID,
    NOT_FOUND,
    NOW,
    TODAY,
    adjust,
    audit_rows,
    category_of,
    code,
    create,
    created,
    detail,
    expense_body,
    field_errors,
    listed,
    listing,
    new_key,
    new_vendor,
    paid,
    pay,
    read,
    reverse,
    rows,
    stored_keys,
)
from tests.integration.finance_support import reset
from tests.integration.test_payroll_workflow_concurrency import overlap

YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    reset(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance_staff(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture(scope="module")
def administrator(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (ADMINISTRATOR,))


@pytest.fixture
def rent(module_seeded_url: str) -> str:
    return category_of(module_seeded_url, "rent")


def insert_expense(url: str, category: str, **change: Any) -> str:
    """An expense by SQL, for reads that need many."""
    row = {
        "category_id": category,
        "vendor_id": None,
        "description": "Gasto",
        "amount": "100.00",
        "incurred_on": TODAY,
        "due_on": TODAY,
        "receipt_reference": None,
        **change,
    }
    columns = ", ".join(row)
    binds = ", ".join(f":{name}" for name in row)
    [made] = run_sql(
        url,
        f"INSERT INTO expenses ({columns}) VALUES ({binds}) RETURNING id",  # noqa: S608
        row,
    )
    return str(made.id)


# --- B5: the happy path -----


def test_b5_a_created_expense_answers_its_id_and_status_only(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url, "Inmobiliaria Sur")

    response = create(
        client,
        finance_staff,
        expense_body(
            rent,
            vendor_id=vendor,
            description="  Renta de diciembre ",
            receipt_reference=" F-1001 ",
            note=" Pagar\nantes del 10 ",
            due_on=(TODAY + timedelta(days=10)).isoformat(),
        ),
    )

    assert code(response) == CREATED, response.text
    assert set(response.json()) == CREATED_KEYS
    assert response.json()["status"] == "unpaid"
    [row] = rows(url, "expenses")
    assert str(row.id) == response.json()["id"]
    assert (
        row.description,
        row.receipt_reference,
        row.note,
        row.amount,
        str(row.vendor_id),
        str(row.category_id),
        row.created_by_user_id,
    ) == (
        "Renta de diciembre",
        "F-1001",
        "Pagar\nantes del 10",
        Money("1000.00").amount,
        vendor,
        rent,
        finance_staff.user.id,
    )


def test_b5_a_past_due_date_is_overdue_and_blank_optionals_are_null(
    module_seeded_url, client, owner, rent
):
    response = create(
        client,
        owner,
        expense_body(
            rent,
            incurred_on=YESTERDAY.isoformat(),
            due_on=YESTERDAY.isoformat(),
            receipt_reference="   ",
            note="",
        ),
    )

    assert code(response) == CREATED, response.text
    assert response.json()["status"] == "overdue"
    [row] = rows(module_seeded_url, "expenses")
    assert (row.receipt_reference, row.note, row.vendor_id) == (None, None, None)


def test_b5_replay_reuse_and_no_duplicate_rule(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    key = new_key()
    payload = expense_body(rent)

    first = create(client, finance_staff, payload, key=key)
    again = create(client, finance_staff, payload, key=key)
    reused = create(client, finance_staff, expense_body(rent, amount="2.00"), key=key)
    second = create(client, finance_staff, payload)

    assert code(first) == CREATED
    assert code(again) == CREATED
    assert again.json() == first.json()
    assert again.headers.get(REPLAYED_HEADER) == "true"
    assert code(reused) == (422, "idempotency_key_reused")
    assert code(second) == CREATED
    assert second.json()["id"] != first.json()["id"]
    assert len(rows(url, "expenses")) == 2


# --- B1, B2: the order and the body -----


def test_b1_401_csrf_and_permission_come_first(
    module_seeded_url, client, administrator, finance_staff, rent
):
    url = module_seeded_url
    client.cookies.clear()
    assert client.post(EXPENSES, json=expense_body(rent)).status_code == 401
    client.cookies.clear()
    no_csrf = client.post(
        EXPENSES,
        json=expense_body(rent),
        headers={**cookie_header(finance_staff.cookie), "Idempotency-Key": new_key()},
    )
    assert code(no_csrf) == (403, "csrf_failed")
    assert code(create(client, administrator, expense_body(rent))) == DENIED
    assert rows(url, "expenses") == []


def test_b1_a_lone_surrogate_comes_before_the_key_and_the_key_before_the_body(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    keys = len(stored_keys(url, finance_staff.user.id))
    raw = json.dumps(expense_body(rent, description="@@")).replace("@@", "a\\ud800b")
    client.cookies.clear()
    surrogate = client.post(
        EXPENSES,
        content=raw.encode(),
        headers={
            **signed_headers(finance_staff.cookie),
            "Content-Type": "application/json",
        },
    )
    assert code(surrogate) == INVALID
    assert field_errors(surrogate) == [(("body",), "text_invalid")]

    client.cookies.clear()
    no_key = client.post(
        EXPENSES, json={}, headers=signed_headers(finance_staff.cookie)
    )
    assert code(no_key) == (400, "idempotency_key_required")
    assert rows(url, "expenses") == []
    assert len(stored_keys(url, finance_staff.user.id)) == keys


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"amount": "0.00"}, (("body", "amount"), "amount_not_positive")),
        ({"amount": "-1.00"}, (("body", "amount"), "amount_not_positive")),
        ({"amount": "1.001"}, (("body", "amount"), "money_precision")),
        (
            {"amount": "1000000000000.00"},
            (("body", "amount"), "money_out_of_range"),
        ),
        ({"description": "   "}, (("body", "description"), "text_empty")),
        ({"description": "x" * 501}, (("body", "description"), "text_too_long")),
        ({"description": "a\x00b"}, (("body", "description"), "text_invalid")),
        ({"description": "a\nb"}, (("body", "description"), "text_invalid")),
        (
            {"receipt_reference": "x" * 101},
            (("body", "receipt_reference"), "text_too_long"),
        ),
        ({"note": "x" * 1001}, (("body", "note"), "text_too_long")),
        ({"description": ...}, (("body", "description"), "missing")),
        ({"category_id": "nope"}, (("body", "category_id"), "uuid_parsing")),
        (
            {"incurred_on": "2026-13-01"},
            (("body", "incurred_on"), "date_from_datetime_parsing"),
        ),
        ({"template_id": "x"}, (("body", "template_id"), "extra_forbidden")),
    ],
)
def test_b2_the_body_is_checked(
    module_seeded_url, client, finance_staff, rent, change, error
):
    response = create(client, finance_staff, expense_body(rent, **change))

    assert code(response) == INVALID, response.text
    assert error in field_errors(response)
    assert rows(module_seeded_url, "expenses") == []


def test_b2_texts_at_their_limits_pass(module_seeded_url, client, finance_staff, rent):
    response = create(
        client,
        finance_staff,
        expense_body(
            rent,
            description="x" * 500,
            receipt_reference="r" * 100,
            note="n" * 1000,
            amount="999999999999.99",
        ),
    )

    assert code(response) == CREATED, response.text


# --- B3: the dates -----


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (
            {"incurred_on": (TODAY + timedelta(days=1)).isoformat()},
            ("incurred_on", "incurred_on_in_future"),
        ),
        (
            {
                "incurred_on": TODAY.isoformat(),
                "due_on": YESTERDAY.isoformat(),
            },
            ("due_on", "due_on_before_incurred_on"),
        ),
    ],
)
def test_b3_the_date_rules(
    module_seeded_url, client, finance_staff, rent, change, error
):
    keys = len(stored_keys(module_seeded_url, finance_staff.user.id))
    response = create(client, finance_staff, expense_body(rent, **change))

    assert code(response) == (422, error[1]), response.text
    assert field_errors(response) == [(("body", error[0]), error[1])]
    assert rows(module_seeded_url, "expenses") == []
    assert len(stored_keys(module_seeded_url, finance_staff.user.id)) == keys


def test_b3_today_passes_and_the_center_today_decides_not_utc(
    module_seeded_url, client, frozen, rent
):
    # 05:59 UTC on Dec 8 is 23:59 on Dec 7 in Mexico City.
    frozen.set(datetime(2026, 12, 8, 5, 59, tzinfo=UTC))
    who = staff(module_seeded_url, client, (FINANCE_STAFF,))
    utc_today = date(2026, 12, 8).isoformat()

    refused = create(
        client, who, expense_body(rent, incurred_on=utc_today, due_on=utc_today)
    )
    due_today = create(client, who, expense_body(rent))

    assert code(refused) == (422, "incurred_on_in_future")
    assert code(due_today) == CREATED
    assert due_today.json()["status"] == "unpaid"
    frozen.set(datetime(2026, 12, 8, 6, 1, tzinfo=UTC))  # Dec 8, 00:01 local
    [item] = listed(client, who)
    assert item["status"] == "overdue"


# --- B4: the category and the vendor -----


def test_b4_unknown_and_inactive_category_and_vendor(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    inactive_vendor = new_vendor(url, active=False)
    supplies = category_of(url, "supplies")
    run_sql(
        url,
        "UPDATE expense_categories SET is_active = false WHERE id = :i",
        {"i": supplies},
    )

    cases = [
        (expense_body(GONE), (422, "expense_category_unknown")),
        (expense_body(rent, vendor_id=GONE), (422, "vendor_unknown")),
        (expense_body(supplies), (409, "expense_category_inactive")),
        (expense_body(rent, vendor_id=inactive_vendor), (409, "vendor_inactive")),
        # Every unknown before any inactive; the category first.
        (expense_body(supplies, vendor_id=GONE), (422, "vendor_unknown")),
        (
            expense_body(supplies, vendor_id=inactive_vendor),
            (409, "expense_category_inactive"),
        ),
        # The category and vendor before the dates.
        (
            expense_body(supplies, incurred_on=(TODAY + timedelta(days=3)).isoformat()),
            (409, "expense_category_inactive"),
        ),
    ]
    for payload, expected in cases:
        assert code(create(client, finance_staff, payload)) == expected, payload
    assert rows(url, "expenses") == []


def test_b4_an_expense_keeps_a_category_and_vendor_deactivated_later(
    module_seeded_url, client, finance_staff, owner, rent
):
    url = module_seeded_url
    vendor = new_vendor(url, "Papelería Centro")
    expense = created(client, finance_staff, expense_body(rent, vendor_id=vendor))
    run_sql(url, "UPDATE vendors SET is_active = false WHERE id = :i", {"i": vendor})
    run_sql(
        url,
        "UPDATE expense_categories SET is_active = false WHERE id = :i",
        {"i": rent},
    )

    payment = paid(client, finance_staff, expense, "100.00")
    assert code(adjust(client, owner, expense, "50.00")) == CREATED
    assert code(reverse(client, owner, payment)) == CREATED

    item = detail(client, finance_staff, expense)
    assert item["category"] == {
        "id": rent,
        "name": "Renta",
        "system_key": "rent",
        "is_active": False,
    }
    assert item["vendor"] == {
        "id": vendor,
        "name": "Papelería Centro",
        "is_active": False,
    }
    assert [i["id"] for i in listed(client, finance_staff, vendor_id=vendor)] == [
        expense
    ]
    assert [i["id"] for i in listed(client, finance_staff, category_id=rent)] == [
        expense
    ]


def _creating(user_id: uuid.UUID, category: str) -> Any:
    body = ExpenseCreate(
        category_id=uuid.UUID(category),
        description="Gasto en carrera",
        amount=Money("10.00"),
        incurred_on=TODAY,
        due_on=TODAY,
    )

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.create_expense(session, user_id, body)

    return call


def _deactivating(category: str) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance_categories.change_category(
            session,
            uuid.UUID(category),
            ExpenseCategoryUpdate(version=1, is_active=False),
        )

    return call


def test_b4_a_deactivation_first_makes_the_create_wait_and_refuse(
    module_seeded_url, monkeypatch, finance_staff
):
    url = module_seeded_url
    [made] = run_sql(
        url, "INSERT INTO expense_categories (name) VALUES ('Carrera') RETURNING id"
    )
    category = str(made.id)

    first, second = overlap(
        url,
        monkeypatch,
        finance_categories,
        "change_category",
        _deactivating(category),
        _creating(finance_staff.user.id, category),
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError)
    assert second.code == "expense_category_inactive"
    assert rows(url, "expenses") == []


def test_b4_a_create_first_makes_the_deactivation_wait_for_its_commit(
    module_seeded_url, monkeypatch, finance_staff
):
    url = module_seeded_url
    [made] = run_sql(
        url, "INSERT INTO expense_categories (name) VALUES ('Carrera') RETURNING id"
    )
    category = str(made.id)

    first, second = overlap(
        url,
        monkeypatch,
        finance_expenses,
        "insert_expense",
        _creating(finance_staff.user.id, category),
        _deactivating(category),
    )

    assert first.status == "unpaid"
    assert not isinstance(second, BaseException), second
    [row] = rows(url, "expenses")
    assert str(row.category_id) == category
    [state] = run_sql(
        url, "SELECT is_active FROM expense_categories WHERE id = :i", {"i": category}
    )
    assert state.is_active is False


# --- C1, C2: the list -----


def test_c1_an_item_carries_its_figures_and_status(
    module_seeded_url, client, finance_staff, owner, rent
):
    url = module_seeded_url
    vendor = new_vendor(url, "Luz del Valle")
    expense = created(
        client,
        finance_staff,
        expense_body(rent, vendor_id=vendor, receipt_reference="F-77", note="Nota"),
    )
    paid(client, finance_staff, expense, "300.00")
    reversed_payment = paid(client, finance_staff, expense, "100.00")
    assert code(reverse(client, owner, reversed_payment)) == CREATED
    assert code(adjust(client, owner, expense, "-200.00")) == CREATED

    response = listing(client, finance_staff)

    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 25, 0)
    [item] = body["items"]
    [row] = rows(url, "expenses")
    assert item == {
        "id": expense,
        "category": {
            "id": rent,
            "name": "Renta",
            "system_key": "rent",
            "is_active": True,
        },
        "vendor": {"id": vendor, "name": "Luz del Valle", "is_active": True},
        "description": "Renta de diciembre",
        "amount": "1000.00",
        "adjusted_amount": "800.00",
        "paid_amount": "300.00",
        "open_amount": "500.00",
        "status": "partially_paid",
        "incurred_on": TODAY.isoformat(),
        "due_on": TODAY.isoformat(),
        "receipt_reference": "F-77",
        "template": None,  # #116 C10: entered by hand
        "created_at": item["created_at"],
    }
    assert datetime.fromisoformat(item["created_at"]) == row.created_at
    assert "note" not in item


def _five_statuses(url: str, client: TestClient, who: Staff, owner: Staff, rent: str):
    """One expense in each status; returns their ids by status."""
    later = (TODAY + timedelta(days=5)).isoformat()
    unpaid = created(client, who, expense_body(rent, due_on=later, description="Uno"))
    partial = created(client, who, expense_body(rent, due_on=later, description="Dos"))
    paid(client, who, partial, "1.00")
    full = created(client, who, expense_body(rent, description="Tres"))
    paid(client, who, full, "1000.00")
    overdue = created(
        client,
        who,
        expense_body(
            rent,
            incurred_on=YESTERDAY.isoformat(),
            due_on=YESTERDAY.isoformat(),
            description="Cuatro",
        ),
    )
    paid(client, who, overdue, "1.00")
    void = created(client, who, expense_body(rent, description="Cinco"))
    assert code(adjust(client, owner, void, "-1000.00")) == CREATED
    return {
        "unpaid": unpaid,
        "partially_paid": partial,
        "paid": full,
        "overdue": overdue,
        "void": void,
    }


def test_c1_c2_the_status_filter_agrees_with_the_badge(
    module_seeded_url, client, finance_staff, owner, rent
):
    ids = _five_statuses(module_seeded_url, client, finance_staff, owner, rent)

    every = {i["id"]: i["status"] for i in listed(client, finance_staff)}
    assert every == {expense: status for status, expense in ids.items()}
    for status, expense in ids.items():
        assert [i["id"] for i in listed(client, finance_staff, status=status)] == [
            expense
        ], status
    both = listed(client, finance_staff, status=["void", "paid"])
    assert {i["id"] for i in both} == {ids["void"], ids["paid"]}
    assert code(listing(client, finance_staff, status="pending")) == INVALID


def test_c1_filters_by_category_vendor_dates_and_q(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    supplies = category_of(url, "supplies")
    vendor = new_vendor(url)
    a = insert_expense(url, rent, description="Renta 100% local", incurred_on=YESTERDAY)
    b = insert_expense(
        url, supplies, vendor_id=vendor, description="Hojas", receipt_reference="A_1"
    )
    c = insert_expense(
        url, supplies, description="Back\\slash", incurred_on=date(2026, 11, 1)
    )

    def ids(**params: Any) -> list[str]:
        return [i["id"] for i in listed(client, finance_staff, **params)]

    assert ids(category_id=supplies) == [b, c]
    assert ids(vendor_id=vendor) == [b]
    assert ids(incurred_from=YESTERDAY.isoformat()) == [b, a]
    assert ids(incurred_to=YESTERDAY.isoformat()) == [a, c]
    same = TODAY.isoformat()
    assert ids(incurred_from=same, incurred_to=same) == [b]
    assert ids(q="RENTA") == [a]
    assert ids(q="  100%  ") == [a]
    assert ids(q="%") == [a]
    assert ids(q="a_1") == [b]
    assert ids(q="_") == [b]
    assert ids(q="\\") == [c]
    assert ids(q="   ") == [b, a, c]
    reversed_range = listing(
        client,
        finance_staff,
        incurred_from=TODAY.isoformat(),
        incurred_to=YESTERDAY.isoformat(),
    )
    assert code(reversed_range) == INVALID
    assert field_errors(reversed_range) == [(("query", "incurred_to"), "range_invalid")]
    assert code(listing(client, finance_staff, q="x" * 101)) == INVALID


def test_c1_the_two_sorts_and_the_page(module_seeded_url, client, finance_staff, rent):
    url = module_seeded_url
    old = insert_expense(
        url, rent, incurred_on=date(2026, 11, 1), due_on=date(2026, 12, 20)
    )
    first = insert_expense(url, rent, due_on=date(2026, 12, 9))
    second = insert_expense(url, rent, due_on=date(2026, 12, 8))

    newest = [i["id"] for i in listed(client, finance_staff)]
    by_due = [i["id"] for i in listed(client, finance_staff, sort="due_on")]

    # Same incurred_on: the newer created_at first.
    assert newest == [second, first, old]
    assert by_due == [second, first, old]
    page = listing(client, finance_staff, limit=1, offset=1).json()
    assert ([i["id"] for i in page["items"]], page["total"]) == ([first], 3)
    past = listing(client, finance_staff, offset=10).json()
    assert (past["items"], past["total"]) == ([], 3)
    assert code(listing(client, finance_staff, sort="amount")) == INVALID


def test_c2_the_list_takes_the_same_statements_for_1_and_30(
    module_seeded_url, client, finance_staff, owner, rent
):
    url = module_seeded_url

    def count() -> int:
        statements, page = count_statements(
            url,
            lambda session: finance.list_expenses(
                session,
                filters=finance.ExpenseFilters(statuses=["partially_paid"]),
                sort="-incurred_on",
                page=OffsetParams(limit=100, offset=0),
            ),
        )
        assert page.items
        return int(statements)

    expense = created(client, finance_staff, expense_body(rent))
    paid(client, finance_staff, expense, "1.00")
    assert code(adjust(client, owner, expense, "5.00")) == CREATED
    one = count()
    for _ in range(29):
        more = insert_expense(url, rent)
        run_sql(
            url,
            "INSERT INTO expense_payments (expense_id, paid_on, amount, method_code,"
            " recorded_by_user_id) VALUES (:e, :d, 1, 'cash', :u)",
            {"e": more, "d": TODAY, "u": finance_staff.user.id},
        )
        run_sql(
            url,
            "INSERT INTO expense_adjustments (expense_id, amount, reason,"
            " created_by_user_id) VALUES (:e, 5, 'Ajuste', :u)",
            {"e": more, "u": owner.user.id},
        )

    assert count() == one == 2


# --- C3: the detail -----


def test_c3_the_detail(client, finance_staff, owner, rent):
    expense = created(client, finance_staff, expense_body(rent, note="Contrato anual"))
    first = paid(
        client, finance_staff, expense, "100.00", reference="SPEI 1", paid_on=YESTERDAY
    )
    second = paid(client, owner, expense, "50.00", method="cash", note="Caja")
    assert code(reverse(client, owner, first)) == CREATED
    assert code(adjust(client, owner, expense, "25.00", "Recargo")) == CREATED
    assert (
        code(adjust(client, finance_staff, expense, "-10.00", "Descuento")) == CREATED
    )

    item = detail(client, finance_staff, expense)

    assert (item["adjusted_amount"], item["paid_amount"], item["open_amount"]) == (
        "1015.00",
        "50.00",
        "965.00",
    )
    assert item["note"] == "Contrato anual"
    assert item["created_by"]["id"] == str(finance_staff.user.id)
    assert set(item["created_by"]) == {"id", "display_name"}
    assert [p["id"] for p in item["payments"]] == [second, first]
    newest, oldest = item["payments"]
    assert newest["reversal"] is None
    assert (newest["amount"], newest["method_code"], newest["reference"]) == (
        "50.00",
        "cash",
        None,
    )
    assert newest["note"] == "Caja"
    assert newest["recorded_by"]["id"] == str(owner.user.id)
    assert oldest["reference"] == "SPEI 1"
    assert oldest["paid_on"] == YESTERDAY.isoformat()
    assert oldest["reversal"]["reason"] == "Monto equivocado"
    assert oldest["reversal"]["reversed_by"]["id"] == str(owner.user.id)
    assert [(a["amount"], a["reason"]) for a in item["adjustments"]] == [
        ("25.00", "Recargo"),
        ("-10.00", "Descuento"),
    ]
    assert item["adjustments"][1]["created_by"]["id"] == str(finance_staff.user.id)
    assert code(read(client, finance_staff, GONE)) == NOT_FOUND


def test_c3_a_generated_expense_has_no_creator(
    module_seeded_url, client, finance_staff, rent
):
    expense = insert_expense(module_seeded_url, rent)

    assert detail(client, finance_staff, expense)["created_by"] is None


# --- G: access, leakage and no edit -----


def test_g1_only_owners_and_finance_staff_reach_the_routes(
    module_seeded_url, client, owner, finance_staff, rent
):
    url = module_seeded_url
    expense = created(client, owner, expense_body(rent))
    payment = paid(client, owner, expense, "1.00")
    others = [
        staff(url, client, (role,), delegations=delegations)
        for role, delegations in (
            (ADMINISTRATOR, None),
            (ADMINISTRATOR, ["finance.refund.approve"]),
            (ACADEMIC_COORDINATOR, None),
            (TUTOR, None),
            (STUDENT, None),
            (GUARDIAN, None),
        )
    ]
    for who in others:
        assert code(listing(client, who)) == DENIED
        assert code(read(client, who, expense)) == DENIED
        assert code(create(client, who, expense_body(rent))) == DENIED
        assert code(pay(client, who, expense)) == DENIED
        assert code(adjust(client, who, expense, "1.00")) == DENIED
        assert code(reverse(client, who, payment)) == DENIED
    for who in (owner, finance_staff):
        assert listing(client, who).status_code == 200
        assert read(client, who, expense).status_code == 200
    assert len(rows(url, "expense_payments")) == 1


def test_g2_no_amount_or_text_in_the_key_store_the_audit_or_the_logs(
    module_seeded_url, client, administrator, rent, caplog
):
    url = module_seeded_url
    owner = staff(url, client, (OWNER,))  # its own keys only
    secrets = (
        "4321.09",
        "Renta secreta",
        "FAC-SECRETA",
        "Nota secreta",
        "SPEI-SECRETO",
        "Pago secreto",
        "Motivo secreto",
        "Ajuste secreto",
        "312.34",
        "356.78",
    )
    with caplog.at_level(logging.DEBUG):
        expense = created(
            client,
            owner,
            expense_body(
                rent,
                amount="4321.09",
                description="Renta secreta",
                receipt_reference="FAC-SECRETA",
                note="Nota secreta",
            ),
        )
        payment = paid(
            client,
            owner,
            expense,
            "312.34",
            reference="SPEI-SECRETO",
            note="Pago secreto",
        )
        reversed_ = reverse(client, owner, payment, "Motivo secreto")
        adjusted = adjust(client, owner, expense, "356.78", "Ajuste secreto")
    assert code(reversed_) == code(adjusted) == CREATED

    keys = stored_keys(url, owner.user.id)
    assert len(keys) == 4
    records = {
        "expenses": expense,
        "expense_payments": payment,
        "expense_payment_reversals": reversed_.json()["id"],
        "expense_adjustments": adjusted.json()["id"],
    }
    events = audit_rows(url, list(records.values()))
    assert {e.record_type: e.action for e in events} == dict.fromkeys(records, "create")
    changes = {e.record_type: e.changes for e in events}
    masks = {
        "expenses": ("amount", "description", "receipt_reference", "note"),
        "expense_payments": ("amount", "reference", "note"),
        "expense_payment_reversals": ("reason",),
        "expense_adjustments": ("amount", "reason"),
    }
    for table, columns in masks.items():
        for column in columns:
            assert changes[table][column]["new"] == "[masked]", (table, column)
    # Category, vendor, dates and method stay visible (#115 E7).
    assert changes["expenses"]["category_id"]["new"] == rent
    assert changes["expenses"]["incurred_on"]["new"] == TODAY.isoformat()
    assert changes["expense_payments"]["method_code"]["new"] == "transfer"
    texts = [
        caplog.text,
        reversed_.text,
        adjusted.text,
        *(json.dumps(key.response_body) for key in keys),
        *(json.dumps([e.changes, e.details], default=str) for e in events),
    ]
    for text in texts:
        for secret in secrets:
            assert secret not in text, (secret, text)
    named = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE action LIKE 'finance.%'",
    )
    assert named[0].n == 0
    # What an Administrator's audit view shows.
    client.cookies.clear()
    seen = client.get(
        "/api/v1/audit-events",
        params={"record_type": "expenses", "record_id": expense},
        headers=cookie_header(administrator.cookie),
    )
    assert seen.status_code == 200, seen.text
    assert rent in seen.text
    for secret in secrets:
        assert secret not in seen.text, secret


def test_g3_no_route_changes_or_deletes_an_expense(
    module_seeded_url, client, owner, rent
):
    expense = created(client, owner, expense_body(rent))
    client.cookies.clear()
    headers = {**signed_headers(owner.cookie), "Idempotency-Key": new_key()}

    patched = client.patch(
        f"{EXPENSES}/{expense}",
        json={"category_id": category_of(module_seeded_url, "supplies")},
        headers=headers,
    )
    deleted = client.delete(f"{EXPENSES}/{expense}", headers=headers)

    assert patched.status_code == 405
    assert deleted.status_code == 405
    [row] = rows(module_seeded_url, "expenses")
    assert str(row.category_id) == rent


# --- A4: the reset clears expenses first -----


def test_a4_the_reset_clears_expenses_and_keeps_the_triggers(
    module_seeded_url, client, owner, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    expense = created(client, owner, expense_body(rent, vendor_id=vendor))
    payment = paid(client, owner, expense, "10.00")
    assert code(reverse(client, owner, payment)) == CREATED
    assert code(adjust(client, owner, expense, "1.00")) == CREATED

    reset(url)
    rent = category_of(url, "rent")

    for table in (
        "expenses",
        "expense_payments",
        "expense_payment_reversals",
        "expense_adjustments",
        "vendors",
    ):
        assert rows(url, table) == [], table
    assert len(run_sql(url, "SELECT id FROM expense_categories")) == 5
    disabled = run_sql(
        url,
        "SELECT tgname FROM pg_trigger WHERE tgrelid = ANY(ARRAY["
        "'expenses'::regclass, 'expense_payments'::regclass,"
        " 'expense_payment_reversals'::regclass, 'expense_adjustments'::regclass])"
        " AND tgenabled = 'D'",
    )
    assert disabled == []
    again = created(client, owner, expense_body(rent))
    assert run_sql(url, "SELECT count(*) AS n FROM expenses")[0].n == 1
    with pytest.raises(Exception):  # noqa: B017, PT011 - the trigger is back on
        run_sql(url, "DELETE FROM expenses WHERE id = :i", {"i": again})
