"""Recurring expense templates through the real routes (#116 A2, A4, C, E, F;
ADR-0017 E17; data-model §12.7). The job is
``test_finance_recurring_expenses.py``, the grid
``tests/unit/test_finance_template_periods.py``, the table ``test_migrations.py
-k 0085``, the roles ``tests/authz/matrix.toml``.

The clock is frozen at :data:`NOW` (the center's today is :data:`TODAY`,
``America/Mexico_City``) unless a test moves it.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.money import Money
from app.modules.finance import categories as finance_categories
from app.modules.finance import expenses as finance_expenses
from app.modules.finance import service as finance
from app.modules.finance import templates as finance_templates
from app.modules.finance import vendors as finance_vendors
from app.modules.finance.schemas import (
    ExpenseCategoryUpdate,
    ExpenseTemplateCreate,
    ExpenseTemplateUpdate,
    VendorUpdate,
)
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
from tests.integration.catalog_support import count_statements, patch
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.finance_support import CATEGORIES, VENDORS, reset
from tests.integration.template_support import (
    EXPENSES,
    TEMPLATES,
    category_of,
    change,
    changed,
    clear_queue,
    code,
    create,
    created,
    field_errors,
    generated,
    listed,
    listing,
    new_category,
    new_vendor,
    queued,
    read,
    run_job,
    set_active,
    template_body,
    template_path,
    template_row,
)
from tests.integration.test_payroll_workflow_concurrency import overlap

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
TODAY = date(2026, 12, 7)
"""The center's today at :data:`NOW` (noon in Mexico City)."""
GONE = "00000000-0000-7000-8000-000000000000"

CREATED = (201, None)
OK = (200, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
STALE = (409, "version_conflict")
EXISTS = (409, "expense_template_exists")
INVALID = (422, "validation_error")
STARTS_ON = (422, "expense_template_starts_on_invalid")
ENDS_ON = (422, "expense_template_ends_on_invalid")
CATEGORY_UNKNOWN = (422, "expense_category_unknown")
VENDOR_UNKNOWN = (422, "vendor_unknown")
CATEGORY_INACTIVE = (409, "expense_category_inactive")
VENDOR_INACTIVE = (409, "vendor_inactive")
CATEGORY_IN_USE = (409, "expense_category_in_use")
VENDOR_IN_USE = (409, "vendor_in_use")

ITEM_KEYS = {
    "id",
    "name",
    "category",
    "vendor",
    "amount",
    "frequency",
    "starts_on",
    "ends_on",
    "due_offset_days",
    "next_period_start",
    "last_period_start",
    "status",
    "is_active",
    "version",
    "created_at",
    "updated_at",
}


def local_noon(day: date) -> datetime:
    """Noon in Mexico City (UTC-6) on ``day``."""
    return datetime(day.year, day.month, day.day, 18, 0, tzinfo=UTC)


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
    clear_queue(module_seeded_url)
    yield
    reset(module_seeded_url)
    clear_queue(module_seeded_url)


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


def signed_in(url: str, client: TestClient) -> Staff:
    """Finance Staff signed in at the clock's current time (a session doesn't
    survive a jump of the frozen clock)."""
    return staff(url, client, (FINANCE_STAFF,))


def _create(client: TestClient, who: Staff, body: dict[str, Any]) -> dict[str, Any]:
    return created(client, who.cookie, body)


# --- A2, C3, C7: create and the item -----


def test_c3_a_create_answers_the_item_and_queues_a_run(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url, "Inmobiliaria Sur")
    body = template_body(
        rent,
        TODAY,
        name="  Renta local  ",
        vendor_id=vendor,
        ends_on=date(2027, 12, 31).isoformat(),
        due_offset_days=10,
    )

    response = create(client, finance_staff.cookie, body)

    assert response.status_code == 201, response.text
    item = response.json()
    assert set(item) == ITEM_KEYS
    assert item["name"] == "Renta local"
    assert item["category"] == {
        "id": rent,
        "name": "Renta",
        "system_key": "rent",
        "is_active": True,
    }
    assert item["vendor"] == {
        "id": vendor,
        "name": "Inmobiliaria Sur",
        "is_active": True,
    }
    assert (item["amount"], item["frequency"], item["due_offset_days"]) == (
        "8000.00",
        "monthly",
        10,
    )
    assert (item["starts_on"], item["ends_on"]) == ("2026-12-07", "2027-12-31")
    assert item["next_period_start"] == "2026-12-07"
    assert item["last_period_start"] is None
    assert (item["status"], item["is_active"], item["version"]) == ("active", True, 1)
    row = template_row(url, item["id"])
    assert (row.name, row.next_period_start) == ("Renta local", TODAY)
    assert queued(url) == 1


def test_c3_the_name_is_unique_ignoring_case_and_spaces_paused_and_ended_too(
    module_seeded_url, client, finance_staff, rent
):
    first = _create(client, finance_staff, template_body(rent, TODAY, name="Renta"))

    for name in ("renta", " RENTA ", "Renta"):
        again = create(
            client, finance_staff.cookie, template_body(rent, TODAY, name=name)
        )
        assert code(again) == EXISTS, name
    changed(
        client, finance_staff.cookie, first["id"], {"version": 1, "is_active": False}
    )
    assert (
        code(
            create(
                client, finance_staff.cookie, template_body(rent, TODAY, name="RENTA")
            )
        )
        == EXISTS
    )
    ended = _create(
        client,
        finance_staff,
        template_body(rent, TODAY, name="Luz", ends_on=TODAY.isoformat()),
    )
    run_job(module_seeded_url)
    assert listed(client, finance_staff.cookie, status="ended")[0]["id"] == ended["id"]
    assert (
        code(
            create(client, finance_staff.cookie, template_body(rent, TODAY, name="luz"))
        )
        == EXISTS
    )


def test_c3_no_idempotency_key_is_read(module_seeded_url, client, finance_staff, rent):
    body = template_body(rent, TODAY)
    key = {"Idempotency-Key": f"key-{uuid.uuid4().hex}"}

    first = create(client, finance_staff.cookie, body, headers=key)
    retried = create(client, finance_staff.cookie, body, headers=key)

    assert code(first) == CREATED
    assert code(retried) == EXISTS
    stored = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM idempotency_keys WHERE endpoint LIKE :p",
        {"p": "%expense-templates%"},
    )
    assert stored[0].n == 0


# --- C1, C2: create checks and their order -----


def test_c1_a_lone_surrogate_is_text_invalid_before_the_body(
    client, finance_staff, rent
):
    raw = (
        '{"name": "Renta \\ud800", "category_id": "' + rent + '", "amount": "0",'
        ' "frequency": "daily", "starts_on": "2026-12-07", "due_offset_days": 99}'
    )
    client.cookies.clear()
    response = client.post(
        TEMPLATES,
        content=raw,
        headers={
            **signed_headers(finance_staff.cookie),
            "Content-Type": "application/json",
        },
    )

    assert code(response) == INVALID
    assert field_errors(response) == [(("body",), "text_invalid")]


@pytest.mark.parametrize(
    ("change_", "path", "error"),
    [
        ({"name": "   "}, "name", "text_empty"),
        ({"name": "x" * 201}, "name", "text_too_long"),
        ({"name": "Renta\nmayo"}, "name", "text_invalid"),
        ({"amount": "0.00"}, "amount", "amount_not_positive"),
        ({"amount": "-5.00"}, "amount", "amount_not_positive"),
        ({"due_offset_days": -1}, "due_offset_days", "greater_than_equal"),
        ({"due_offset_days": 61}, "due_offset_days", "less_than_equal"),
        ({"frequency": "biweekly"}, "frequency", "literal_error"),
        ({"next_period_start": "2026-12-07"}, "next_period_start", "extra_forbidden"),
        ({"category_id": ...}, "category_id", "missing"),
    ],
)
def test_c1_the_body_is_checked(client, finance_staff, rent, change_, path, error):
    response = create(
        client, finance_staff.cookie, template_body(rent, TODAY, **change_)
    )

    assert code(response) == INVALID
    assert (("body", path), error) in field_errors(response)


def test_c1_the_refusals_come_in_order(module_seeded_url, client, finance_staff, rent):
    url = module_seeded_url
    _create(client, finance_staff, template_body(rent, TODAY, name="Renta"))
    inactive_category = new_category(url, active=False)
    inactive_vendor = new_vendor(url, active=False)
    far = (TODAY + timedelta(days=400)).isoformat()

    def attempt(**change_: Any) -> tuple[int, str | None]:
        return code(
            create(client, finance_staff.cookie, template_body(rent, TODAY, **change_))
        )

    # A taken name before an unknown category.
    assert attempt(name="RENTA", category_id=GONE) == EXISTS
    # Unknown before inactive, the category first.
    assert attempt(category_id=GONE, vendor_id=inactive_vendor) == CATEGORY_UNKNOWN
    assert attempt(category_id=inactive_category, vendor_id=GONE) == VENDOR_UNKNOWN
    assert attempt(category_id=inactive_category, vendor_id=inactive_vendor) == (
        CATEGORY_INACTIVE
    )
    assert attempt(vendor_id=inactive_vendor) == VENDOR_INACTIVE
    # Inactive before the dates; starts_on before ends_on.
    assert attempt(category_id=inactive_category, starts_on=far) == CATEGORY_INACTIVE
    assert attempt(starts_on=far, ends_on="2026-01-01") == STARTS_ON
    assert attempt(ends_on=(TODAY - timedelta(days=1)).isoformat()) == ENDS_ON
    assert run_sql(url, "SELECT count(*) AS n FROM expense_templates")[0].n == 1


@pytest.mark.parametrize(
    ("offset", "allowed"),
    [(-31, True), (-32, False), (366, True), (367, False), (0, True)],
)
def test_c2_starts_on_window(client, finance_staff, rent, offset, allowed):
    starts_on = TODAY + timedelta(days=offset)

    response = create(client, finance_staff.cookie, template_body(rent, starts_on))

    if allowed:
        assert code(response) == CREATED
    else:
        assert code(response) == STARTS_ON
        assert field_errors(response) == [
            (("body", "starts_on"), "expense_template_starts_on_invalid")
        ]


def test_c2_ends_on_on_starts_on_passes_and_the_day_before_fails(
    client, finance_staff, rent
):
    same = create(
        client,
        finance_staff.cookie,
        template_body(rent, TODAY, ends_on=TODAY.isoformat()),
    )
    before = create(
        client,
        finance_staff.cookie,
        template_body(rent, TODAY, ends_on=(TODAY - timedelta(days=1)).isoformat()),
    )

    assert code(same) == CREATED
    assert code(before) == ENDS_ON
    assert field_errors(before) == [
        (("body", "ends_on"), "expense_template_ends_on_invalid")
    ]


def test_c2_the_window_uses_the_center_today_not_utc(
    module_seeded_url, frozen, client, rent
):
    # 05:30 UTC on the 8th is 23:30 on the 7th in Mexico City.
    frozen.set(datetime(2026, 12, 8, 5, 30, tzinfo=UTC))
    finance_staff = signed_in(module_seeded_url, client)
    back = date(2026, 11, 6)  # the 7th - 31: refused if the UTC 8th were used
    ahead = date(2027, 12, 9)  # the 8th + 366: allowed only by a UTC today

    assert code(create(client, finance_staff.cookie, template_body(rent, back))) == (
        CREATED
    )
    assert code(create(client, finance_staff.cookie, template_body(rent, ahead))) == (
        STARTS_ON
    )


# --- C4-C6: change -----


def test_c4_frequency_starts_on_and_next_period_are_fixed(client, finance_staff, rent):
    item = _create(client, finance_staff, template_body(rent, TODAY))

    for field, value in (
        ("frequency", "weekly"),
        ("starts_on", "2026-12-08"),
        ("next_period_start", "2027-01-07"),
        ("status", "paused"),
    ):
        response = change(
            client, finance_staff.cookie, item["id"], {"version": 1, field: value}
        )
        assert code(response) == INVALID, field
        assert ((("body", field), "extra_forbidden")) in field_errors(response)
    assert code(change(client, finance_staff.cookie, item["id"], {"version": 1})) == (
        INVALID
    )
    response = change(
        client, finance_staff.cookie, item["id"], {"version": 1, "name": None}
    )
    assert code(response) == INVALID


def test_c4_404_then_version_then_name(module_seeded_url, client, finance_staff, rent):
    _create(client, finance_staff, template_body(rent, TODAY, name="Renta"))
    item = _create(client, finance_staff, template_body(rent, TODAY, name="Luz"))

    assert (
        code(
            change(client, finance_staff.cookie, GONE, {"version": 1, "amount": "1.00"})
        )
        == NOT_FOUND
    )
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 9, "name": "renta"},
            )
        )
        == STALE
    )
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 1, "name": " RENTA ", "category_id": GONE},
            )
        )
        == EXISTS
    )
    # Its own name in another case is no clash.
    renamed = changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "name": "LUZ"}
    )
    assert (renamed["name"], renamed["version"]) == ("LUZ", 2)


def test_c4_every_field_changes_and_vendor_null_removes_it(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    supplies = category_of(url, "supplies")
    item = _create(client, finance_staff, template_body(rent, TODAY, vendor_id=vendor))

    after = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {
            "version": 1,
            "name": "Papelería",
            "category_id": supplies,
            "amount": "120.50",
            "due_offset_days": 0,
            "ends_on": "2027-06-30",
        },
    )
    assert (after["name"], after["category"]["id"], after["amount"]) == (
        "Papelería",
        supplies,
        "120.50",
    )
    assert (after["due_offset_days"], after["ends_on"], after["version"]) == (
        0,
        "2027-06-30",
        2,
    )
    assert after["vendor"]["id"] == vendor  # left out: kept

    cleared = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": 2, "vendor_id": None, "ends_on": None},
    )
    assert (cleared["vendor"], cleared["ends_on"], cleared["version"]) == (
        None,
        None,
        3,
    )
    # An unchanged value writes nothing.
    same = changed(
        client, finance_staff.cookie, item["id"], {"version": 3, "amount": "120.50"}
    )
    assert same["version"] == 3


def test_c4_a_new_unknown_category_or_vendor_is_refused(client, finance_staff, rent):
    item = _create(client, finance_staff, template_body(rent, TODAY))
    changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "is_active": False}
    )

    # Paused: only existence is checked.
    for body, expected in (
        ({"category_id": GONE}, CATEGORY_UNKNOWN),
        ({"vendor_id": GONE}, VENDOR_UNKNOWN),
    ):
        response = change(
            client, finance_staff.cookie, item["id"], {"version": 2, **body}
        )
        assert code(response) == expected, body


def test_c5_a_write_that_leaves_the_template_live_rereads_category_and_vendor(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    category = new_category(url)
    item = _create(
        client,
        finance_staff,
        template_body(category, TODAY, vendor_id=vendor, starts_on="2027-01-01"),
    )
    other = new_category(url, active=False)
    other_vendor = new_vendor(url, active=False)

    # A new inactive category or vendor on a live template.
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 1, "category_id": other},
            )
        )
        == CATEGORY_INACTIVE
    )
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 1, "vendor_id": other_vendor},
            )
        )
        == VENDOR_INACTIVE
    )
    # Paused, its category deactivated (now allowed), then a rename passes …
    changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "is_active": False}
    )
    set_active(url, "expense_categories", category, False)
    renamed = changed(
        client, finance_staff.cookie, item["id"], {"version": 2, "name": "Renta vieja"}
    )
    assert renamed["status"] == "paused"
    # … and a reactivation is refused (D10's premise).
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 3, "is_active": True},
            )
        )
        == CATEGORY_INACTIVE
    )
    # An unchanged category is read too: an amount change on a live template.
    set_active(url, "expense_categories", category, True)
    changed(client, finance_staff.cookie, item["id"], {"version": 3, "is_active": True})
    set_active(url, "vendors", vendor, False)
    assert (
        code(
            change(
                client,
                finance_staff.cookie,
                item["id"],
                {"version": 4, "amount": "1.00"},
            )
        )
        == VENDOR_INACTIVE
    )


def test_c5_an_ends_on_that_revives_an_ended_template_rereads_them(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    item = _create(
        client,
        finance_staff,
        template_body(rent, TODAY, vendor_id=vendor, ends_on=TODAY.isoformat()),
    )
    run_job(url)  # posts the 7th and ends it
    assert read(client, finance_staff.cookie, item["id"]).json()["status"] == "ended"
    version = template_row(url, item["id"]).version
    set_active(url, "vendors", vendor, False)

    revived = change(
        client, finance_staff.cookie, item["id"], {"version": version, "ends_on": None}
    )
    renamed = change(
        client, finance_staff.cookie, item["id"], {"version": version, "name": "Vieja"}
    )

    assert code(revived) == VENDOR_INACTIVE
    assert code(renamed) == OK


def test_c6_a_reactivation_never_back_fills(
    module_seeded_url, frozen, client, finance_staff, rent
):
    url = module_seeded_url
    frozen.set(local_noon(date(2027, 1, 1)))
    finance_staff = signed_in(url, client)
    item = _create(client, finance_staff, template_body(rent, date(2027, 1, 1)))
    run_job(url)
    frozen.set(local_noon(date(2027, 2, 10)))
    finance_staff = signed_in(url, client)
    run_job(url)
    version = template_row(url, item["id"]).version
    paused = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": version, "is_active": False},
    )
    assert (paused["status"], paused["next_period_start"]) == ("paused", "2027-03-01")
    clear_queue(url)

    frozen.set(local_noon(date(2027, 4, 15)))
    finance_staff = signed_in(url, client)
    resumed = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": paused["version"], "is_active": True},
    )
    assert (resumed["status"], resumed["next_period_start"]) == ("active", "2027-05-01")
    assert queued(url) == 1
    run_job(url)

    periods = [row.period_start for row in generated(url, item["id"])]
    assert periods == [date(2027, 1, 1), date(2027, 2, 1)]


def test_c6_a_reactivation_on_a_period_start_posts_it_and_pausing_moves_nothing(
    module_seeded_url, frozen, client, finance_staff, rent
):
    url = module_seeded_url
    frozen.set(local_noon(date(2027, 1, 1)))
    finance_staff = signed_in(url, client)
    item = _create(client, finance_staff, template_body(rent, date(2027, 1, 1)))
    run_job(url)
    version = template_row(url, item["id"]).version
    paused = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": version, "is_active": False},
    )
    row = template_row(url, item["id"])
    assert (row.next_period_start, row.amount) == (date(2027, 2, 1), Decimal("8000.00"))

    frozen.set(local_noon(date(2027, 5, 1)))
    finance_staff = signed_in(url, client)
    resumed = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": paused["version"], "is_active": True},
    )
    assert resumed["next_period_start"] == "2027-05-01"
    run_job(url)

    periods = [row.period_start for row in generated(url, item["id"])]
    assert periods == [date(2027, 1, 1), date(2027, 5, 1)]


def test_c6_a_reactivation_never_moves_next_backwards(
    module_seeded_url, frozen, client, finance_staff, rent
):
    # Paused before its first (future) period: resuming keeps that period.
    item = _create(client, finance_staff, template_body(rent, date(2027, 3, 1)))
    changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "is_active": False}
    )

    resumed = changed(
        client, finance_staff.cookie, item["id"], {"version": 2, "is_active": True}
    )

    assert resumed["next_period_start"] == "2027-03-01"


def test_c6_a_same_day_resume_doesnt_go_back_to_a_generated_period(
    module_seeded_url, client, finance_staff, rent
):
    # Today's period is generated; the first period on or after today is
    # today's, so only greatest() keeps next on 7 January.
    url = module_seeded_url
    item = _create(client, finance_staff, template_body(rent, TODAY))
    run_job(url)
    version = template_row(url, item["id"]).version
    paused = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": version, "is_active": False},
    )

    resumed = changed(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": paused["version"], "is_active": True},
    )

    assert resumed["next_period_start"] == "2027-01-07"
    assert resumed["last_period_start"] == TODAY.isoformat()


def test_e6_ends_on_not_before_a_generated_period(
    module_seeded_url, frozen, client, finance_staff, rent
):
    url = module_seeded_url
    item = _create(client, finance_staff, template_body(rent, date(2026, 11, 7)))
    run_job(url)  # 7 Nov and 7 Dec
    version = template_row(url, item["id"]).version
    clear_queue(url)

    before = change(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": version, "ends_on": "2026-12-06"},
    )
    on_it = change(
        client,
        finance_staff.cookie,
        item["id"],
        {"version": version, "ends_on": "2026-12-07"},
    )

    assert code(before) == ENDS_ON
    assert field_errors(before) == [
        (("body", "ends_on"), "expense_template_ends_on_invalid")
    ]
    assert code(on_it) == OK
    assert on_it.json()["status"] == "ended"
    assert queued(url) == 1


# --- C7, C8: list and detail -----


def test_c8_the_list_sorts_by_status_then_name_and_filters(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    supplies = category_of(url, "supplies")
    b = _create(client, finance_staff, template_body(rent, TODAY, name="b activa"))
    a = _create(
        client,
        finance_staff,
        template_body(supplies, TODAY, name="A activa", vendor_id=vendor),
    )
    paused = _create(
        client, finance_staff, template_body(rent, TODAY, name="0 pausada")
    )
    changed(
        client, finance_staff.cookie, paused["id"], {"version": 1, "is_active": False}
    )
    ended = _create(
        client,
        finance_staff,
        template_body(rent, TODAY, name="0 terminada", ends_on=TODAY.isoformat()),
    )
    run_job(url)

    every = listed(client, finance_staff.cookie)

    assert [(t["id"], t["status"]) for t in every] == [
        (a["id"], "active"),
        (b["id"], "active"),
        (paused["id"], "paused"),
        (ended["id"], "ended"),
    ]
    by_id = {t["id"]: t for t in every}
    assert by_id[ended["id"]]["last_period_start"] == TODAY.isoformat()
    assert by_id[ended["id"]]["is_active"] is True
    assert by_id[paused["id"]]["last_period_start"] is None
    assert [t["id"] for t in listed(client, finance_staff.cookie, status="ended")] == [
        ended["id"]
    ]
    both = listed(client, finance_staff.cookie, status=["paused", "ended"])
    assert [t["id"] for t in both] == [paused["id"], ended["id"]]
    assert [
        t["id"] for t in listed(client, finance_staff.cookie, category_id=supplies)
    ] == [a["id"]]
    assert [
        t["id"] for t in listed(client, finance_staff.cookie, vendor_id=vendor)
    ] == [a["id"]]
    assert code(listing(client, finance_staff.cookie, status="gone")) == INVALID


def test_c8_inactive_categories_and_vendors_are_listed_and_filterable(
    module_seeded_url, client, finance_staff
):
    url = module_seeded_url
    category = new_category(url)
    vendor = new_vendor(url)
    item = _create(
        client,
        finance_staff,
        template_body(category, TODAY, vendor_id=vendor, starts_on="2027-01-01"),
    )
    changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "is_active": False}
    )
    set_active(url, "expense_categories", category, False)
    set_active(url, "vendors", vendor, False)

    [found] = listed(client, finance_staff.cookie, category_id=category)

    assert found["category"]["is_active"] is False
    assert found["vendor"]["is_active"] is False


def test_c8_detail_and_404(client, finance_staff, rent):
    item = _create(client, finance_staff, template_body(rent, TODAY))

    assert read(client, finance_staff.cookie, item["id"]).json() == item
    assert code(read(client, finance_staff.cookie, GONE)) == NOT_FOUND


def test_c7_the_list_is_one_statement_for_1_and_20(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url

    def counted() -> int:
        count, _ = count_statements(
            url, lambda s: finance.list_templates(s, finance.TemplateFilters())
        )
        return count

    _create(client, finance_staff, template_body(rent, TODAY))
    run_job(url)
    one = counted()
    for _ in range(19):
        _create(client, finance_staff, template_body(rent, TODAY))
    run_job(url)

    assert counted() == one == 1


# --- C9, C10: future periods only; the expense's template -----


def test_c9_a_change_keeps_generated_expenses_and_applies_to_the_next(
    module_seeded_url, frozen, client, finance_staff, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    supplies = category_of(url, "supplies")
    item = _create(
        client,
        finance_staff,
        template_body(rent, TODAY, name="Renta", vendor_id=vendor),
    )
    run_job(url)
    version = template_row(url, item["id"]).version
    changed(
        client,
        finance_staff.cookie,
        item["id"],
        {
            "version": version,
            "name": "Renta nueva",
            "amount": "9000.00",
            "category_id": supplies,
            "vendor_id": None,
            "due_offset_days": 0,
        },
    )

    # The worker is late: the change is committed before January is posted.
    frozen.set(local_noon(date(2027, 1, 9)))
    run_job(url)

    december, january = generated(url, item["id"])
    assert (december.description, december.amount, str(december.category_id)) == (
        "Renta",
        Decimal("8000.00"),
        rent,
    )
    assert (str(december.vendor_id), december.due_on) == (vendor, date(2026, 12, 12))
    assert (january.description, january.amount, str(january.category_id)) == (
        "Renta nueva",
        Decimal("9000.00"),
        supplies,
    )
    assert (january.vendor_id, january.due_on) == (None, date(2027, 1, 7))


def test_c10_the_expense_list_filters_and_names_the_template(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url
    item = _create(
        client, finance_staff, template_body(rent, date(2026, 11, 7), name="Renta")
    )
    other = _create(client, finance_staff, template_body(rent, TODAY, name="Luz"))
    run_job(url)
    client.cookies.clear()
    manual = client.post(
        EXPENSES,
        json={
            "category_id": rent,
            "description": "Compra",
            "amount": "10.00",
            "incurred_on": TODAY.isoformat(),
            "due_on": TODAY.isoformat(),
        },
        headers={
            **signed_headers(finance_staff.cookie),
            "Idempotency-Key": f"key-{uuid.uuid4().hex}",
        },
    )
    assert manual.status_code == 201, manual.text

    client.cookies.clear()
    page = client.get(
        EXPENSES,
        params={"template_id": item["id"], "limit": 100},
        headers=cookie_header(finance_staff.cookie),
    ).json()
    every = client.get(
        EXPENSES, params={"limit": 100}, headers=cookie_header(finance_staff.cookie)
    ).json()

    assert page["total"] == 2
    assert {e["template"]["id"] for e in page["items"]} == {item["id"]}
    assert {e["template"]["name"] for e in page["items"]} == {"Renta"}
    templates = {e["id"]: e["template"] for e in every["items"]}
    assert templates[manual.json()["id"]] is None
    assert sum(1 for t in templates.values() if t and t["id"] == other["id"]) == 1
    expense = page["items"][0]["id"]
    detail = client.get(
        f"{EXPENSES}/{expense}", headers=cookie_header(finance_staff.cookie)
    ).json()
    assert detail["template"] == {"id": item["id"], "name": "Renta"}
    assert detail["created_by"] is None
    assert detail["note"] is None and detail["receipt_reference"] is None


def test_c10_the_expense_list_is_still_two_statements(
    module_seeded_url, client, finance_staff, rent
):
    url = module_seeded_url

    def counted() -> int:
        count, _ = count_statements(
            url,
            lambda s: finance.list_expenses(
                s,
                filters=finance.ExpenseFilters(),
                sort="-incurred_on",
                page=finance_expenses.OffsetParams(limit=50, offset=0),
            ),
        )
        return count

    _create(client, finance_staff, template_body(rent, TODAY))
    run_job(url)
    one = counted()
    for _ in range(9):
        _create(client, finance_staff, template_body(rent, date(2026, 11, 7)))
    run_job(url)

    assert counted() == one == 2


# --- E1, E2: the deactivation refusal -----


def test_e1_a_live_template_blocks_its_category_and_vendor(
    module_seeded_url, client, finance_staff, administrator
):
    url = module_seeded_url
    category = new_category(url)
    vendor = new_vendor(url)
    for name in ("Uno", "Dos"):
        _create(
            client,
            finance_staff,
            template_body(category, TODAY, name=name, vendor_id=vendor),
        )

    for who in (finance_staff, administrator):
        refused = patch(
            client,
            who.cookie,
            f"{CATEGORIES}/{category}",
            {"version": 1, "is_active": False},
        )
        assert code(refused) == CATEGORY_IN_USE
        assert refused.json()["details"] == {"template_count": 2}
        vendor_refused = patch(
            client,
            who.cookie,
            f"{VENDORS}/{vendor}",
            {"version": 1, "is_active": False},
        )
        assert code(vendor_refused) == VENDOR_IN_USE
        assert vendor_refused.json()["details"] == {"template_count": 2}
        body = refused.text + vendor_refused.text
        assert "Uno" not in body and "8000.00" not in body
    # Renames are unaffected.
    renamed = patch(
        client,
        administrator.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 1, "name": "Arrendamiento"},
    )
    assert code(renamed) == OK


def test_e1_a_stale_version_comes_before_the_refusal(
    module_seeded_url, client, finance_staff
):
    url = module_seeded_url
    category = new_category(url)
    _create(client, finance_staff, template_body(category, TODAY))

    stale = patch(
        client,
        finance_staff.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 7, "is_active": False},
    )

    assert code(stale) == STALE


def test_e1_paused_and_ended_templates_dont_block(
    module_seeded_url, client, finance_staff
):
    url = module_seeded_url
    category = new_category(url)
    vendor = new_vendor(url)
    paused = _create(
        client, finance_staff, template_body(category, TODAY, vendor_id=vendor)
    )
    changed(
        client, finance_staff.cookie, paused["id"], {"version": 1, "is_active": False}
    )
    _create(
        client,
        finance_staff,
        template_body(category, TODAY, vendor_id=vendor, ends_on=TODAY.isoformat()),
    )
    run_job(url)  # ends the second

    deactivated = patch(
        client,
        finance_staff.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 1, "is_active": False},
    )
    vendor_off = patch(
        client,
        finance_staff.cookie,
        f"{VENDORS}/{vendor}",
        {"version": 1, "is_active": False},
    )
    reactivated = patch(
        client,
        finance_staff.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 2, "is_active": True},
    )

    assert code(deactivated) == code(vendor_off) == code(reactivated) == OK


# Races on real connections (E2): a template write against a deactivation.


def _template_create(category: str, vendor: str | None = None) -> Any:
    body = ExpenseTemplateCreate(
        name=f"Renta {uuid.uuid4().hex[:6]}",
        category_id=uuid.UUID(category),
        vendor_id=None if vendor is None else uuid.UUID(vendor),
        amount=Money("100.00"),
        frequency="monthly",
        starts_on=TODAY,
        due_offset_days=0,
    )

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.create_template(session, body)

    return call


def _deactivate_category(category: str) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance_categories.change_category(
            session,
            uuid.UUID(category),
            ExpenseCategoryUpdate(version=1, is_active=False),
        )

    return call


def _deactivate_vendor(vendor: str) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance_vendors.change_vendor(
            session, uuid.UUID(vendor), VendorUpdate(version=1, is_active=False)
        )

    return call


def test_e2_a_create_first_makes_the_category_deactivation_wait_and_refuse(
    module_seeded_url, monkeypatch
):
    url = module_seeded_url
    category = new_category(url)

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "ensure_usable",
        _template_create(category),
        _deactivate_category(category),
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError)
    assert second.code == "expense_category_in_use"
    assert second.details == {"template_count": 1}


def test_e2_a_category_deactivation_first_makes_the_create_wait_and_refuse(
    module_seeded_url, monkeypatch
):
    url = module_seeded_url
    category = new_category(url)

    first, second = overlap(
        url,
        monkeypatch,
        finance_categories,
        "change_category",
        _deactivate_category(category),
        _template_create(category),
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError)
    assert second.code == "expense_category_inactive"
    assert run_sql(url, "SELECT count(*) AS n FROM expense_templates")[0].n == 0


def test_e2_a_create_first_makes_the_vendor_deactivation_wait_and_refuse(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "ensure_usable",
        _template_create(rent, vendor),
        _deactivate_vendor(vendor),
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError)
    assert second.code == "vendor_in_use"


def test_e2_a_vendor_deactivation_first_makes_the_create_wait_and_refuse(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)

    first, second = overlap(
        url,
        monkeypatch,
        finance_vendors,
        "change_vendor",
        _deactivate_vendor(vendor),
        _template_create(rent, vendor),
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError)
    assert second.code == "vendor_inactive"


def test_e2_a_reactivation_and_a_deactivation_both_orders(
    module_seeded_url, monkeypatch, client, finance_staff
):
    url = module_seeded_url
    category = new_category(url)
    item = _create(client, finance_staff, template_body(category, TODAY))
    template = uuid.UUID(item["id"])
    changed(
        client, finance_staff.cookie, item["id"], {"version": 1, "is_active": False}
    )

    def reactivate(session: AsyncSession) -> Awaitable[Any]:
        return finance.change_template(
            session, template, ExpenseTemplateUpdate(version=2, is_active=True)
        )

    # The reactivation first: the deactivation waits, then sees it live.
    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "ensure_usable",
        reactivate,
        _deactivate_category(category),
    )
    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError) and second.code == "expense_category_in_use"

    # Paused again; the deactivation first: the reactivation waits and refuses.
    changed(
        client, finance_staff.cookie, item["id"], {"version": 3, "is_active": False}
    )

    def reactivate_again(session: AsyncSession) -> Awaitable[Any]:
        return finance.change_template(
            session, template, ExpenseTemplateUpdate(version=4, is_active=True)
        )

    first, second = overlap(
        url,
        monkeypatch,
        finance_categories,
        "change_category",
        _deactivate_category(category),
        reactivate_again,
    )
    assert not isinstance(first, BaseException), first
    assert isinstance(second, AppError) and second.code == "expense_category_inactive"
    assert template_row(url, item["id"]).is_active is False


# --- F1, F2: access, audit, logs -----


@pytest.fixture(scope="module")
def outsiders(module_seeded_url: str, client: TestClient) -> list[Staff]:
    url = module_seeded_url
    return [
        staff(url, client, (ADMINISTRATOR,), reauth=False),
        staff(
            url,
            client,
            (ADMINISTRATOR,),
            delegations=["finance.refund.approve"],
            reauth=False,
        ),
        staff(url, client, (ACADEMIC_COORDINATOR,), reauth=False),
        staff(url, client, (TUTOR,), reauth=False),
        staff(url, client, (STUDENT,), reauth=False),
        staff(url, client, (GUARDIAN,), reauth=False),
    ]


def test_f1_only_owner_and_finance_staff_reach_the_routes(
    module_seeded_url, client, owner, finance_staff, outsiders, rent
):
    item = _create(client, finance_staff, template_body(rent, TODAY, name="Renta"))

    for who in outsiders:
        attempts = (
            listing(client, who.cookie),
            read(client, who.cookie, item["id"]),
            create(client, who.cookie, template_body(rent, TODAY)),
            change(client, who.cookie, item["id"], {"version": 1, "amount": "1.00"}),
        )
        assert [code(a) for a in attempts] == [DENIED] * 4
    assert code(listing(client, owner.cookie)) == OK
    assert code(read(client, owner.cookie, item["id"])) == OK
    assert code(create(client, owner.cookie, template_body(rent, TODAY))) == CREATED
    assert (
        code(change(client, owner.cookie, item["id"], {"version": 1, "amount": "1.00"}))
        == OK
    )


def test_f2_the_audit_masks_name_and_amount_and_logs_hold_neither(
    module_seeded_url, client, finance_staff, administrator, rent, caplog
):
    url = module_seeded_url
    secrets = ("Renta secreta 4321", "7654.32", "5432.10", "Renta oculta")
    with caplog.at_level(logging.DEBUG):
        item = _create(
            client,
            finance_staff,
            template_body(rent, TODAY, name=secrets[0], amount=secrets[1]),
        )
        changed(
            client,
            finance_staff.cookie,
            item["id"],
            {"version": 1, "amount": secrets[2], "name": secrets[3]},
        )
        run_job(url)

    events = run_sql(
        url,
        "SELECT action, changes, actor_user_id FROM audit_events"
        " WHERE record_type = 'expense_templates' AND record_id = :i"
        " ORDER BY occurred_at, id",
        {"i": item["id"]},
    )
    assert [e.action for e in events] == ["create", "update", "update"]
    create_changes, update_changes = events[0].changes, events[1].changes
    for column in ("name", "amount"):
        assert create_changes[column]["new"] == "[masked]"
        assert update_changes[column] == {"old": "[masked]", "new": "[masked]"}
    assert create_changes["category_id"]["new"] == rent
    assert create_changes["frequency"]["new"] == "monthly"
    assert create_changes["starts_on"]["new"] == TODAY.isoformat()
    # The job's advance has no actor.
    assert events[2].actor_user_id is None
    assert set(events[2].changes) == {"next_period_start"}
    named = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE action LIKE 'finance.%'",
    )
    assert named[0].n == 0
    for secret in secrets:
        assert secret not in caplog.text, secret
    client.cookies.clear()
    seen = client.get(
        "/api/v1/audit-events",
        params={"record_type": "expense_templates", "record_id": item["id"]},
        headers=cookie_header(administrator.cookie),
    )
    assert seen.status_code == 200, seen.text
    assert rent in seen.text
    for secret in secrets:
        assert secret not in seen.text, secret
    assert json.dumps(seen.json()).count("[masked]") >= 4


def test_e10_no_route_deletes_a_template(module_seeded_url, client, owner, rent):
    item = _create(client, owner, template_body(rent, TODAY))
    client.cookies.clear()

    deleted = client.delete(
        template_path(item["id"]), headers=signed_headers(owner.cookie)
    )

    assert deleted.status_code == 405
    assert template_row(module_seeded_url, item["id"]).is_active is True


# --- A4: the reset -----


def test_a4_the_reset_clears_templates_after_their_expenses(
    module_seeded_url, client, owner, rent
):
    url = module_seeded_url
    vendor = new_vendor(url)
    _create(client, owner, template_body(rent, date(2026, 11, 7), vendor_id=vendor))
    run_job(url)
    assert run_sql(url, "SELECT count(*) AS n FROM expenses")[0].n == 2

    reset(url)

    for table in ("expenses", "expense_templates", "vendors"):
        assert run_sql(url, f"SELECT count(*) AS n FROM {table}")[0].n == 0, table  # noqa: S608
    assert run_sql(url, "SELECT count(*) AS n FROM expense_categories")[0].n == 5
