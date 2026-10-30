"""Cancellations of charged and uncharged sessions (#99 D4, E3, E4, E8, E9,
E11, E12): one test per policy class, the edge cases, replay, immutability,
audit and the statement bound. See ``schedule_reconciliation_support``."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.money import Money
from app.workflows import schedule_reconciliation as reconciliation
from tests.integration import schedule_reconciliation_support as sr
from tests.integration import session_charge_support as scs
from tests.integration.billing_ledger_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import events, in_uow


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    sr.reset(module_seeded_url)


def _request(url: str, c: sr.Charged, **kwargs: object) -> uuid.UUID:
    request = sr.approved_request(url, c.session, student=c.student, **kwargs)  # type: ignore[arg-type]
    sr.cancel(url, c.session)
    return request


# --- E3: one test per policy class ---------------------------------------------------


def test_a_center_cancel_credits_the_net_and_releases_the_allocation(
    module_seeded_url,
):
    url = module_seeded_url
    c = sr.charged(url)
    allocation = sr.pay(url, c.charge, "250.00")
    sr.cancel(url, c.session)

    result = sr.run(url)

    assert result.candidates == 1
    assert result.cancellation_credited == 1
    [credit] = sr.adjustments(url, c.charge)
    assert (credit.kind, Money(credit.amount)) == (
        "cancellation_credit",
        Money("-250.00"),
    )
    assert credit.source_key == f"cancellation_credit:{c.charge}"
    assert credit.posted_by_user_id is None
    assert sr.net(url, c.charge) == Money.zero()
    assert sr.released(url, allocation)
    row = sr.decision(url, c.session, c.student)
    assert (row.initiated_by, row.classification, row.makeup_allowed) == (
        "center",
        "excused",
        True,
    )
    assert (row.outcome, row.result) == ("credit", "credited")
    assert (row.charge_id, row.charge_adjustment_id) == (c.charge, credit.id)
    assert row.change_request_id is None


def test_a_family_on_time_request_is_credited(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    request = _request(url, c, classification="on_time", makeup=False)

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]

    row = sr.decision(url, c.session, c.student)
    assert (row.initiated_by, row.change_request_id) == ("family", request)
    assert (row.classification, row.outcome, row.result) == (
        "on_time",
        "credit",
        "credited",
    )


def test_a_family_late_request_lets_the_charge_stand(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _request(url, c, classification="late", makeup=False)

    assert sr.one(url, c.session, c.student) == ["cancellation_charge_stands"]

    assert sr.adjustments(url, c.charge) == []
    row = sr.decision(url, c.session, c.student)
    assert (row.outcome, row.result, row.charge_id) == (
        "charge",
        "charge_stands",
        c.charge,
    )
    assert row.charge_adjustment_id is None


def test_a_late_request_approved_with_an_exception_is_credited(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _request(
        url,
        c,
        classification="late",
        makeup=False,
        final_classification="excused",
        final_makeup=True,
        exception=True,
    )

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]
    row = sr.decision(url, c.session, c.student)
    assert (row.classification, row.makeup_allowed) == ("excused", True)


def test_a_late_request_with_a_makeup_is_credited(module_seeded_url):
    # makeup_after_late = true: the request-time outcome allows a makeup.
    url = module_seeded_url
    c = sr.charged(url)
    _request(url, c, classification="late", makeup=True)

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]
    assert sr.decision(url, c.session, c.student).classification == "late"


def test_a_tutor_request_is_credited(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _request(url, c, initiated_by="tutor", classification="excused", makeup=True)

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]
    row = sr.decision(url, c.session, c.student)
    assert (row.initiated_by, row.classification) == ("tutor", "excused")


def test_the_rule_in_force_when_the_request_was_made_applies(module_seeded_url):
    url = module_seeded_url
    made = datetime.now(UTC) - timedelta(minutes=10)
    # late → charge at the request; a later version credits late cancellations.
    standing = sr.charged(url)
    sr.approved_request(
        url,
        standing.session,
        student=standing.student,
        classification="late",
        created_at=made,
    )
    newer = sr.add_rule(url, made + timedelta(minutes=1), late_outcome="credit")
    sr.cancel(url, standing.session)

    assert sr.one(url, standing.session, standing.student) == [
        "cancellation_charge_stands"
    ]
    assert sr.decision(url, standing.session, standing.student).rule_id != newer

    # The other direction: late → credit at the request, charge after it.
    credited = sr.charged(url)
    sr.approved_request(
        url,
        credited.session,
        student=credited.student,
        classification="late",
        created_at=made + timedelta(minutes=2),
    )
    later = sr.add_rule(url, made + timedelta(minutes=3), late_outcome="charge")
    sr.cancel(url, credited.session)

    assert sr.one(url, credited.session, credited.student) == ["cancellation_credited"]
    assert sr.decision(url, credited.session, credited.student).rule_id == newer
    assert later != newer


def test_a_no_show_follows_the_no_show_column(module_seeded_url):
    url = module_seeded_url
    made = datetime.now(UTC) - timedelta(minutes=5)
    charged = sr.charged(url)
    sr.approved_request(
        url,
        charged.session,
        student=charged.student,
        classification="no_show",
        created_at=made,
    )
    sr.cancel(url, charged.session)
    assert sr.one(url, charged.session, charged.student) == [
        "cancellation_charge_stands"
    ]
    sr.add_rule(url, made + timedelta(seconds=30), no_show_outcome="credit")
    credited = sr.charged(url)
    sr.approved_request(
        url,
        credited.session,
        student=credited.student,
        classification="no_show",
        created_at=made + timedelta(minutes=1),
    )
    sr.cancel(url, credited.session)

    assert sr.one(url, credited.session, credited.student) == ["cancellation_credited"]


# --- E4: edge cases ------------------------------------------------------------------


def test_a_cancel_before_the_sweep_with_credit_is_never_charged(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY))
    sr.cancel(url, it.session)
    scs.set_floor(url, sr.FLOOR)

    assert sr.run(url).cancellation_no_charge == 1
    assert scs.run(url).posted == 0

    assert sr.charges_of(url, it.session) == []
    assert sr.decision(url, it.session, it.student).result == "no_charge"


def test_a_late_cancel_before_the_sweep_is_charged_as_the_sweeper_would(
    module_seeded_url,
):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY))
    scs.set_offset(url, it.family, 5)
    sr.approved_request(url, it.session, student=it.student, classification="late")
    sr.cancel(url, it.session)
    scs.set_floor(url, sr.FLOOR)

    assert sr.run(url).cancellation_charged == 1

    [charge] = sr.charges_of(url, it.session)
    assert Money(charge.amount) == Money("250.00")
    assert (charge.service_date, charge.due_date) == (sr.DAY, sr.DAY + timedelta(5))
    assert charge.scheduled_minutes == 60
    row = sr.decision(url, it.session, it.student)
    assert (row.result, row.charge_id) == ("charged", charge.id)


def test_a_late_cancel_waits_for_billing(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY))
    sr.approved_request(url, it.session, student=it.student, classification="late")
    sr.cancel(url, it.session)

    assert sr.run(url).skipped_transient == 1
    assert sr.decision(url, it.session, it.student) is None
    scs.set_floor(url, sr.DAY + timedelta(days=1))  # after the service date
    assert sr.run(url).skipped_transient == 1

    scs.set_floor(url, sr.FLOOR)
    assert sr.run(url).cancellation_charged == 1
    assert len(sr.charges_of(url, it.session)) == 1


def test_a_late_cancel_without_a_family_is_retried(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), family=False)
    sr.approved_request(url, it.session, student=it.student, classification="late")
    sr.cancel(url, it.session)
    scs.set_floor(url, sr.FLOOR)

    assert sr.run(url).skipped_transient == 1
    scs.add_family(url, it.student)

    assert sr.run(url).cancellation_charged == 1
    assert sr.decision(url, it.session, it.student).result == "charged"


def test_a_zero_charge_is_never_credited(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url, price="0.00")
    sr.cancel(url, c.session)

    assert sr.one(url, c.session, c.student) == ["cancellation_no_charge"]
    assert sr.decision(url, c.session, c.student).result == "zero_charge"
    assert sr.adjustments(url, c.charge) == []


def test_a_charge_already_at_zero_is_not_credited_again(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    sr.set_state(url, c.session, "skipped_closure")
    assert sr.one(url, c.session, c.student) == ["closure_credited"]
    sr.set_state(url, c.session, "conflict")  # a reopened date, still credited
    sr.cancel(url, c.session)  # an occurrence-conflict decision cancels it

    assert sr.one(url, c.session, c.student) == ["cancellation_no_charge"]
    assert sr.decision(url, c.session, c.student).result == "already_zero"
    assert sr.net(url, c.charge) == Money.zero()


def test_a_series_change_cancelling_an_occurrence_is_credited(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    sr.cancel(url, c.session, reason="series_changed")

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]


def test_a_never_confirmed_cancelled_session_is_no_candidate(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), state="unassigned")
    sr.cancel(url, it.session)

    assert sr.run(url).candidates == 0
    assert sr.one(url, it.session, it.student) == ["skipped_not_applicable"]
    assert sr.decision(url, it.session, it.student) is None


def test_a_group_cancel_credits_every_participant_on_their_accounts(
    module_seeded_url,
):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), kind="group", price=None)
    scs.add_price(url, it.subject, [(scs.PRICE_FROM, "120.00")], class_type="group")
    assert it.family is not None
    second, third = sr.add_students(url, it, [it.family, None])
    first_charge = sr.post(url, it)
    others = [sr.post_for(url, it.session, s) for s in (second, third)]
    sr.cancel(url, it.session)

    assert sr.run(url).cancellation_credited == 3

    accounts = set()
    for charge in (first_charge, *others):
        [credit] = sr.adjustments(url, charge, "cancellation_credit")
        assert Money(credit.amount) == Money("-120.00")
        accounts.add(credit.billing_account_id)
    assert len(accounts) == 2


def test_a_course_session_is_never_reconciled(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), kind="course", price=None)
    sr.cancel(url, it.session)

    assert sr.run(url).candidates == 0
    assert sr.one(url, it.session, it.student) == ["skipped_not_applicable"]


def test_a_withdrawn_participant_is_skipped(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    run_sql(
        url,
        "DELETE FROM class_session_participants WHERE class_session_id = :s",
        {"s": c.session},
    )
    sr.cancel(url, c.session)

    assert sr.run(url).candidates == 0
    assert sr.one(url, c.session, c.student) == ["skipped_not_applicable"]
    assert sr.adjustments(url, c.charge) == []


# --- E8: idempotency and replay ------------------------------------------------------


def _ledger(url: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in run_sql(
            url,
            "SELECT charge_id, kind, amount, source_key FROM charge_adjustments"
            " ORDER BY source_key",
        )
    ]


def test_running_the_job_twice_changes_nothing_more(module_seeded_url):
    url = module_seeded_url
    credited = sr.charged(url)
    sr.cancel(url, credited.session)
    standing = sr.charged(url)
    _request(url, standing, classification="late")
    skipped = sr.charged(url)
    sr.set_state(url, skipped.session, "skipped_closure")
    first = sr.run(url)
    ledger = _ledger(url)
    decisions = run_sql(url, "SELECT count(*) AS n FROM session_cancellation_decisions")

    second = sr.run(url)

    assert (first.cancellation_credited, first.cancellation_charge_stands) == (1, 1)
    assert first.closure_credited == 1
    assert second.candidates == 0
    assert _ledger(url) == ledger
    assert (
        run_sql(url, "SELECT count(*) AS n FROM session_cancellation_decisions")
        == decisions
    )


def test_a_pre_existing_credit_key_is_reused(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    from app.modules.billing import service as billing

    async def _partial(session):  # type: ignore[no-untyped-def]
        return await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=c.charge,
                kind="cancellation_credit",
                amount=Money("-50.00"),
                source_key=f"cancellation_credit:{c.charge}",
            ),
        )

    existing = in_uow(url, _partial)
    sr.cancel(url, c.session)

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]

    assert len(sr.adjustments(url, c.charge)) == 1
    assert sr.decision(url, c.session, c.student).charge_adjustment_id == (
        existing.adjustment_id
    )


def test_a_pre_existing_decision_makes_it_a_skip(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    sr.cancel(url, c.session)
    [rule] = run_sql(url, "SELECT id FROM cancellation_billing_rules LIMIT 1")
    run_sql(
        url,
        "INSERT INTO session_cancellation_decisions (class_session_id, student_id,"
        " initiated_by, classification, makeup_allowed, classified_at, rule_id,"
        " outcome, result, charge_id, decided_at) VALUES (:s, :st, 'center',"
        " 'excused', true, now(), :r, 'charge', 'charge_stands', :c, now())",
        {"s": c.session, "st": c.student, "r": rule.id, "c": c.charge},
    )

    assert sr.run(url).candidates == 0
    assert sr.one(url, c.session, c.student) == ["skipped_not_applicable"]
    assert sr.adjustments(url, c.charge) == []


# --- E9, E11: immutability and audit -------------------------------------------------


def test_charges_never_change_and_every_row_is_audited_without_an_actor(
    module_seeded_url,
):
    url = module_seeded_url
    credited = sr.charged(url)
    restored = sr.charged(url)
    repriced = sr.charged(url)
    ids = [credited.charge, restored.charge, repriced.charge]
    before = run_sql(
        url, "SELECT * FROM charges WHERE id = ANY(:ids) ORDER BY id", {"ids": ids}
    )
    sr.cancel(url, credited.session)
    sr.set_state(url, restored.session, "skipped_closure")
    sr.run(url)
    sr.set_state(url, restored.session, "confirmed")
    sr.retime(url, repriced.session, 45)
    result = sr.run(url)

    assert (result.closure_restored, result.repriced + result.repriced_zero) == (1, 1)
    after = run_sql(
        url, "SELECT * FROM charges WHERE id = ANY(:ids) ORDER BY id", {"ids": ids}
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    for table in (
        "charge_adjustments",
        "session_cancellation_decisions",
        "charge_duration_repricings",
    ):
        rows = run_sql(
            url,
            f"SELECT id FROM {table} WHERE charge_id = ANY(:ids)",  # noqa: S608
            {"ids": ids},
        )
        assert rows, table
        for row in rows:
            [event] = events(url, record_type=table, record_id=row.id)
            assert (event.action, event.actor_user_id) == ("create", None)


def test_the_log_line_carries_counts_only(module_seeded_url, caplog):
    url = module_seeded_url
    c = sr.charged(url)
    sr.cancel(url, c.session)

    with caplog.at_level("INFO", logger=reconciliation.__name__):
        sr.run(url)

    [record] = [r for r in caplog.records if r.message == "Reconciled session charges"]
    assert record.cancellation_credited_count == 1
    assert "250" not in caplog.text


# --- E12: the statement bound --------------------------------------------------------


def test_one_statement_per_page_and_one_page_when_there_is_nothing(
    module_seeded_url,
):
    url = module_seeded_url
    count, (page, following) = count_statements(
        url, lambda s: reconciliation.candidate_page(s)
    )
    assert (count, page, following) == (1, [], None)

    made = [sr.charged(url) for _ in range(3)]
    for c in made:
        sr.cancel(url, c.session)
    count, (page, following) = count_statements(
        url, lambda s: reconciliation.candidate_page(s, limit=2)
    )
    assert count == 1
    assert len(page) == 2 and following == page[-1]
    count, (rest, last) = count_statements(
        url, lambda s: reconciliation.candidate_page(s, after=following, limit=2)
    )
    assert count == 1
    assert len(rest) == 1 and last is None
    assert set(page + rest) == {c.key for c in made}
