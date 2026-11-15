"""The pure package rules (#94 D4, L4): the first-expiring-first plan and
the status."""

import uuid
from datetime import date

import pytest

from app.modules.billing.package_rules import (
    EligiblePackage,
    package_status,
    plan_consumption,
)

D = date(2031, 3, 1)


def pkg(
    unit: str,
    remaining: int,
    expires: date,
    *,
    purchased: date = D,
    id_: uuid.UUID | None = None,
) -> EligiblePackage:
    return EligiblePackage(
        id=id_ or uuid.uuid7(),
        unit=unit,  # type: ignore[arg-type]
        remaining=remaining,
        expires_on=expires,
        purchased_on=purchased,
    )


def test_a_sessions_package_gives_one_unit_whatever_the_length():
    only = pkg("sessions", 3, date(2031, 6, 1))
    assert plan_consumption([only], 120) == ((only.id, 1),)
    assert plan_consumption([only], 15) == ((only.id, 1),)


def test_the_earlier_expiring_sessions_package_is_used_first():
    late = pkg("sessions", 5, date(2031, 9, 1))
    early = pkg("sessions", 5, date(2031, 6, 1))
    assert plan_consumption([late, early], 60) == ((early.id, 1),)


def test_ties_on_expiry_go_to_the_earlier_purchase_then_the_id():
    first_id, second_id = sorted([uuid.uuid4(), uuid.uuid4()])
    older = pkg("sessions", 1, date(2031, 6, 1), purchased=date(2031, 1, 1))
    newer = pkg("sessions", 1, date(2031, 6, 1), purchased=date(2031, 2, 1))
    assert plan_consumption([newer, older], 60) == ((older.id, 1),)
    a = pkg("sessions", 1, date(2031, 6, 1), id_=second_id)
    b = pkg("sessions", 1, date(2031, 6, 1), id_=first_id)
    assert plan_consumption([a, b], 60) == ((first_id, 1),)


def test_minutes_are_taken_across_packages_first_expiring_first():
    early = pkg("minutes", 30, date(2031, 6, 1))
    late = pkg("minutes", 300, date(2031, 9, 1))
    assert plan_consumption([late, early], 60) == ((early.id, 30), (late.id, 30))


def test_minutes_from_one_package_when_it_is_enough():
    early = pkg("minutes", 90, date(2031, 6, 1))
    late = pkg("minutes", 300, date(2031, 9, 1))
    assert plan_consumption([late, early], 60) == ((early.id, 60),)


def test_too_few_minutes_is_short_all_or_nothing():
    assert plan_consumption([pkg("minutes", 60, date(2031, 6, 1))], 90) is None
    assert (
        plan_consumption(
            [pkg("minutes", 30, date(2031, 6, 1)), pkg("minutes", 45, D)], 90
        )
        is None
    )


def test_no_package_is_short():
    assert plan_consumption([], 60) is None


def test_mixed_units_choose_the_path_whose_first_package_expires_first():
    sessions = pkg("sessions", 2, date(2031, 9, 1))
    minutes = pkg("minutes", 120, date(2031, 6, 1))
    assert plan_consumption([sessions, minutes], 60) == ((minutes.id, 60),)
    earlier_sessions = pkg("sessions", 2, date(2031, 5, 1))
    assert plan_consumption([earlier_sessions, minutes], 60) == (
        (earlier_sessions.id, 1),
    )


def test_a_tie_between_the_paths_goes_to_sessions():
    sessions = pkg("sessions", 1, date(2031, 6, 1))
    minutes = pkg("minutes", 120, date(2031, 6, 1))
    assert plan_consumption([minutes, sessions], 60) == ((sessions.id, 1),)


def test_mixed_units_never_split_one_session():
    # 30 minutes left and one session: the sessions path covers it whole.
    sessions = pkg("sessions", 1, date(2031, 9, 1))
    minutes = pkg("minutes", 30, date(2031, 6, 1))
    assert plan_consumption([sessions, minutes], 60) == ((sessions.id, 1),)


def test_a_plan_never_takes_more_than_the_remaining_units():
    packages = [pkg("minutes", r, date(2031, 6, d)) for d, r in ((1, 15), (2, 45))]
    plan = plan_consumption(packages, 60)
    assert plan is not None
    remaining = {p.id: p.remaining for p in packages}
    assert all(0 < units <= remaining[pid] for pid, units in plan)
    assert sum(units for _, units in plan) == 60


def test_scheduled_minutes_must_be_positive():
    with pytest.raises(ValueError):
        plan_consumption([], 0)


@pytest.mark.parametrize(
    ("remaining", "expires", "expire_row", "expected"),
    [
        (5, date(2031, 3, 10), False, "active"),
        (0, date(2031, 3, 10), False, "exhausted"),
        (5, date(2031, 3, 9), False, "expired"),
        (0, date(2031, 3, 9), False, "expired"),
        (5, date(2031, 3, 20), True, "expired"),
        (5, date(2031, 3, 10), False, "active"),
    ],
)
def test_the_status_lets_expired_win(remaining, expires, expire_row, expected):
    assert (
        package_status(
            remaining, expires, has_expire_row=expire_row, today=date(2031, 3, 10)
        )
        == expected
    )
