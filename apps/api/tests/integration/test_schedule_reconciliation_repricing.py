"""Duration changes of charged sessions (#99 D6, D7, E6; ADR-0006 E2, Q2,
Q8): catalog, snapshot discounts, pro rata, back to the original, a zero
delta, coalesced edits, manual credits and the clamp, group sessions,
reschedules and closeouts. See ``schedule_reconciliation_support``."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from tests.factories import billing as factories
from tests.integration import schedule_reconciliation_support as sr
from tests.integration import session_charge_support as scs
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    sr.reset(module_seeded_url)


def _price(url: str, c: sr.Charged, minutes: int, amount: str, **key: object) -> None:
    scs.add_price(
        url,
        c.item.subject,
        [(scs.PRICE_FROM, amount)],
        duration_minutes=minutes,
        **key,
    )


def _deltas(url: str, charge: object) -> list[Money]:
    return [
        Money(row.amount)
        for row in sr.adjustments(url, charge, "duration_change")  # type: ignore[arg-type]
    ]


def _latest(url: str, charge: object) -> object:
    return sr.repricings(url, charge)[-1]  # type: ignore[arg-type]


def test_a_shorter_session_is_repriced_from_the_catalog(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _price(url, c, 45, "200.00")
    [before] = run_sql(
        url, "SELECT version FROM class_sessions WHERE id = :s", {"s": c.session}
    )
    sr.retime(url, c.session, 45)

    assert sr.run(url).repriced == 1

    assert _deltas(url, c.charge) == [Money("-50.00")]
    row = _latest(url, c.charge)
    assert (row.method, row.previous_minutes, row.scheduled_minutes) == (
        "catalog",
        60,
        45,
    )
    assert (Money(row.target_amount), Money(row.delta_amount)) == (
        Money("200.00"),
        Money("-50.00"),
    )
    assert row.session_version == before.version + 1
    [adjustment] = sr.adjustments(url, c.charge)
    assert adjustment.source_key == f"duration_change:{c.charge}:{row.session_version}"
    assert row.charge_adjustment_id == adjustment.id
    assert sr.run(url).candidates == 0


def test_the_charges_discount_snapshot_is_applied_not_selected(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY))
    only_this = {"subject_ids": [str(it.subject)]}

    async def _discounts(session: AsyncSession) -> list[object]:
        return [
            await factories.persist_discount(
                session, kind="discount", percent="10", eligibility=only_this
            ),
            await factories.persist_discount(
                session,
                kind="promotion",
                amount="20.00",
                eligibility=only_this,
                starts_on=scs.PRICE_FROM,
                expires_on=sr.DAY + timedelta(days=365),
            ),
        ]

    made = in_uow(url, _discounts)
    c = sr.Charged(it, sr.post(url, it))
    assert Money(sr.charge_of(url, it.session, it.student).amount) == Money("205.00")
    _price(url, c, 45, "200.00")
    _price(url, c, 30, "150.00")
    sr.retime(url, c.session, 45)
    sr.run(url)
    run_sql(
        url,
        "UPDATE discounts SET is_active = false WHERE id = ANY(:ids)",
        {"ids": [d.id for d in made]},  # type: ignore[attr-defined]
    )
    sr.retime(url, c.session, 30)
    sr.run(url)

    # 200 − 10 % − 20 = 160, then 150 − 10 % − 20 = 115.
    assert [Money(r.target_amount) for r in sr.repricings(url, c.charge)] == [
        Money("160.00"),
        Money("115.00"),
    ]
    assert _deltas(url, c.charge) == [Money("-45.00"), Money("-45.00")]
    assert sr.net(url, c.charge) == Money("115.00")


def test_no_price_for_the_new_duration_prorates(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), price=None)
    scs.add_price(url, it.subject, [(scs.PRICE_FROM, "250.00")], duration_minutes=60)
    c = sr.Charged(it, sr.post(url, it))
    sr.retime(url, c.session, 50)

    sr.run(url)

    row = _latest(url, c.charge)
    assert (row.method, Money(row.target_amount)) == ("pro_rata", Money("208.33"))
    assert sr.net(url, c.charge) == Money("208.33")


def test_back_to_the_original_minutes_restores_the_amount(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _price(url, c, 45, "200.00")
    sr.retime(url, c.session, 45)
    sr.run(url)
    sr.retime(url, c.session, 60)

    assert sr.run(url).repriced == 1

    row = _latest(url, c.charge)
    assert (row.method, row.previous_minutes, row.scheduled_minutes) == (
        "original",
        45,
        60,
    )
    assert _deltas(url, c.charge) == [Money("-50.00"), Money("50.00")]
    assert sr.net(url, c.charge) == Money("250.00")


def test_the_same_price_gives_a_zero_row_and_no_adjustment(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)  # any-duration price: 55 minutes cost the same
    sr.retime(url, c.session, 55)

    assert sr.run(url).repriced_zero == 1

    row = _latest(url, c.charge)
    assert (Money(row.delta_amount), row.charge_adjustment_id) == (Money.zero(), None)
    assert sr.adjustments(url, c.charge) == []
    assert sr.run(url).candidates == 0


def test_two_edits_before_a_run_give_one_repricing(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _price(url, c, 45, "200.00")
    _price(url, c, 30, "150.00")
    sr.retime(url, c.session, 45)
    sr.retime(url, c.session, 30)

    assert sr.run(url).repriced == 1

    [row] = sr.repricings(url, c.charge)
    assert (row.previous_minutes, row.scheduled_minutes) == (60, 30)
    assert _deltas(url, c.charge) == [Money("-100.00")]


def _manual(url: str, charge: object, amount: str) -> None:
    async def _post(session: AsyncSession) -> object:
        return await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=charge,  # type: ignore[arg-type]
                kind="manual",
                amount=Money(amount),
                reason="Cortesía",
            ),
        )

    in_uow(url, _post)


def test_a_manual_credit_survives_and_the_net_never_goes_below_zero(
    module_seeded_url,
):
    url = module_seeded_url
    kept = sr.charged(url)
    _price(url, kept, 45, "200.00")
    _manual(url, kept.charge, "-30.00")
    sr.retime(url, kept.session, 45)
    clamped = sr.charged(url, subject=kept.item.subject, price=None)
    _manual(url, clamped.charge, "-240.00")
    sr.retime(url, clamped.session, 45)

    sr.run(url)

    # Billed 250 → target 200: −50; the manual −30 stays.
    assert _deltas(url, kept.charge) == [Money("-50.00")]
    assert sr.net(url, kept.charge) == Money("170.00")
    assert [Money(r.amount) for r in sr.adjustments(url, kept.charge, "manual")] == [
        Money("-30.00")
    ]
    row = _latest(url, clamped.charge)
    assert (row.clamped, Money(row.delta_amount)) == (True, Money("-10.00"))
    assert sr.net(url, clamped.charge) == Money.zero()


def test_a_group_session_is_repriced_per_participant(module_seeded_url):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY), kind="group", price=None)
    scs.add_price(url, it.subject, [(scs.PRICE_FROM, "120.00")], class_type="group")
    scs.add_price(
        url,
        it.subject,
        [(scs.PRICE_FROM, "90.00")],
        class_type="group",
        duration_minutes=45,
    )
    [other] = sr.add_students(url, it, [None])
    charges = [sr.post(url, it), sr.post_for(url, it.session, other)]
    sr.retime(url, it.session, 45)

    assert sr.run(url).repriced == 2

    for charge in charges:
        assert _deltas(url, charge) == [Money("-30.00")]


def test_a_reschedule_with_the_same_minutes_posts_nothing(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    before = sr.charge_of(url, c.session, c.student)
    sr.move(url, c.session, timedelta(days=2))

    assert sr.run(url).candidates == 0
    assert sr.one(url, c.session, c.student) == ["skipped_not_applicable"]
    assert tuple(sr.charge_of(url, c.session, c.student)) == tuple(before)
    assert before.service_date == sr.DAY


def test_a_move_with_new_minutes_is_repriced_at_the_original_date(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    _price(url, c, 45, "200.00")
    sr.move(url, c.session, timedelta(days=40))
    sr.retime(url, c.session, 45)

    sr.run(url)

    assert _deltas(url, c.charge) == [Money("-50.00")]
    assert sr.charge_of(url, c.session, c.student).service_date == sr.DAY


def test_a_closeout_with_other_actual_minutes_changes_nothing(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'completed', actual_minutes = 30,"
        " version = version + 1 WHERE id = :s",
        {"s": c.session},
    )

    assert sr.run(url).candidates == 0
    assert sr.adjustments(url, c.charge) == []
