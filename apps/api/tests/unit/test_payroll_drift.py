"""The drift comparison and the period policies (#110 D3, J9, E8, E11; #108 QA
O3). Pure: no database."""

import uuid
from datetime import date

import pytest

from app.core.authz import Principal
from app.core.money import Money, Rate
from app.modules.payroll.calculation import LineDraft, UnresolvedItem, line_drift
from app.modules.payroll.errors import (
    UNRESOLVED_ITEMS_SHOWN,
    PayrollUnresolvedWorkError,
)
from app.modules.payroll.permissions import (
    can_list_periods,
    can_read_period,
    can_return_period,
    finalize_refusal,
)

SESSION = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)
ITEM = uuid.UUID(int=3)


def session_line(session: uuid.UUID = SESSION, amount: str = "120.00") -> LineDraft:
    return LineDraft(
        kind="session",
        amount=Money(amount),
        class_session_id=session,
        approval_id=uuid.UUID(int=9),
        service_date=date(2026, 11, 3),
        class_kind="individual",
        minutes=60,
        rate_model="per_hour",
        rate=Rate("120"),
    )


def salary_line(starts: date, amount: str = "700.00") -> LineDraft:
    return LineDraft(kind="salary", amount=Money(amount), salary_starts_on=starts)


def item_line(item: uuid.UUID = ITEM, amount: str = "40.00") -> LineDraft:
    return LineDraft(kind="pay_item", amount=Money(amount), pay_item_id=item)


LINES = [
    session_line(),
    session_line(OTHER),
    salary_line(date(2026, 11, 2)),
    item_line(),
]


def test_equal_lines_are_clean_in_any_order():
    drift = line_drift(LINES, list(reversed(LINES)))

    assert drift.is_clean
    assert (drift.added, drift.removed, drift.changed) == (0, 0, 0)


def test_no_lines_are_clean():
    assert line_drift([], []).is_clean


def test_a_new_line_is_added():
    drift = line_drift(LINES[:3], LINES)

    assert (drift.added, drift.removed, drift.changed) == (1, 0, 0)
    assert not drift.is_clean


def test_a_gone_line_is_removed():
    drift = line_drift(LINES, LINES[1:])

    assert (drift.added, drift.removed, drift.changed) == (0, 1, 0)


def test_the_same_source_with_another_figure_is_changed():
    drift = line_drift(LINES, [session_line(amount="60.00"), *LINES[1:]])

    assert (drift.added, drift.removed, drift.changed) == (0, 0, 1)


def test_another_kind_on_the_same_session_is_changed():
    no_show = LineDraft(
        kind="no_show", amount=Money("120.00"), class_session_id=SESSION
    )

    drift = line_drift([session_line()], [no_show])

    assert (drift.added, drift.removed, drift.changed) == (0, 0, 1)


def test_a_multiset_a_repeated_line_isnt_one_line():
    """A set would call these equal, and hide a line paid twice."""
    drift = line_drift([item_line(), item_line()], [item_line()])

    assert (drift.added, drift.removed, drift.changed) == (0, 1, 0)


def test_every_kind_of_difference_at_once():
    drift = line_drift(
        [session_line(), item_line()],
        [session_line(amount="60.00"), salary_line(date(2026, 11, 2))],
    )

    assert (drift.added, drift.removed, drift.changed) == (1, 1, 1)


def principal(*permissions: str, roles: tuple[str, ...] = ()) -> Principal:
    return Principal(
        user_id=uuid.UUID(int=7),
        roles=frozenset(roles),
        permissions=frozenset(permissions),
    )


def test_a_view_holder_reads_every_status():
    who = principal("payroll.view")
    assert can_list_periods(who)
    assert all(can_read_period(who, s) for s in ("open", "prepared", "finalized"))


def test_a_delegate_reads_only_what_there_is_to_review():
    who = principal("payroll.finalize")
    assert can_list_periods(who)
    assert not can_read_period(who, "open")
    assert can_read_period(who, "prepared")
    assert can_read_period(who, "finalized")


def test_nobody_else_reads():
    assert not can_list_periods(principal("payroll.prepare"))
    assert not can_read_period(principal(), "finalized")


def test_either_permission_returns():
    assert can_return_period(principal("payroll.prepare"))
    assert can_return_period(principal("payroll.finalize"))
    assert not can_return_period(principal("payroll.view"))


@pytest.mark.parametrize(
    ("tutor", "prepared_by", "expected"),
    [
        (uuid.UUID(int=8), uuid.UUID(int=7), "preparer"),
        (uuid.UUID(int=7), uuid.UUID(int=8), "own_pay"),
        (uuid.UUID(int=7), uuid.UUID(int=7), "preparer"),
        (uuid.UUID(int=8), uuid.UUID(int=9), None),
    ],
)
def test_the_second_person(tutor, prepared_by, expected):
    assert (
        finalize_refusal(
            principal("payroll.finalize"),
            tutor_user_id=tutor,
            prepared_by_user_id=prepared_by,
        )
        == expected
    )


def test_an_owner_is_excepted_from_both():
    who = principal("payroll.finalize", roles=("owner",))
    assert (
        finalize_refusal(
            who, tutor_user_id=who.user_id, prepared_by_user_id=who.user_id
        )
        is None
    )


def test_the_unresolved_error_lists_twenty_and_counts_all():
    items = [
        UnresolvedItem(
            class_session_id=uuid.UUID(int=i),
            tutor_user_id=OTHER,
            class_kind="individual",
            service_date=date(2026, 11, 3),
            source="closeout",
            reason="no_rate",
        )
        for i in range(UNRESOLVED_ITEMS_SHOWN + 3)
    ]

    details = PayrollUnresolvedWorkError(items).details

    assert details is not None
    assert details["count"] == UNRESOLVED_ITEMS_SHOWN + 3
    assert len(details["items"]) == UNRESOLVED_ITEMS_SHOWN
    assert set(details["items"][0]) == {"class_session_id", "service_date", "reason"}
