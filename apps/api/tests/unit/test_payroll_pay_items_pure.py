"""The pure placement of pay items (#109 B; ADR-0017 E8, #109 E3), and the rules
of the bodies. The syntax-tree test of ``pay_item_lines.py`` is in
``test_payroll_calculation_pure.py``.
"""

import uuid
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.money import Money
from app.modules.payroll.calculation import LineDraft
from app.modules.payroll.pay_item_lines import (
    PayItemFact,
    PeriodDates,
    pay_item_drafts,
    placed_period,
)
from app.modules.payroll.schemas import (
    PayItemCreate,
    PayItemTypeCreate,
    PayItemTypeUpdate,
    PayItemUpdate,
)

NOV = PeriodDates(uuid.uuid7(), date(2026, 11, 1), date(2026, 11, 30))
DEC = PeriodDates(uuid.uuid7(), date(2026, 12, 1), date(2026, 12, 31))
AFTER_GAP = PeriodDates(uuid.uuid7(), date(2027, 2, 1), date(2027, 2, 28))
MAX = "999999999999.99"


def item(
    on: date,
    until: date | None = None,
    *,
    recurrence: str = "one_time",
    direction: str = "bonus",
    pay: str = "100.00",
) -> PayItemFact:
    return PayItemFact(
        id=uuid.uuid7(),
        direction=direction,
        pay=Money(pay),
        recurrence=recurrence,
        effective_from=on,
        effective_to=until,
    )


def recurring(on: date, until: date | None = None, **changes: Any) -> PayItemFact:
    return item(on, until, recurrence="recurring", **changes)


def drafts(
    period: PeriodDates,
    *items: PayItemFact,
    periods: tuple[PeriodDates, ...] | None = None,
    paid: frozenset[uuid.UUID] = frozenset(),
) -> list[LineDraft]:
    return pay_item_drafts(
        period=period,
        items=items,
        unfinalized_periods=(period,) if periods is None else periods,
        finalized_item_ids=paid,
    )


# --- B4: recurring -----


def test_recurring_from_the_periods_last_day_is_a_full_line():
    assert [d.amount for d in drafts(NOV, recurring(NOV.ends_on))] == [Money("100.00")]


def test_recurring_ending_on_the_periods_first_day_is_a_full_line():
    made = recurring(date(2026, 1, 1), NOV.starts_on)
    assert [d.amount for d in drafts(NOV, made)] == [Money("100.00")]


def test_recurring_ended_the_day_before_has_no_line():
    assert drafts(NOV, recurring(date(2026, 1, 1), date(2026, 10, 31))) == []


def test_recurring_starting_the_day_after_has_no_line():
    assert drafts(NOV, recurring(date(2026, 12, 1))) == []


def test_recurring_with_an_open_end_is_a_full_line_every_week():
    weeks = [
        PeriodDates(uuid.uuid7(), date(2026, 11, day), date(2026, 11, day + 6))
        for day in (2, 9, 16, 23)
    ]
    made = recurring(date(2026, 11, 1))
    assert [[d.amount for d in drafts(week, made)] for week in weeks] == (
        [[Money("100.00")]] * 4
    )


def test_recurring_in_a_one_day_stub_is_a_full_line():
    stub = PeriodDates(uuid.uuid7(), date(2026, 11, 2), date(2026, 11, 2))
    assert [d.amount for d in drafts(stub, recurring(date(2026, 1, 1)))] == [
        Money("100.00")
    ]


def test_a_finalized_line_doesnt_stop_a_recurring_item():
    made = recurring(date(2026, 1, 1))
    assert len(drafts(NOV, made, paid=frozenset({made.id}))) == 1


def test_a_recurring_item_has_no_single_period():
    assert placed_period(recurring(date(2026, 11, 3)), [NOV, DEC], set()) is None


# --- B4: one-time. Finalized periods aren't in the list, so a date inside one
# reads like any date before the first not-finalized period. -----


@pytest.mark.parametrize(
    ("on", "periods", "expected"),
    [
        pytest.param(date(2026, 11, 3), (NOV, DEC), NOV, id="inside-an-open-period"),
        pytest.param(date(2026, 11, 30), (NOV, DEC), NOV, id="its-last-day"),
        pytest.param(date(2026, 12, 1), (NOV, DEC), DEC, id="the-day-after"),
        pytest.param(date(2026, 11, 3), (DEC,), DEC, id="finalized-next-open"),
        pytest.param(date(2026, 11, 3), (DEC,), DEC, id="finalized-next-prepared"),
        pytest.param(date(2026, 11, 3), (NOV,), NOV, id="inside-a-prepared-period"),
        pytest.param(date(2020, 1, 1), (NOV, DEC), NOV, id="before-the-first-period"),
        pytest.param(date(2027, 1, 15), (NOV, AFTER_GAP), AFTER_GAP, id="in-a-gap"),
        pytest.param(date(2027, 1, 15), (NOV, DEC), None, id="after-the-last-period"),
        pytest.param(date(2026, 11, 3), (), None, id="no-periods"),
    ],
)
def test_e3_placement(on, periods, expected):
    assert placed_period(item(on), periods, set()) == expected


def test_e3_the_list_order_doesnt_matter():
    assert placed_period(item(date(2026, 11, 3)), [DEC, NOV], set()) == NOV


def test_e3_an_item_with_a_finalized_line_is_done_everywhere():
    made = item(date(2026, 11, 3))
    paid = frozenset({made.id})
    assert placed_period(made, [NOV, DEC], paid) is None
    assert drafts(NOV, made, periods=(NOV, DEC), paid=paid) == []
    assert drafts(DEC, made, periods=(NOV, DEC), paid=paid) == []


def test_a_one_time_item_lands_in_one_period_of_two():
    made = item(date(2026, 11, 3))
    assert len(drafts(NOV, made, periods=(NOV, DEC))) == 1
    assert drafts(DEC, made, periods=(NOV, DEC)) == []


# --- B3: sign, order, range -----


def test_a_draft_copies_the_amount_with_the_directions_sign():
    bonus = item(date(2026, 11, 3), pay="10.01")
    deduction = item(date(2026, 11, 3), direction="deduction", pay="10.01")

    assert drafts(NOV, bonus, deduction) == [
        LineDraft(kind="pay_item", amount=Money("10.01"), pay_item_id=bonus.id),
        LineDraft(kind="pay_item", amount=Money("-10.01"), pay_item_id=deduction.id),
    ]


def test_drafts_come_by_item_id_whatever_the_input_order():
    older, newer = item(date(2026, 11, 3)), item(date(2026, 11, 3))
    assert [d.pay_item_id for d in drafts(NOV, newer, older)] == [older.id, newer.id]


def test_the_largest_deduction_fits():
    [draft] = drafts(NOV, item(date(2026, 11, 3), direction="deduction", pay=MAX))
    assert draft.amount == Money(f"-{MAX}")


# --- F3, D3, F5: the bodies -----

TYPE_ID = str(uuid.uuid7())


def create(**changes: Any) -> PayItemCreate:
    return PayItemCreate.model_validate(
        {
            "pay_item_type_id": TYPE_ID,
            "description": " Bono de asistencia ",
            "amount": "100.00",
            "recurrence": "recurring",
            "effective_from": "2026-11-01",
            **changes,
        }
    )


def errors_of(call: Any) -> set[str]:
    with pytest.raises(ValidationError) as caught:
        call()
    return {error["type"] for error in caught.value.errors()}


def test_a_valid_body_is_trimmed():
    assert create().description == "Bono de asistencia"


def test_an_end_on_the_start_day_is_valid():
    assert create(effective_to="2026-11-01").effective_to == date(2026, 11, 1)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"amount": "0.00"}, "amount_not_positive"),
        ({"amount": "-1.00"}, "amount_not_positive"),
        ({"amount": "1.001"}, "money_precision"),
        ({"description": "   "}, "text_empty"),
        ({"description": "x" * 501}, "text_too_long"),
        ({"description": "a\x00b"}, "text_invalid"),
        ({"description": "a\ud800b"}, "text_invalid"),
        ({"effective_to": "2026-10-31"}, "effective_to_before_from"),
        (
            {"recurrence": "one_time", "effective_to": "2026-12-01"},
            "effective_to_not_allowed",
        ),
        ({"recurrence": "monthly"}, "literal_error"),
        ({"surprise": 1}, "extra_forbidden"),
    ],
)
def test_f3_a_bad_item_body(changes, code):
    assert code in errors_of(lambda: create(**changes))


def test_a_description_of_500_characters_is_valid():
    assert len(create(description="x" * 500).description) == 500


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"name": "  ", "direction": "bonus"}, "text_empty"),
        ({"name": "x" * 101, "direction": "bonus"}, "text_too_long"),
        ({"name": "a\x00b", "direction": "bonus"}, "text_invalid"),
        ({"name": "a\ud800b", "direction": "bonus"}, "text_invalid"),
        ({"name": "Bono", "direction": "gift"}, "literal_error"),
    ],
)
def test_a_bad_type_body(body, code):
    assert code in errors_of(lambda: PayItemTypeCreate.model_validate(body))


def test_a_type_rename_refuses_a_lone_surrogate():
    body = {"version": 1, "name": "a\ud800"}
    assert errors_of(lambda: PayItemTypeUpdate.model_validate(body)) == {"text_invalid"}


def test_d3_the_direction_isnt_a_field():
    body = {"version": 1, "direction": "deduction"}
    assert errors_of(lambda: PayItemTypeUpdate.model_validate(body)) == {
        "extra_forbidden"
    }


@pytest.mark.parametrize("field", ["amount", "description", "effective_from"])
def test_f5_only_the_end_moves(field):
    body = {"version": 1, "effective_to": None, field: "x"}
    assert errors_of(lambda: PayItemUpdate.model_validate(body)) == {"extra_forbidden"}


def test_f5_the_end_is_written_out():
    assert errors_of(lambda: PayItemUpdate.model_validate({"version": 1})) == {
        "missing"
    }
