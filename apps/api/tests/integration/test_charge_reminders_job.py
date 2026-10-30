"""The daily charge reminder job (#97 F2-F7; I5; N1-N4 (a)).

Every test starts from :func:`due_date_support.reset` (billing on, every
earlier charge of the module database already reminded), so a run's counts
are the test's own. Runs are at 09:00 center-local time unless a window
edge needs another hour.
"""

import asyncio
import logging
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.workflows import charge_reminders
from app.workflows.charge_reminders import ChargeReminderRunResult
from tests.integration import due_date_support as dd
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

TODAY = dd.TODAY


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    dd.reset(module_seeded_url)


# --- Windows (N1) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "stage"),
    [
        (-4, None),
        (-3, "due_soon"),
        (0, "due_soon"),
        (1, "overdue"),
        (13, "overdue"),
        (14, "overdue_followup"),
        (400, "overdue_followup"),
    ],
)
def test_each_stage_at_its_window_edges(module_seeded_url, offset, stage):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)

    result = dd.run(url, TODAY + dd.days(offset))

    expected = [] if stage is None else [(charge, stage, TODAY)]
    assert dd.reminders(url, charge) == expected
    assert result.sent_charges == (0 if stage is None else 1)
    sent = dd.notifications(url, w.guardians)
    assert [params["kind"] for _, params in sent] == ([] if stage is None else [stage])


def test_the_window_follows_the_center_date_not_utc(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)

    # 23:30 local on the due date is 05:30 UTC the next day: still due_soon.
    dd.run(url, TODAY, 23, 30)
    # 00:05 local the next day: overdue.
    dd.run(url, TODAY + dd.days(1), 0, 5)

    assert [r[1] for r in dd.reminders(url, charge)] == ["due_soon", "overdue"]


def test_a_missed_window_is_never_caught_up(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)

    dd.run(url, TODAY + dd.days(20))
    dd.run(url, TODAY + dd.days(21))

    assert [r[1] for r in dd.reminders(url, charge)] == ["overdue_followup"]


def test_two_runs_on_one_day_send_once(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url, guardians=2)
    charge = dd.charge(url, w.student, due=TODAY)

    first = dd.run(url, TODAY, 9)
    second = dd.run(url, TODAY, 15)

    assert (first.sent_charges, first.notifications) == (1, 2)
    assert second == ChargeReminderRunResult()
    assert len(dd.reminders(url, charge)) == 1
    assert sorted(user for user, _ in dd.notifications(url, w.guardians)) == sorted(
        w.guardians
    )


# --- Grouping (N2) -------------------------------------------------------------------


def test_charges_in_one_stage_make_one_notification(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url, students=2)
    later = dd.charge(url, w.students[0], "150.00", due=TODAY + dd.days(2))
    earlier = dd.charge(url, w.students[1], "100.50", due=TODAY + dd.days(1))
    dd.pay(url, w, earlier, "50.00")

    result = dd.run(url)

    assert (result.sent_charges, result.notifications) == (2, 1)
    [(user, params)] = dd.notifications(url, w.guardians)
    assert user == w.guardians[0]
    assert params == {
        "family_id": str(w.family),
        "kind": "due_soon",
        "count": 2,
        "open_total": "200.50",  # 150.00 + the open 50.50
        "due_on": (TODAY + dd.days(1)).isoformat(),
    }
    assert {r[0] for r in dd.reminders(url, later, earlier)} == {later, earlier}


def test_two_stages_in_one_run_make_two_notifications(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    dd.charge(url, w.student, due=TODAY)
    dd.charge(url, w.student, due=TODAY - dd.days(5))

    dd.run(url)

    kinds = [params["kind"] for _, params in dd.notifications(url, w.guardians)]
    assert kinds == ["due_soon", "overdue"]


# --- Recipients (N3) -----------------------------------------------------------------


def test_every_active_guardian_with_a_login_is_notified(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url, guardians=2)
    inactive = dd.world(url, guardians=1, active=False)
    no_login = dd.world(url, guardians=1, login=False)
    dd.charge(url, w.student)
    inactive_charge = dd.charge(url, inactive.student)
    no_login_charge = dd.charge(url, no_login.student)
    inactive_users = [
        row.user_id
        for row in run_sql(
            url,
            "SELECT g.user_id FROM guardians g JOIN family_guardians f"
            " ON f.guardian_id = g.id WHERE f.family_id = :f",
            {"f": inactive.family},
        )
    ]

    result = dd.run(url)

    assert sorted(user for user, _ in dd.notifications(url, w.guardians)) == sorted(
        w.guardians
    )
    assert dd.notifications(url, inactive_users) == []
    assert result.no_recipient == 2
    assert dd.reminders(url, inactive_charge, no_login_charge) == []


def test_no_recipient_writes_nothing_and_a_new_guardian_gets_it(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url, guardians=0)
    charge = dd.charge(url, w.student, due=TODAY + dd.days(2))

    assert dd.run(url).no_recipient == 1
    assert dd.reminders(url, charge) == []

    user = dd.add_guardian(url, w.family)
    result = dd.run(url, TODAY + dd.days(1))

    assert result.sent_charges == 1
    assert [u for u, _ in dd.notifications(url, [user])] == [user]


def test_a_moved_student_reminds_the_accounts_family(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)
    run_sql(url, "DELETE FROM family_students WHERE student_id = :s", {"s": w.student})
    new = dd.world(url, students=0)
    run_sql(
        url,
        "INSERT INTO family_students (family_id, student_id) VALUES (:f, :s)",
        {"f": new.family, "s": w.student},
    )

    dd.run(url)

    assert len(dd.notifications(url, w.guardians)) == 1
    assert dd.notifications(url, new.guardians) == []
    assert len(dd.reminders(url, charge)) == 1


def test_an_inactive_family_is_still_reminded(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url, family_active=False)
    dd.charge(url, w.student)

    dd.run(url)

    assert len(dd.notifications(url, w.guardians)) == 1


# --- Quiet when settled (F5) ---------------------------------------------------------


def test_paid_void_and_zero_charges_are_quiet(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    paid = dd.charge(url, w.student)
    dd.pay(url, w, paid, "100.00")
    voided = dd.charge(url, w.student)
    dd.credit(url, voided, "-100.00")
    zero = dd.charge(url, w.student, "0.00")

    result = dd.run(url)

    assert dd.reminders(url, paid, voided, zero) == []
    assert dd.notifications(url, w.guardians) == []
    assert result.sent_charges == 0


def test_a_charge_paid_after_a_reminder_gets_no_further_stage(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)

    dd.run(url)
    dd.pay(url, w, charge, "100.00")
    dd.run(url, TODAY + dd.days(5))

    assert [r[1] for r in dd.reminders(url, charge)] == ["due_soon"]


def test_an_extension_never_resends_a_stage(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)
    dd.run(url, TODAY + dd.days(2))  # overdue
    dd.change_due(url, w, charge, TODAY + dd.days(10), today=TODAY + dd.days(2))

    dd.run(url, TODAY + dd.days(11))  # overdue again by the new date
    dd.run(url, TODAY + dd.days(24))  # 14 days after the new date

    assert dd.reminders(url, charge) == [
        (charge, "overdue", TODAY),
        (charge, "overdue_followup", TODAY + dd.days(10)),
    ]


def test_a_date_moved_into_the_past_is_reminded_on_the_next_run(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY + dd.days(30))
    assert dd.run(url).sent_charges == 0

    dd.change_due(url, w, charge, TODAY - dd.days(2))
    dd.run(url)

    assert dd.reminders(url, charge) == [(charge, "overdue", TODAY - dd.days(2))]


def test_billing_off_sends_nothing(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)
    dd.set_floor(url, None)

    result = dd.run(url)

    assert result == ChargeReminderRunResult(billing_off=1)
    assert dd.reminders(url, charge) == []


# --- Failures (F4) -------------------------------------------------------------------


def test_a_failing_account_does_not_block_the_others(
    module_seeded_url, monkeypatch, caplog
):
    url = module_seeded_url
    broken = dd.world(url)
    fine = dd.world(url)
    broken_charge = dd.charge(url, broken.student)
    fine_charge = dd.charge(url, fine.student)
    real = billing.record_reminders

    async def failing(session: AsyncSession, charges, *, sent_at):  # type: ignore[no-untyped-def]
        if any(c.charge_id == broken_charge for c in charges):
            await real(session, charges, sent_at=sent_at)
            raise RuntimeError("boom")
        return await real(session, charges, sent_at=sent_at)

    monkeypatch.setattr(billing, "record_reminders", failing)
    with caplog.at_level(logging.ERROR, logger="app.workflows.charge_reminders"):
        result = dd.run(url)

    assert (result.failed, result.sent_charges) == (1, 1)
    # Rolled back: no row and no notification for the failing account.
    assert dd.reminders(url, broken_charge) == []
    assert dd.notifications(url, broken.guardians) == []
    assert len(dd.reminders(url, fine_charge)) == 1
    [record] = [r for r in caplog.records if r.message.startswith("Sending charge")]
    assert record.billing_account_id == str(broken.account)  # type: ignore[attr-defined]
    assert record.error == "RuntimeError"  # type: ignore[attr-defined]

    monkeypatch.setattr(billing, "record_reminders", real)
    assert dd.run(url).sent_charges == 1  # retried by the next run


def test_notifications_exist_only_if_the_transaction_commits(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)

    class Abort(Exception):
        pass

    async def _remind_then_abort() -> None:
        from app.core.db import create_sessionmaker
        from tests.integration.conftest import short_lived_engine

        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                done = await charge_reminders.remind_account(
                    uow.session, w.account, today=TODAY
                )
                assert done.notifications == 1
                raise Abort()
        finally:
            await engine.dispose()

    with pytest.raises(Abort):
        asyncio.run(_remind_then_abort())

    assert dd.reminders(url, charge) == []
    assert dd.notifications(url, w.guardians) == []


def test_the_run_logs_counts_only(module_seeded_url, caplog):
    url = module_seeded_url
    w = dd.world(url)
    dd.charge(url, w.student)

    with caplog.at_level(logging.INFO, logger="app.workflows.charge_reminders"):
        caplog.clear()
        dd.run(url)

    [line] = [r for r in caplog.records if r.message == "Sent charge reminders"]
    assert line.sent_charges_count == 1  # type: ignore[attr-defined]
    assert line.notifications_count == 1  # type: ignore[attr-defined]
    assert str(w.family) not in line.getMessage()


def test_selection_pages_through_every_account(module_seeded_url, monkeypatch):
    url = module_seeded_url
    worlds = [dd.world(url) for _ in range(3)]
    charges = [dd.charge(url, w.student) for w in worlds]
    seen: list[uuid.UUID | None] = []
    real = billing.reminder_candidate_accounts

    async def small_pages(session: AsyncSession, *, today: date, after=None):  # type: ignore[no-untyped-def]
        seen.append(after)
        return await real(session, today=today, after=after, limit=2)

    monkeypatch.setattr(billing, "reminder_candidate_accounts", small_pages)
    monkeypatch.setattr(billing, "ACCOUNT_PAGE", 2)

    assert dd.run(url).sent_charges == 3
    assert len(seen) == 2
    assert all(len(dd.reminders(url, c)) == 1 for c in charges)


def test_account_ids_come_back_in_order(module_seeded_url):
    url = module_seeded_url
    worlds = [dd.world(url) for _ in range(2)]
    for w in worlds:
        dd.charge(url, w.student)

    found = in_uow(url, lambda s: billing.reminder_candidate_accounts(s, today=TODAY))

    assert found == sorted(w.account for w in worlds)
