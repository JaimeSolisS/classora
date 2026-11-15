"""The simulation's administration and reporting plans (#554 HF-01f: A1 and
the plan's rules), pure."""

from datetime import UTC, date, datetime

import pytest

from tests.factories.simulation.administration import (
    READ_BEFORE,
    SERIES_FROM,
    SNAPSHOT_DAY,
    TRIAL_UNTIL,
    build_administration,
    calendar_of,
    local_day,
)
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_SETUP,
    sim_clock,
)
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.reporting import DELETED_ON, build_reporting
from tests.factories.simulation.timetable import mins

PLANS = {
    (language, profile): plan_simulation(language, profile, 1)
    for language in ("es", "en")
    for profile in ("small", "full")
}
KEYS = sorted(PLANS)


@pytest.mark.parametrize("key", KEYS)
def test_the_plans_are_pure(key):
    plan = PLANS[key]

    assert build_administration(plan, 1) == plan.administration
    assert build_reporting(plan) == plan.reporting


def test_the_run_date_does_not_change_the_plans():
    plans = []
    for now in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        with sim_clock(now):
            plans.append(plan_simulation("en", "small", 1))

    assert plans[0].administration == plans[1].administration
    assert plans[0].reporting == plans[1].reporting


@pytest.mark.parametrize("key", KEYS)
def test_the_trial_and_the_imported_series_break_no_scheduling_rule(key):
    plan = PLANS[key]
    admin = plan.administration
    cal = calendar_of(plan)
    trial = admin.trial
    assert len(trial.dates) == 2
    assert all(SIM_ANCHOR > d <= TRIAL_UNTIL for d in trial.dates)
    for where in trial.placements():
        lo, hi = where.lo, where.hi
        assert cal.center_error(where.day, lo, hi) is None
        assert cal.tutor_error(trial.tutor, trial.subject, where.day, lo, hi) is None
    series = admin.series
    assert series.dates[0] >= SERIES_FROM and series.dates[-1] <= SIM_HORIZON
    for day in series.dates:
        lo = mins(series.start)
        hi = lo + series.minutes
        assert cal.center_error(day, lo, hi) is None
        assert cal.tutor_error(series.tutor, series.subject, day, lo, hi) is None
        assert not any(
            olo < hi and lo < ohi for olo, ohi, _ in cal.student[(series.student, day)]
        )
    # The conflicting row is one of the tutor's own classes after the anchor.
    assert series.conflict.tutor == series.tutor
    assert series.conflict.day > SIM_ANCHOR
    assert plan.billing.mode_on(series.student, SERIES_FROM) == "per_class"


@pytest.mark.parametrize("key", KEYS)
def test_the_families_pay_what_the_imports_and_the_trial_add(key):
    plan = PLANS[key]
    admin = plan.administration
    late = {*plan.billing.overdue_families, *plan.billing.ahead_families}
    assert admin.family not in late
    assert len(admin.balances) == 2
    for balance in admin.balances:
        assert balance.family not in late
        assert any(
            p.family == balance.family
            and local_day(p.at) == balance.due_date
            and p.cutoff >= balance.due_date
            for p in plan.billing.payments
        )


@pytest.mark.parametrize("key", KEYS)
def test_the_imported_children_and_the_opt_outs(key):
    plan = PLANS[key]
    admin = plan.administration
    family = next(f for f in plan.families if f.key == admin.family)
    assert [s.key for s in admin.students] == ["imp1", "imp2"]
    for child in admin.students:
        assert child.family_names == family.students[0].family_names
        assert child.birthdate not in {s.birthdate for s in plan.students}
    names = {f"{s.given_names} {s.family_names}" for s in plan.students}
    assert not names & {f"{s.given_names} {s.family_names}" for s in admin.students}
    assert admin.duplicate in {s.key for s in family.students}
    accounts = {a.username: a for a in plan.accounts}
    for opt_out in admin.opt_outs:
        assert accounts[opt_out.username].created_at <= SIM_SETUP
    assert len({o.notification_type for o in admin.opt_outs}) == 2
    assert READ_BEFORE == date(2026, 11, 12) and SNAPSHOT_DAY < READ_BEFORE


@pytest.mark.parametrize("key", KEYS)
def test_the_reporting_plan(key):
    plan = PLANS[key]
    reporting = plan.reporting
    assert len(reporting.setups) >= 3
    assert len(reporting.exports) == 2
    assert {layout.user for layout in reporting.layouts} == {"owner", "finance"}
    assert SNAPSHOT_DAY not in reporting.days()
    deleted = reporting.deleted
    held = plan.timetable.cls(deleted.cls)
    assert held.status == "completed" and held.final.tutor == deleted.tutor
    assert held.final.day < DELETED_ON
    assert deleted.student in held.students
