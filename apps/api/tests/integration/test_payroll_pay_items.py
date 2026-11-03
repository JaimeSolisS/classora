"""Bonuses and deductions through the real run and routes (#109 C-H, J, L;
ADR-0017 E8, its #109 notes). The placement is
``tests/unit/test_payroll_pay_items_pure.py``, the races
``test_payroll_calculation_concurrency.py``, the guard ``test_migrations.py``.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import REPLAYED_HEADER
from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    STUDENT,
    TUTOR,
)
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import PayrollPeriodSummary
from app.workflows import payroll_calculation
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_terms,
    clear,
    item_lines,
    lines,
    paid_tutor,
    run,
    stable,
)
from tests.integration.payroll_period_support import finalize, set_floor
from tests.integration.test_payroll_calculation import (
    NOW,
    local,
    stored_approvals,
    worked,
)

ROOT = "/api/v1/payroll"
TYPES = f"{ROOT}/pay-item-types"
DENIED = (403, "permission_denied")
OWN = (403, "payroll_own_compensation")
IN_USE = (409, "pay_item_in_use")
STALE = (409, "version_conflict")
NOT_FOUND = (404, "not_found")
OUT_OF_RANGE = (409, "payroll_amount_out_of_range")
MONEY_LIMIT = "999999999999.99"
ITEM_CANARY = "4321.77"
TEXT_CANARY = "embargo-canario"
NO_ACCESS = ("administrator", "delegated", "coordinator", "guardian", "student")


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str, frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)
    clear(module_seeded_url)
    run_sql(module_seeded_url, "UPDATE payroll_settings SET payroll_starts_on = NULL")


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    return {
        "owner": create_user(url, roles=(OWNER,)),
        "finance": create_user(url, roles=(FINANCE_STAFF,)),
        "administrator": create_user(url, roles=(ADMINISTRATOR,)),
        "delegated": create_user(
            url, roles=(ADMINISTRATOR,), delegations=[PAYROLL_FINALIZE]
        ),
        "coordinator": create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        "guardian": create_user(url, roles=(GUARDIAN,)),
        "student": create_user(url, roles=(STUDENT,)),
        "finance_tutor": create_user(url, roles=(FINANCE_STAFF, TUTOR)),
        "owner_tutor": create_user(url, roles=(OWNER, TUTOR)),
        "plain_tutor": create_user(url, roles=(TUTOR,)),
    }


@pytest.fixture
def cookies(
    client: TestClient, users: dict[str, CreatedUser], frozen: FrozenClock
) -> dict[str, SetCookie]:
    return {name: sign_in(client, user.username) for name, user in users.items()}


@pytest.fixture
def staff(cookies: dict[str, SetCookie]) -> SetCookie:
    return cookies["finance"]


@pytest.fixture
def owner(users: dict[str, CreatedUser]) -> CreatedUser:
    return users["owner"]


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


def key() -> str:
    return f"key-{uuid.uuid4().hex}"


def items_path(tutor: uuid.UUID, item: uuid.UUID | str | None = None) -> str:
    base = f"{ROOT}/tutors/{tutor}/pay-items"
    return base if item is None else f"{base}/{item}"


def post_item(
    client: TestClient,
    cookie: SetCookie,
    tutor: uuid.UUID,
    body: dict[str, Any],
    idempotency: str | None = "new",
) -> Response:
    client.cookies.clear()
    headers = signed_headers(cookie)
    if idempotency is not None:
        headers["Idempotency-Key"] = key() if idempotency == "new" else idempotency
    return client.post(items_path(tutor), json=body, headers=headers)


def item_body(pay_type: uuid.UUID, **changes: Any) -> dict[str, Any]:
    return {
        "pay_item_type_id": str(pay_type),
        "description": "Bono de asistencia",
        "amount": "100.00",
        "recurrence": "one_time",
        "effective_from": "2026-11-03",
        **changes,
    }


def counted(url: str, table: str, tutor: uuid.UUID) -> int:
    return int(
        run_sql(
            url,
            f"SELECT count(*) AS n FROM {table} WHERE tutor_user_id = :u",  # noqa: S608
            {"u": tutor},
        )[0].n
    )


def set_prepared(url: str, period: uuid.UUID, by: uuid.UUID) -> None:
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :u WHERE id = :p",
        {"u": by, "p": period},
    )


def read_lines(client: TestClient, cookie: SetCookie, period: uuid.UUID) -> Any:
    return get(client, cookie, f"{ROOT}/periods/{period}/lines").json()


# --- C: the run -----


def test_a_bonus_and_a_deduction_are_signed_lines(module_seeded_url, tutor):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    bonus = add_item(url, tutor.id, add_pay_type(url), "150.25", "11-03")
    deduction = add_item(
        url, tutor.id, add_pay_type(url, direction="deduction"), "50.10", "11-04"
    )

    result = run(url, period)

    assert (result.line_count, str(result.total)) == (2, "100.15")
    found = {line.pay_item_id: line for line in item_lines(url, period)}
    assert found[bonus].amount == Decimal("150.25")
    assert found[deduction].amount == Decimal("-50.10")
    line = found[bonus]
    assert (line.class_session_id, line.approval_id, line.rate_model) == (
        None,
        None,
        None,
    )
    assert (line.service_date, line.salary_amount) == (None, None)


def test_c5_a_negative_total_shows_on_both_routes(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "75.00")

    response = post(client, staff, f"{ROOT}/periods/{period}/recalculate", None)

    assert code_of(response) == (200, None), response.text
    assert response.json() == {"line_count": 1, "total": "-75.00"}
    assert read_lines(client, staff, period)["total"] == "-75.00"


def test_c6_a_recurring_item_is_one_line_per_period_across_runs(
    module_seeded_url, tutor
):
    """The issue's criterion: two runs, then two periods."""
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )

    run(url, first)
    before = stable(lines(url, first))
    old_ids = [line.id for line in lines(url, first)]
    run(url, first)
    run(url, second)

    assert stable(lines(url, first)) == before
    assert [line.id for line in lines(url, first)] != old_ids
    assert [line.pay_item_id for line in lines(url, first)] == [item]
    assert [line.pay_item_id for line in lines(url, second)] == [item]


def test_c7_a_run_touches_its_own_period_and_tutor_only(module_seeded_url, tutor):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,))
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    others = add_period_row(url, other.id, "11-02", "11-08")
    add_item(url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring")
    add_item(url, other.id, add_pay_type(url), "7.00", on="11-03")
    run(url, second)
    kept = [line.id for line in lines(url, second)]

    run(url, first)
    run(url, others)

    assert [line.id for line in lines(url, second)] == kept
    assert [line.amount for line in lines(url, others)] == [Decimal("7.00")]


def test_c7_an_item_line_keeps_no_work_off_the_unresolved_list(
    module_seeded_url, client, staff, owner, tutor
):
    url = module_seeded_url
    set_floor(url, "10-01")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    add_item(url, tutor.id, add_pay_type(url))
    run(url, period)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)

    run(url, period)

    assert len(lines(url, period)) == 1
    response = get(
        client, staff, f"{ROOT}/unresolved-work", tutor_user_id=str(tutor.id)
    )
    assert [item["reason"] for item in response.json()["items"]] == ["no_pay_terms"]


def test_c4_a_total_beyond_the_range_writes_nothing(module_seeded_url, tutor):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url)
    add_item(url, tutor.id, pay_type, MONEY_LIMIT)
    run(url, period)
    kept = [line.id for line in lines(url, period)]
    add_item(url, tutor.id, pay_type, "0.01")

    with pytest.raises(Exception) as refused:  # noqa: PT011
        run(url, period)

    assert getattr(refused.value, "code", None) == OUT_OF_RANGE[1]
    assert [line.id for line in lines(url, period)] == kept


def test_a_deduction_brings_a_large_total_back_in_range(module_seeded_url, tutor):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url)
    add_item(url, tutor.id, pay_type, MONEY_LIMIT)
    add_item(url, tutor.id, pay_type, MONEY_LIMIT)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), MONEY_LIMIT)

    assert str(run(url, period).total) == MONEY_LIMIT


def statements(url: str, period: uuid.UUID) -> tuple[int, Any]:
    return count_statements(url, lambda s: payroll_calculation.recalculate(s, period))


def test_c8_items_cost_the_same_statements_whatever_their_number(
    module_seeded_url, owner, tutor
):
    """Twelve with no item (``test_c4_statements_with_and_without_a_salary``),
    twelve with recurring items, fourteen with any one-time item."""
    url = module_seeded_url
    recurring_tutor = create_user(url, roles=(TUTOR,))
    recurring_period = paid_tutor(url, recurring_tutor.id)
    one_time_tutor = create_user(url, roles=(TUTOR,))
    one_time_period = paid_tutor(url, one_time_tutor.id)
    pay_type = add_pay_type(url)
    add_item(url, recurring_tutor.id, pay_type, on="10-01", recurrence="recurring")
    add_item(url, one_time_tutor.id, pay_type)
    stored_approvals(url, recurring_tutor, owner, 2)
    stored_approvals(url, one_time_tutor, owner, 2)

    assert statements(url, recurring_period)[0] == 12
    assert statements(url, one_time_period)[0] == 14

    for _ in range(5):
        add_item(url, recurring_tutor.id, pay_type, on="10-01", recurrence="recurring")
        add_item(url, one_time_tutor.id, pay_type)

    assert statements(url, recurring_period)[0] == 12
    count, result = statements(url, one_time_period)
    assert count == 14
    assert result.line_count == 8


def test_an_older_finalized_one_time_item_isnt_paid_again(module_seeded_url, tutor):
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    add_item(url, tutor.id, add_pay_type(url), on="11-03")
    run(url, first)
    finalize(url, first, tutor.id)

    assert run(url, second).line_count == 0


def test_a_one_time_item_in_a_finalized_period_lands_in_the_next(
    module_seeded_url, tutor
):
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    finalize(url, first, tutor.id)
    item = add_item(url, tutor.id, add_pay_type(url), on="11-03")

    run(url, second)

    assert [line.pay_item_id for line in lines(url, second)] == [item]


def test_a_later_period_drops_the_line_once_an_earlier_one_can_take_it(
    module_seeded_url, tutor
):
    """E3 has one answer in every state, so a run of the later period can't
    keep paying what the earlier one owns."""
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    add_item(url, tutor.id, add_pay_type(url), on="11-03")

    run(url, first)
    assert run(url, second).line_count == 0
    assert len(lines(url, first)) == 1


# --- G, H: reading lines -----


def test_g1_a_line_shows_the_types_current_name_and_the_description(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url, "Puntualidad", direction="deduction")
    item = add_item(url, tutor.id, pay_type, "12.50", description="Retardo")
    run(url, period)
    version = get(client, staff, TYPES).json()[0]["version"]
    renamed = patch(
        client, staff, f"{TYPES}/{pay_type}", {"version": version, "name": "Retardos"}
    )
    assert code_of(renamed) == (200, None), renamed.text

    [line] = read_lines(client, staff, period)["lines"]

    assert line["kind"] == "pay_item"
    assert line["amount"] == "-12.50"
    assert line["pay_item_id"] == str(item)
    assert line["pay_item_type_name"] == "Retardos"
    assert line["pay_item_description"] == "Retardo"
    assert line["rate"] is None


def test_g1_other_kinds_have_no_item_text(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id, "10-01", salary="1000.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    run(url, period)

    [line] = read_lines(client, staff, period)["lines"]

    assert line["kind"] == "salary"
    assert (
        line["pay_item_id"],
        line["pay_item_type_name"],
        line["pay_item_description"],
    ) == (None, None, None)


def test_g3_the_lines_read_is_two_statements_whatever_the_items(
    module_seeded_url, tutor
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url)
    for _ in range(4):
        add_item(url, tutor.id, pay_type)
    run(url, period)

    count, result = count_statements(url, lambda s: payroll.period_lines(s, period))

    assert count == 2
    assert len(result.lines) == 4


def test_h1_drafts_come_sessions_then_salary_then_items(
    module_seeded_url, client, owner, tutor
):
    """#108 QA O3. The drafts themselves, before any sort a read applies."""
    url = module_seeded_url
    add_terms(url, tutor.id, "10-01", salary="1000.00")
    add_rate(url, tutor.id, "10-01")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    add_item(url, tutor.id, add_pay_type(url))
    add_item(url, tutor.id, add_pay_type(url), on="10-20")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    summary = run_sql(
        url,
        "SELECT id, tutor_user_id, starts_on, ends_on, pay_frequency, status"
        " FROM payroll_periods WHERE id = :p",
        {"p": period},
    )[0]

    async def compute(session: AsyncSession) -> Any:
        return await payroll_calculation.compute_lines(
            session, PayrollPeriodSummary.model_validate(summary, from_attributes=True)
        )

    drafts = in_uow(url, compute)

    assert [draft.kind for draft in drafts] == [
        "session",
        "salary",
        "pay_item",
        "pay_item",
    ]


def test_h2_lines_sort_whatever_order_they_went_in(
    module_seeded_url, client, staff, owner, tutor
):
    """#108 QA O4: the rows are inserted backwards, so a missing sort key can't
    hide behind insertion order."""
    url = module_seeded_url
    terms = add_terms(url, tutor.id, "10-01", salary="1000.00")
    period = add_period_row(url, tutor.id, "11-02", "11-29", "monthly")
    pay_type = add_pay_type(url)
    items = sorted(add_item(url, tutor.id, pay_type) for _ in range(2))
    insert_item = (
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " pay_item_id) VALUES (:p, :t, 'pay_item', 1, :i)"
    )
    insert_salary = (
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " tutor_pay_terms_id, salary_amount, salary_starts_on, salary_ends_on,"
        " proration_numerator, proration_denominator, proration_basis)"
        " VALUES (:p, :t, 'salary', 1, :terms, 1, :first, :last, 7, 7,"
        " 'calendar_days')"
    )
    base = {"p": period, "t": tutor.id}
    for item in reversed(items):
        run_sql(url, insert_item, {**base, "i": item})
    for first, last in (("2026-11-16", "2026-11-22"), ("2026-11-02", "2026-11-08")):
        run_sql(
            url,
            insert_salary,
            {
                **base,
                "terms": terms,
                "first": date.fromisoformat(first),
                "last": date.fromisoformat(last),
            },
        )

    found = read_lines(client, staff, period)["lines"]

    assert [(line["kind"], line["salary_starts_on"]) for line in found[:2]] == [
        ("salary", "2026-11-02"),
        ("salary", "2026-11-16"),
    ]
    assert [line["pay_item_id"] for line in found[2:]] == [str(i) for i in items]


# --- D: types -----


def test_d1_types_come_active_first_then_by_name(module_seeded_url, client, staff):
    url = module_seeded_url
    add_pay_type(url, "zeta")
    add_pay_type(url, "Alfa", active=False)
    add_pay_type(url, "Beta", direction="deduction")

    response = get(client, staff, TYPES)

    assert [(row["name"], row["is_active"]) for row in response.json()] == [
        ("Beta", True),
        ("zeta", True),
        ("Alfa", False),
    ]


def test_d2_add_a_type_and_refuse_its_name_ignoring_case(client, staff):
    first = post(client, staff, TYPES, {"name": " Bono ", "direction": "bonus"})

    assert code_of(first) == (201, None), first.text
    body = first.json()
    assert (body["name"], body["direction"], body["is_active"], body["version"]) == (
        "Bono",
        "bonus",
        True,
        1,
    )
    again = post(client, staff, TYPES, {"name": "bONO", "direction": "deduction"})
    assert code_of(again) == (409, "pay_item_type_exists")


def test_d3_patch_renames_and_keeps_the_rules(module_seeded_url, client, staff):
    url = module_seeded_url
    pay_type = add_pay_type(url, "Uno")
    add_pay_type(url, "Dos")
    path = f"{TYPES}/{pay_type}"

    renamed = patch(client, staff, path, {"version": 1, "name": "Tres"})
    assert code_of(renamed) == (200, None), renamed.text
    assert (renamed.json()["name"], renamed.json()["version"]) == ("Tres", 2)

    assert code_of(patch(client, staff, path, {"version": 1, "name": "X"})) == STALE
    assert code_of(patch(client, staff, path, {"version": 2, "name": "dos"})) == (
        409,
        "pay_item_type_exists",
    )
    assert code_of(
        patch(client, staff, path, {"version": 2, "direction": "deduction"})
    ) == (422, "validation_error")
    assert (
        code_of(patch(client, staff, f"{TYPES}/{uuid.uuid4()}", {"version": 1}))
        == NOT_FOUND
    )


def test_d3_an_unchanged_patch_writes_nothing(module_seeded_url, client, staff):
    pay_type = add_pay_type(module_seeded_url, "Igual")

    response = patch(client, staff, f"{TYPES}/{pay_type}", {"version": 1})

    assert (response.status_code, response.json()["version"]) == (200, 1)


@pytest.mark.parametrize(
    ("until", "expected"),
    [
        (None, (409, "pay_item_type_in_use")),
        # NOW is 2026-12-07 in the center's zone, or the day before.
        ("2026-12-31", (409, "pay_item_type_in_use")),
        ("2026-11-30", (200, None)),
    ],
)
def test_d4_deactivating_refuses_while_a_recurring_item_runs(
    module_seeded_url, client, staff, tutor, until, expected
):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    add_item(
        url, tutor.id, pay_type, on="2026-01-01", until=until, recurrence="recurring"
    )

    response = patch(
        client, staff, f"{TYPES}/{pay_type}", {"version": 1, "is_active": False}
    )

    assert code_of(response) == expected, response.text


def test_d4_the_boundary_is_the_centers_today(module_seeded_url, client, staff, tutor):
    """#109 QA O2: an end of today still runs today, and yesterday's doesn't."""
    url = module_seeded_url
    today = in_uow(url, administration.center_today)
    running = add_pay_type(url)
    ended = add_pay_type(url)
    add_item(
        url,
        tutor.id,
        running,
        on="2026-01-01",
        until=today.isoformat(),
        recurrence="recurring",
    )
    add_item(
        url,
        tutor.id,
        ended,
        on="2026-01-01",
        until=(today - timedelta(days=1)).isoformat(),
        recurrence="recurring",
    )
    off = {"version": 1, "is_active": False}

    refused = patch(client, staff, f"{TYPES}/{running}", off)
    passed = patch(client, staff, f"{TYPES}/{ended}", off)

    assert code_of(refused) == (409, "pay_item_type_in_use")
    assert code_of(passed) == (200, None), passed.text


def test_d4_a_one_time_item_doesnt_hold_a_type_and_reactivating_passes(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    add_item(url, tutor.id, pay_type)
    path = f"{TYPES}/{pay_type}"

    off = patch(client, staff, path, {"version": 1, "is_active": False})
    assert (off.status_code, off.json()["is_active"]) == (200, False)
    on = patch(client, staff, path, {"version": 2, "is_active": True})
    assert (on.status_code, on.json()["is_active"]) == (200, True)


def test_d5_there_is_no_delete(module_seeded_url, client, staff):
    pay_type = add_pay_type(module_seeded_url)
    assert delete(client, staff, f"{TYPES}/{pay_type}").status_code == 405


# --- F: items -----


def test_f2_add_and_list_an_item(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url, "Bono", direction="deduction")

    response = post_item(
        client, staff, tutor.id, item_body(pay_type, description="  Préstamo  ")
    )

    assert code_of(response) == (201, None), response.text
    body = response.json()
    assert body["tutor_user_id"] == str(tutor.id)
    assert body["pay_item_type"] == {
        "id": str(pay_type),
        "name": "Bono",
        "direction": "deduction",
    }
    assert body["description"] == "Préstamo"
    assert (body["amount"], body["recurrence"]) == ("100.00", "one_time")
    assert (body["effective_from"], body["effective_to"]) == ("2026-11-03", None)
    assert (body["version"], body["applied"]) == (1, False)
    assert body["placed_period"] == {
        "id": str(period),
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-08",
        "status": "open",
    }
    listed = get(client, staff, items_path(tutor.id)).json()
    assert listed["total"] == 1
    assert listed["items"] == [body]


def test_f2_a_retry_with_the_key_replays_and_another_body_is_refused(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    same = key()

    first = post_item(client, staff, tutor.id, item_body(pay_type), same)
    second = post_item(client, staff, tutor.id, item_body(pay_type), same)
    other = post_item(
        client, staff, tutor.id, item_body(pay_type, amount="200.00"), same
    )

    assert first.status_code == second.status_code == 201
    assert second.headers.get(REPLAYED_HEADER) == "true"
    assert second.json() == first.json()
    assert code_of(other) == (422, "idempotency_key_reused")
    assert counted(url, "pay_items", tutor.id) == 1


def test_f2_a_post_needs_a_key(module_seeded_url, client, staff, tutor):
    pay_type = add_pay_type(module_seeded_url)

    response = post_item(client, staff, tutor.id, item_body(pay_type), None)

    assert code_of(response) == (400, "idempotency_key_required")


def test_f3_a_type_must_exist_and_be_active(module_seeded_url, client, staff, tutor):
    url = module_seeded_url

    unknown = post_item(client, staff, tutor.id, item_body(uuid.uuid4()))
    inactive = post_item(
        client, staff, tutor.id, item_body(add_pay_type(url, active=False))
    )

    assert code_of(unknown) == (422, "pay_item_type_unknown")
    assert code_of(inactive) == (409, "pay_item_type_inactive")


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0.00"},
        {"amount": "1.001"},
        {"description": " "},
        {"description": "a\x00b"},
        {"effective_to": "2026-12-01"},
        {"recurrence": "recurring", "effective_to": "2026-11-02"},
    ],
)
def test_f3_a_bad_body_is_422(module_seeded_url, client, staff, tutor, changes):
    pay_type = add_pay_type(module_seeded_url)

    response = post_item(client, staff, tutor.id, item_body(pay_type, **changes))

    assert code_of(response) == (422, "validation_error"), response.text


@pytest.mark.parametrize("on", ["2000-01-01", "2090-06-01"])
def test_f3_any_start_is_accepted(module_seeded_url, client, staff, tutor, on):
    pay_type = add_pay_type(module_seeded_url)

    response = post_item(
        client, staff, tutor.id, item_body(pay_type, effective_from=on)
    )

    assert response.status_code == 201
    assert response.json()["placed_period"] is None


def test_f1_placed_period_and_applied(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    third = add_period_row(url, tutor.id, "11-16", "11-22", first=False)
    pay_type = add_pay_type(url)
    paid = add_item(url, tutor.id, pay_type, on="11-03")
    run(url, first)
    finalize(url, first, tutor.id)
    set_prepared(url, second, tutor.id)
    in_prepared = add_item(url, tutor.id, pay_type, on="11-04")
    in_open = add_item(url, tutor.id, pay_type, on="11-20")
    waiting = add_item(url, tutor.id, pay_type, on="12-20")
    repeating = add_item(url, tutor.id, pay_type, on="10-01", recurrence="recurring")

    found = {
        item["id"]: item
        for item in get(client, staff, items_path(tutor.id)).json()["items"]
    }

    assert found[str(paid)]["applied"] is True
    assert found[str(paid)]["placed_period"] is None
    assert found[str(in_prepared)]["placed_period"]["id"] == str(second)
    assert found[str(in_prepared)]["placed_period"]["status"] == "prepared"
    assert found[str(in_open)]["placed_period"]["id"] == str(third)
    assert found[str(waiting)]["placed_period"] is None
    assert found[str(repeating)]["placed_period"] is None
    assert found[str(repeating)]["applied"] is False


def test_f1_newest_start_first_and_paged(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    old = add_item(url, tutor.id, pay_type, on="10-01")
    new_one, new_two = sorted(
        add_item(url, tutor.id, pay_type, on="11-01") for _ in range(2)
    )

    page = get(client, staff, items_path(tutor.id), limit=2).json()
    rest = get(client, staff, items_path(tutor.id), limit=2, offset=2).json()

    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == [str(new_one), str(new_two)]
    assert [item["id"] for item in rest["items"]] == [str(old)]


def test_f1_the_page_costs_the_same_whatever_its_size(module_seeded_url, tutor):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    add_item(url, tutor.id, pay_type)
    few, _ = count_statements(
        url,
        lambda s: payroll.list_pay_items(s, tutor.id, OffsetParams(limit=50, offset=0)),
    )
    for _ in range(4):
        add_item(url, tutor.id, pay_type)
    many, result = count_statements(
        url,
        lambda s: payroll.list_pay_items(s, tutor.id, OffsetParams(limit=50, offset=0)),
    )

    assert few == many == 4
    assert len(result.items) == 5


def test_f4_an_id_that_isnt_a_tutor_and_another_tutors_item_are_the_same_404(
    module_seeded_url, client, staff, users, tutor
):
    url = module_seeded_url
    pay_type = add_pay_type(url)
    other = create_user(url, roles=(TUTOR,))
    item = add_item(url, other.id, pay_type, on="10-01", recurrence="recurring")
    not_a_tutor = users["coordinator"].id
    end = {"version": 1, "effective_to": None}

    assert code_of(get(client, staff, items_path(not_a_tutor))) == NOT_FOUND
    assert (
        code_of(post_item(client, staff, not_a_tutor, item_body(pay_type))) == NOT_FOUND
    )
    assert code_of(patch(client, staff, items_path(tutor.id, item), end)) == NOT_FOUND
    assert code_of(delete(client, staff, items_path(tutor.id, item))) == NOT_FOUND
    assert code_of(delete(client, staff, items_path(not_a_tutor, item))) == NOT_FOUND
    assert counted(url, "pay_items", other.id) == 1


def test_an_item_for_a_tutor_with_no_terms_or_periods_waits(
    module_seeded_url, client, staff, tutor
):
    pay_type = add_pay_type(module_seeded_url)

    response = post_item(client, staff, tutor.id, item_body(pay_type))

    assert response.status_code == 201
    assert response.json()["placed_period"] is None


def test_f5_end_and_reopen_a_recurring_item(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    path = items_path(tutor.id, item)

    ended = patch(client, staff, path, {"version": 1, "effective_to": "2026-11-30"})
    assert code_of(ended) == (200, None), ended.text
    assert (ended.json()["effective_to"], ended.json()["version"]) == ("2026-11-30", 2)

    assert (
        code_of(patch(client, staff, path, {"version": 1, "effective_to": None}))
        == STALE
    )
    reopened = patch(client, staff, path, {"version": 2, "effective_to": None})
    assert reopened.json()["effective_to"] is None


def test_f5_a_one_time_item_has_no_end(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    item = add_item(url, tutor.id, add_pay_type(url))

    response = patch(
        client,
        staff,
        items_path(tutor.id, item),
        {"version": 1, "effective_to": "2026-12-01"},
    )

    assert code_of(response) == (409, "pay_item_not_recurring")


def test_f5_the_end_cant_precede_the_start(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )

    response = patch(
        client,
        staff,
        items_path(tutor.id, item),
        {"version": 1, "effective_to": "2026-09-30"},
    )

    assert code_of(response) == (422, "pay_item_end_before_start")


def test_f5_an_applied_item_ends_but_never_before_its_paid_line(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    run(url, period)
    finalize(url, period, tutor.id)
    path = items_path(tutor.id, item)

    early = patch(client, staff, path, {"version": 1, "effective_to": "2026-11-01"})
    edge = patch(client, staff, path, {"version": 1, "effective_to": "2026-11-02"})

    assert code_of(early) == (422, "pay_item_end_before_paid_period")
    assert code_of(edge) == (200, None), edge.text
    assert edge.json()["applied"] is True


def test_f6_delete_takes_the_open_lines_with_it(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    run(url, first)
    run(url, second)

    response = delete(client, staff, items_path(tutor.id, item))

    assert response.status_code == 204
    assert counted(url, "pay_items", tutor.id) == 0
    assert lines(url, first) == lines(url, second) == []


@pytest.mark.parametrize("status", ["prepared", "finalized"])
def test_f6_a_used_item_refuses_and_keeps_every_line(
    module_seeded_url, client, staff, tutor, status
):
    url = module_seeded_url
    used = add_period_row(url, tutor.id, "11-02", "11-08")
    still_open = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    run(url, used)
    run(url, still_open)
    kept = [line.id for line in lines(url, still_open)]
    if status == "prepared":
        set_prepared(url, used, tutor.id)
    else:
        finalize(url, used, tutor.id)

    response = delete(client, staff, items_path(tutor.id, item))

    assert code_of(response) == IN_USE
    assert counted(url, "pay_items", tutor.id) == 1
    assert [line.id for line in lines(url, still_open)] == kept


SURROGATE_BODY = '{"name": "a\\ud800b", "direction": "bonus"}'


def raw_post(
    client: TestClient, cookie: SetCookie, path: str, body: str, *, keyed: bool = False
) -> Response:
    """``body`` as sent: ``json=`` can't carry a lone surrogate."""
    client.cookies.clear()
    headers = {**signed_headers(cookie), "Content-Type": "application/json"}
    if keyed:
        headers["Idempotency-Key"] = key()
    return client.post(path, content=body.encode(), headers=headers)


def errors_of(response: Response) -> list[tuple[Any, str]]:
    return [(tuple(e["path"]), e["code"]) for e in response.json()["errors"]]


def test_d2_a_lone_surrogate_in_a_types_name_is_422(module_seeded_url, client, staff):
    pay_type = add_pay_type(module_seeded_url)

    created = raw_post(client, staff, TYPES, SURROGATE_BODY)
    client.cookies.clear()
    renamed = client.patch(
        f"{TYPES}/{pay_type}",
        content=b'{"version": 1, "name": "a\\ud800"}',
        headers={**signed_headers(staff), "Content-Type": "application/json"},
    )

    for response in (created, renamed):
        assert code_of(response) == (422, "validation_error"), response.text
        assert errors_of(response) == [(("body", "name"), "text_invalid")]


def test_d2_a_lone_surrogate_in_an_item_never_reaches_the_key(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    body = item_body(add_pay_type(url), description="@@")
    raw = json.dumps(body).replace("@@", "a\\ud800b")

    response = raw_post(client, staff, items_path(tutor.id), raw, keyed=True)

    assert code_of(response) == (422, "validation_error"), response.text
    assert errors_of(response) == [(("body",), "text_invalid")]
    assert counted(url, "pay_items", tutor.id) == 0


def test_d2_access_still_answers_before_the_body(module_seeded_url, client, cookies):
    who = create_user(module_seeded_url, roles=(TUTOR,)).id

    response = raw_post(
        client, cookies["coordinator"], items_path(who), SURROGATE_BODY, keyed=True
    )

    assert code_of(response) == DENIED


def over_the_range(
    url: str, tutor: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """QA D1: two recurring bonuses of the limit and a deduction of it, run, then
    the deduction deleted by the caller. The period, a bonus and the deduction."""
    period = add_period_row(url, tutor, "11-02", "11-08")
    pay_type = add_pay_type(url)
    add_item(url, tutor, pay_type, MONEY_LIMIT, on="10-01", recurrence="recurring")
    bonus = add_item(
        url, tutor, pay_type, MONEY_LIMIT, on="10-01", recurrence="recurring"
    )
    deduction = add_item(
        url, tutor, add_pay_type(url, direction="deduction"), MONEY_LIMIT
    )
    assert str(run(url, period).total) == MONEY_LIMIT
    return period, bonus, deduction


def test_d1_lines_beyond_the_range_are_a_typed_refusal(
    module_seeded_url, client, staff, tutor
):
    url = module_seeded_url
    period, _, deduction = over_the_range(url, tutor.id)
    lines_path = f"{ROOT}/periods/{period}/lines"

    assert delete(client, staff, items_path(tutor.id, deduction)).status_code == 204

    assert code_of(get(client, staff, lines_path)) == OUT_OF_RANGE
    assert code_of(
        post(client, staff, f"{ROOT}/periods/{period}/recalculate", None)
    ) == (OUT_OF_RANGE)
    assert len(get(client, staff, items_path(tutor.id)).json()["items"]) == 2


@pytest.mark.parametrize("way_out", ["delete", "end"])
def test_d1_finance_gets_out_through_the_item_routes(
    module_seeded_url, client, staff, tutor, way_out
):
    url = module_seeded_url
    period, bonus, deduction = over_the_range(url, tutor.id)
    assert delete(client, staff, items_path(tutor.id, deduction)).status_code == 204
    path = items_path(tutor.id, bonus)

    if way_out == "delete":
        response = delete(client, staff, path)
        assert response.status_code == 204
    else:
        response = patch(
            client, staff, path, {"version": 1, "effective_to": "2026-11-01"}
        )
        assert code_of(response) == (200, None), response.text

    ran = post(client, staff, f"{ROOT}/periods/{period}/recalculate", None)
    assert ran.json() == {"line_count": 1, "total": MONEY_LIMIT}
    assert read_lines(client, staff, period)["total"] == MONEY_LIMIT


def item_on_a_retired_type(
    url: str, tutor: uuid.UUID, until: str = "2026-10-31"
) -> uuid.UUID:
    return add_item(
        url,
        tutor,
        add_pay_type(url, active=False),
        on="10-01",
        until=until,
        recurrence="recurring",
    )


@pytest.mark.parametrize("end", [None, "2026-11-01"])
def test_o3_a_retired_type_doesnt_pay_again(
    module_seeded_url, client, staff, tutor, end
):
    url = module_seeded_url
    item = item_on_a_retired_type(url, tutor.id)

    response = patch(
        client, staff, items_path(tutor.id, item), {"version": 1, "effective_to": end}
    )

    assert code_of(response) == (409, "pay_item_type_inactive")


@pytest.mark.parametrize("end", ["2026-10-31", "2026-10-15"])
def test_o3_a_retired_types_item_can_keep_or_shorten_its_end(
    module_seeded_url, client, staff, tutor, end
):
    url = module_seeded_url
    item = item_on_a_retired_type(url, tutor.id)

    response = patch(
        client, staff, items_path(tutor.id, item), {"version": 1, "effective_to": end}
    )

    assert code_of(response) == (200, None), response.text


def test_o3_an_open_item_on_a_retired_type_can_be_ended(
    module_seeded_url, client, staff, tutor
):
    """By SQL: a deactivation refuses this state, and ending stays safe in it."""
    url = module_seeded_url
    item = add_item(
        url,
        tutor.id,
        add_pay_type(url, active=False),
        on="10-01",
        recurrence="recurring",
    )

    response = patch(
        client,
        staff,
        items_path(tutor.id, item),
        {"version": 1, "effective_to": "2026-10-31"},
    )

    assert code_of(response) == (200, None), response.text


def test_o3_an_active_type_reopens_as_before(module_seeded_url, client, staff, tutor):
    url = module_seeded_url
    item = add_item(
        url,
        tutor.id,
        add_pay_type(url),
        on="10-01",
        until="10-31",
        recurrence="recurring",
    )

    response = patch(
        client,
        staff,
        items_path(tutor.id, item),
        {"version": 1, "effective_to": None},
    )

    assert code_of(response) == (200, None), response.text


def test_a_change_shows_only_on_the_next_run(module_seeded_url, client, staff, tutor):
    """Pull model: no hook. An end leaves the line until a run drops it."""
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    run(url, period)

    patch(
        client,
        staff,
        items_path(tutor.id, item),
        {"version": 1, "effective_to": "2026-10-31"},
    )

    assert len(lines(url, period)) == 1
    assert run(url, period).line_count == 0


# --- L5: who may call what -----


@pytest.mark.parametrize("name", ["owner", "finance", "finance_tutor"])
def test_l5_holders_read(client, users, cookies, name):
    other = users["plain_tutor"].id
    assert get(client, cookies[name], TYPES).status_code == 200
    assert get(client, cookies[name], items_path(other)).status_code == 200


@pytest.mark.parametrize("name", [*NO_ACCESS, "plain_tutor"])
def test_l5_everyone_else_is_refused_every_route(
    module_seeded_url, client, users, cookies, name
):
    url = module_seeded_url
    cookie = cookies[name]
    who = users["plain_tutor"].id
    pay_type = add_pay_type(url)
    item = add_item(url, who, pay_type)
    type_path = f"{TYPES}/{pay_type}"

    assert code_of(get(client, cookie, TYPES)) == DENIED
    assert code_of(get(client, cookie, items_path(who))) == DENIED
    assert code_of(
        post(client, cookie, TYPES, {"name": "N", "direction": "bonus"})
    ) == (DENIED)
    assert code_of(patch(client, cookie, type_path, {"version": 1})) == DENIED
    assert code_of(post_item(client, cookie, who, item_body(pay_type))) == DENIED
    assert (
        code_of(
            patch(
                client,
                cookie,
                items_path(who, item),
                {"version": 1, "effective_to": None},
            )
        )
        == DENIED
    )
    assert code_of(delete(client, cookie, items_path(who, item))) == DENIED


def test_l5_a_finance_tutor_reads_their_own_and_writes_nothing_of_it(
    module_seeded_url, client, users, cookies
):
    url = module_seeded_url
    cookie = cookies["finance_tutor"]
    who = users["finance_tutor"].id
    pay_type = add_pay_type(url)
    item = add_item(url, who, pay_type, on="10-01", recurrence="recurring")
    end = {"version": 1, "effective_to": None}

    assert get(client, cookie, items_path(who)).status_code == 200
    assert code_of(post_item(client, cookie, who, item_body(pay_type))) == OWN
    assert code_of(patch(client, cookie, items_path(who, item), end)) == OWN
    assert code_of(delete(client, cookie, items_path(who, item))) == OWN


def test_l5_an_owner_writes_their_own(module_seeded_url, client, users, cookies):
    url = module_seeded_url
    cookie = cookies["owner_tutor"]
    who = users["owner_tutor"].id
    pay_type = add_pay_type(url)

    made = post_item(client, cookie, who, item_body(pay_type))

    assert made.status_code == 201
    assert delete(client, cookie, items_path(who, made.json()["id"])).status_code == 204


def test_f4_a_bad_body_answers_before_the_own_pay_rule(
    module_seeded_url, client, users, cookies
):
    who = users["finance_tutor"].id
    pay_type = add_pay_type(module_seeded_url)

    response = post_item(
        client, cookies["finance_tutor"], who, item_body(pay_type, amount="0.00")
    )

    assert code_of(response) == (422, "validation_error")


def test_l5_anonymous_and_missing_csrf(module_seeded_url, client, users, staff):
    who = users["plain_tutor"].id
    client.cookies.clear()
    assert client.get(TYPES).status_code == 401
    assert client.get(items_path(who)).status_code == 401
    client.cookies.clear()
    response = client.post(TYPES, json={}, headers=cookie_header(staff))
    assert code_of(response) == (403, "csrf_failed")


# --- L6: audit and logs -----


def test_l6_the_audit_trail_masks_amount_and_description(
    module_seeded_url, client, cookies, tutor
):
    url = module_seeded_url
    staff = cookies["finance"]
    pay_type = add_pay_type(url)
    made = post_item(
        client,
        staff,
        tutor.id,
        item_body(
            pay_type,
            amount=ITEM_CANARY,
            description=TEXT_CANARY,
            recurrence="recurring",
        ),
    ).json()
    path = items_path(tutor.id, made["id"])
    assert (
        patch(
            client, staff, path, {"version": 1, "effective_to": "2026-12-31"}
        ).status_code
        == 200
    )
    assert delete(client, staff, path).status_code == 204

    for reader in ("owner", "administrator"):
        response = get(
            client,
            cookies[reader],
            "/api/v1/audit-events",
            record_type="pay_items",
            record_id=made["id"],
        )
        assert response.status_code == 200, response.text
        assert ITEM_CANARY not in response.text
        assert TEXT_CANARY not in response.text
        events = response.json()["items"]
        assert len(events) == 3
        for event in events:
            assert event["actor_id"] is not None
        changes = [event["changes"] for event in events]
        masked = [c for c in changes if "amount" in c]
        assert len(masked) == 2
        for change in masked:
            assert "[masked]" in str(change["amount"])
            assert "[masked]" in str(change["description"])
        assert any("effective_to" in c for c in changes)


def test_l6_a_type_is_audited_unmasked(client, cookies):
    made = post(
        client, cookies["finance"], TYPES, {"name": "Visible", "direction": "bonus"}
    ).json()

    response = get(
        client,
        cookies["administrator"],
        "/api/v1/audit-events",
        record_type="pay_item_types",
        record_id=made["id"],
    )

    assert "Visible" in response.text


def test_l6_no_amount_or_description_reaches_the_log(
    module_seeded_url, client, staff, tutor, caplog
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    pay_type = add_pay_type(url)

    with caplog.at_level(logging.DEBUG):
        made = post_item(
            client,
            staff,
            tutor.id,
            item_body(pay_type, amount=ITEM_CANARY, description=TEXT_CANARY),
        )
        assert made.status_code == 201
        post(client, staff, f"{ROOT}/periods/{period}/recalculate", None)
        delete(client, staff, items_path(tutor.id, made.json()["id"]))

    assert ITEM_CANARY not in caplog.text
    assert TEXT_CANARY not in caplog.text
