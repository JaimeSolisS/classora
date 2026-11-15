"""A tutor's earnings reads through the real routes (#113 C2, C3, C6, C7; E3-E6;
ADR-0017 E14, E18).

The tutor reads their own; who else reads, and who gets ``404``, is in
``test_payroll_earnings_access.py``. Periods reach ``finalized`` only by their
routes (Finance prepares, the Owner finalizes, oldest first), except in the
statement count test, which needs thirty.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.workflows import payroll_earnings
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payslip_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.invitation_support import sign_in
from tests.integration.payroll_calculation_support import (
    add_period_row,
    approved_lesson,
    clear,
    paid_tutor,
)
from tests.integration.payroll_payment_support import (
    NOW,
    STARTS,
    body,
    pay,
    reverse,
    worked_period,
)
from tests.integration.payroll_period_support import d, set_floor
from tests.integration.payroll_period_support import finalize as finalize_by_sql
from tests.integration.payroll_workflow_support import (
    OK,
    code,
    locked,
    prepare,
    read,
    recalculate,
)
from tests.integration.test_attendance import lesson, principal_of

ZONE = ZoneInfo("America/Mexico_City")
ITEM_KEYS = {
    "id",
    "starts_on",
    "ends_on",
    "pay_frequency",
    "finalized_on",
    "total",
    "paid_total",
    "paid_state",
    "last_paid_on",
    "line_count",
    "payslip",
}
DETAIL_KEYS = {"period", "lines", "summary", "total", "payments", "payslip"}
LINE_KEYS = {
    "kind",
    "amount",
    "service_date",
    "class_kind",
    "minutes",
    "rate_model",
    "rate",
    "covered_by_salary",
    "cumulative_sessions",
    "cohort_sessions",
    "pay_rule",
    "salary",
    "pay_item",
    "adjustment",
}
PAYMENT_KEYS = {"id", "paid_on", "amount", "method_code", "reference", "reversed_on"}
SUMMARY_KEYS = {
    "class_line_count",
    "class_minutes",
    "classes",
    "salary",
    "bonuses",
    "deductions",
    "adjustments",
}


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
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lee")


def ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


# --- C2: the list ---------------------------------------------------------------------


def test_c2_the_list_has_the_finalized_periods_newest_first(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    cookie = sign_in(client, tutor.username)

    page = ok(ps.earnings(client, cookie, tutor.id))

    assert (page["total"], page["limit"], page["offset"]) == (2, 25, 0)
    second, first = page["items"]
    assert set(second) == ITEM_KEYS
    assert [second["id"], first["id"]] == [str(rich.second), str(rich.first)]
    assert second["starts_on"] == "2026-11-09"
    assert second["pay_frequency"] == "weekly"
    assert second["finalized_on"] == "2026-12-07"
    [stored] = run_sql(
        url,
        "SELECT total_amount FROM payroll_periods WHERE id = :p",
        {"p": rich.second},
    )
    assert second["total"] == str(stored.total_amount)
    assert second["line_count"] == 5
    assert (second["paid_total"], second["paid_state"], second["last_paid_on"]) == (
        "0.00",
        "unpaid",
        None,
    )
    payslip = ps.payslip_of(url, rich.second)
    assert second["payslip"] == {"id": str(payslip.id), "number": payslip.number}


def test_c2_offset_pagination(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    cookie = sign_in(client, tutor.username)

    page = ok(ps.earnings(client, cookie, tutor.id, "?limit=1&offset=1"))

    assert page["total"] == 2
    assert [item["id"] for item in page["items"]] == [str(rich.first)]
    assert ps.earnings(client, cookie, tutor.id, "?limit=0").status_code == 422


def test_c2_an_empty_finalized_period_is_listed_at_0_without_a_payslip(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    empty = paid_tutor(url, tutor.id)
    locked(client, url, finance, owner, empty)
    cookie = sign_in(client, tutor.username)

    [item] = ok(ps.earnings(client, cookie, tutor.id))["items"]
    detail = ok(ps.earnings_period(client, cookie, tutor.id, empty))

    assert (item["total"], item["line_count"], item["payslip"]) == ("0.00", 0, None)
    assert item["paid_state"] == "paid"
    assert (detail["lines"], detail["total"], detail["payslip"]) == ([], "0.00", None)


def test_c2_a_period_finalized_before_payslips_has_none(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    finalize_by_sql(url, period, owner.user.id)
    cookie = sign_in(client, tutor.username)

    [item] = ok(ps.earnings(client, cookie, tutor.id))["items"]

    assert item["payslip"] is None


def test_c2_the_list_costs_the_same_at_1_and_at_30_periods(
    module_seeded_url, client, owner, finance
):
    url = module_seeded_url

    def tutor_with(count: int) -> CreatedUser:
        made = create_user(url, roles=(TUTOR,))
        first = paid_tutor(url, made.id, "09-01", "09-07")
        finalize_by_sql(url, first, owner.user.id)
        for index in range(1, count):
            starts = d("09-01") + timedelta(days=7 * index)
            period = add_period_row(
                url,
                made.id,
                starts.isoformat(),
                (starts + timedelta(days=6)).isoformat(),
                first=False,
            )
            finalize_by_sql(url, period, owner.user.id)
        return made

    one, thirty = tutor_with(1), tutor_with(30)

    def listing(who: CreatedUser) -> Any:
        principal = principal_of(who.id, TUTOR)
        return lambda session: payroll_earnings.list_earnings(
            session, principal, who.id, page=OffsetParams(limit=50, offset=0)
        )

    few, small = count_statements(url, listing(one))
    many, large = count_statements(url, listing(thirty))

    assert (small.total, large.total) == (1, 30)
    assert few == many


# --- C3: the detail -------------------------------------------------------------------


def test_c3_the_detail_has_the_exact_shape_and_the_live_lines(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    cookie = sign_in(client, tutor.username)

    detail = ok(ps.earnings_period(client, cookie, tutor.id, rich.second))

    assert set(detail) == DETAIL_KEYS
    assert set(detail["period"]) == ITEM_KEYS
    assert set(detail["summary"]) == SUMMARY_KEYS
    assert detail["period"]["id"] == str(rich.second)
    for line in detail["lines"]:
        assert set(line) == LINE_KEYS
    # The same projection as the payslip: the lines and summary match it.
    snapshot = ps.payslip_of(url, rich.second).snapshot
    assert detail["lines"] == snapshot["lines"]
    assert detail["summary"] == snapshot["summary"]
    assert detail["total"] == snapshot["total"]
    assert detail["payslip"]["number"] == ps.payslip_of(url, rich.second).number
    adjustment = next(line for line in detail["lines"] if line["kind"] == "adjustment")
    assert adjustment["adjustment"]["reason"] == ps.REASON


def test_c3_a_renamed_pay_item_type_shows_its_current_name_on_screen_only(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    run_sql(
        url,
        "UPDATE pay_item_types SET name = 'Bono renombrado' WHERE id = :t",
        {"t": rich.bonus_type},
    )
    cookie = sign_in(client, tutor.username)

    detail = ok(ps.earnings_period(client, cookie, tutor.id, rich.second))
    snapshot = ps.payslip_of(url, rich.second).snapshot

    names = {
        line["pay_item"]["type_name"] for line in detail["lines"] if line["pay_item"]
    }
    stored = {
        line["pay_item"]["type_name"] for line in snapshot["lines"] if line["pay_item"]
    }
    assert "Bono renombrado" in names and ps.BONUS_TYPE not in names
    assert ps.BONUS_TYPE in stored and "Bono renombrado" not in stored


# --- C6: only finalized amounts -------------------------------------------------------


def test_c6_only_the_finalized_period_is_summed_or_shown(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    done = paid_tutor(url, tutor.id)
    prepared = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    still_open = add_period_row(url, tutor.id, "11-16", "11-22", first=False)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    locked(client, url, finance, owner, done)
    # 77 minutes at 120 an hour: 154.00 in the prepared period.
    approved_lesson(
        client,
        lesson(url, starts_at=STARTS + timedelta(days=7), tutor=tutor),
        owner.user,
        minutes=77,
    )
    assert code(prepare(client, finance, prepared)) == OK
    # 83 minutes: 166.00 in the open period, by its recalculation route.
    approved_lesson(
        client,
        lesson(url, starts_at=STARTS + timedelta(days=14), tutor=tutor),
        owner.user,
        minutes=83,
    )
    assert code(recalculate(client, finance, still_open)) == OK
    # And approved work in no period yet.
    approved_lesson(
        client,
        lesson(url, starts_at=STARTS + timedelta(days=21), tutor=tutor),
        owner.user,
        minutes=91,
    )
    totals = {
        row.id: row.total
        for row in run_sql(
            url,
            "SELECT payroll_period_id AS id, sum(amount) AS total FROM payroll_lines"
            " WHERE tutor_user_id = :t GROUP BY payroll_period_id",
            {"t": tutor.id},
        )
    }
    assert str(totals[prepared]) == "154.00"
    assert str(totals[still_open]) == "166.00"
    cookie = sign_in(client, tutor.username)

    page = ps.earnings(client, cookie, tutor.id)
    detail = ps.earnings_period(client, cookie, tutor.id, done)

    assert [item["id"] for item in ok(page)["items"]] == [str(done)]
    assert ok(detail)["total"] == "110.00"
    for response in (page, detail):
        for hidden in ("154.00", "166.00", "182.00"):
            assert hidden not in response.text, hidden
    for hidden_period in (prepared, still_open):
        assert_same_not_found(
            ps.earnings_period(client, cookie, tutor.id, hidden_period),
            ps.earnings_period(client, cookie, tutor.id, uuid.uuid4()),
        )


# --- C7: payments (E5) ----------------------------------------------------------------


def test_c7_payments_partial_and_reversed_without_note_or_reason(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    locked(client, url, finance, owner, period)
    other_tutor = create_user(url, roles=(TUTOR,))
    other = worked_period(url, client, owner, other_tutor)
    locked(client, url, finance, owner, other)
    kept = pay(
        client,
        finance,
        period,
        body("30.00", "SPEI 1", paid_on="2026-11-20", note="Nota interna"),
    )
    wrong = pay(client, finance, period, body("50.00", "SPEI 2", paid_on="2026-11-25"))
    assert code(kept) == code(wrong) == (201, None)
    assert code(reverse(client, owner, wrong.json()["id"], "Razón privada")) == (
        201,
        None,
    )
    assert code(pay(client, finance, other, body("20.00", "SPEI OTRO"))) == (201, None)
    [reversal] = run_sql(
        url,
        "SELECT created_at FROM payroll_payment_reversals"
        " WHERE payroll_payment_id = :p",
        {"p": wrong.json()["id"]},
    )
    reversed_on = reversal.created_at.astimezone(ZONE).date().isoformat()
    cookie = sign_in(client, tutor.username)

    detail = ps.earnings_period(client, cookie, tutor.id, period)
    [item] = ok(ps.earnings(client, cookie, tutor.id))["items"]

    payments = ok(detail)["payments"]
    assert [set(p) for p in payments] == [PAYMENT_KEYS, PAYMENT_KEYS]
    assert [(p["reference"], p["amount"], p["reversed_on"]) for p in payments] == [
        ("SPEI 2", "50.00", reversed_on),
        ("SPEI 1", "30.00", None),
    ]
    assert payments[1]["method_code"] == "transfer"
    for hidden in ("Nota interna", "Razón privada", "SPEI OTRO", "recorded_by"):
        assert hidden not in detail.text, hidden
    period_item = ok(detail)["period"]
    assert (period_item["paid_total"], period_item["paid_state"]) == (
        "30.00",
        "partially_paid",
    )
    assert period_item["last_paid_on"] == "2026-11-20"
    assert item == period_item
    staff_view = read(client, finance, period).json()["period"]
    assert (staff_view["paid_total"], staff_view["paid_state"]) == (
        period_item["paid_total"],
        period_item["paid_state"],
    )
