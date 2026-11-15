"""The session sweeper and packages (#94 D3, E2; L2, L3; G6, I9): a covered
key is never charged and never listed as unbilled; after the approval it is
consumed and no longer a candidate; an approved but short key is charged
once; validity is judged by the session's date; a session charged before
the purchase stays charged; group sessions are never covered; and
``post_charge`` refuses a consumed key."""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.core.pagination import OffsetParams
from app.modules.billing import service as billing
from app.workflows import session_charges
from tests.factories.billing import persist_service_price
from tests.integration import package_support as ps
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

DAY = date(2031, 3, 10)
SOLD = date(2031, 3, 1)
FLOOR = date(2031, 1, 1)


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ps.reset(module_seeded_url)


def unbilled(url: str, student: uuid.UUID) -> list[uuid.UUID]:
    page = in_uow(
        url,
        lambda s: session_charges.list_unbilled_items(
            s, student_id=student, page=OffsetParams(limit=100, offset=0)
        ),
    )
    return [item.class_session_id for item in page.items]


def no_key_has_both(url: str) -> None:
    """I9 over the module database."""
    both = run_sql(
        url,
        "SELECT c.class_session_id FROM charges c JOIN package_credit_transactions t"
        " ON t.kind = 'consume' AND t.class_session_id = c.class_session_id"
        " AND t.student_id = c.student_id WHERE c.kind = 'session'",
    )
    assert both == []


def test_a_covered_session_is_not_charged_until_approval(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, FLOOR)

    first = ps.sweep(url)

    assert (first.candidates, first.package_covered, first.posted) == (1, 1, 0)
    assert ps.session_charges(url, session) == []
    assert ps.consumes(url, session) == []

    ps.approved(url, session)

    assert [(r.package_id, r.units) for r in ps.consumes(url, session)] == [
        (package, -1)
    ]
    later = ps.sweep(url)
    assert later.candidates == 0  # the consumed key is anti-joined
    assert ps.session_charges(url, session) == []
    no_key_has_both(url)


def test_a_covered_session_is_not_an_unbilled_item(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url, session_price=None)
    ps.sell(url, w.student, ps.product(url), SOLD)
    covered = ps.session_for(url, w, DAY)
    outside = ps.session_for(url, w, date(2031, 9, 1))  # after expires_on
    ps.set_floor(url, FLOOR)

    assert unbilled(url, w.student) == [outside]
    assert covered not in unbilled(url, w.student)


def test_an_approved_short_session_is_charged_once(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, unit="minutes", quantity=60), SOLD
    )
    session = ps.session_for(url, w, DAY, minutes=90)
    ps.set_floor(url, FLOOR)

    # 60 minutes can't cover 90: not covered, so the sweeper charges it
    # right away (no approval needed).
    assert ps.sweep(url).posted == 1
    ps.approved(url, session)

    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 60
    assert len(ps.session_charges(url, session)) == 1
    assert ps.sweep(url).posted == 0
    no_key_has_both(url)


def test_approved_with_too_little_credit_left_is_charged_by_the_next_run(
    module_seeded_url,
):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url, quantity=1), SOLD)
    first = ps.session_for(url, w, DAY)
    second = ps.session_for(url, w, date(2031, 3, 11))
    ps.set_floor(url, FLOOR)

    # Both are covered while unapproved (one unit can cover either).
    assert ps.sweep(url).package_covered == 2
    ps.approved(url, first)
    ps.approved(url, second)  # short: the unit went to the first

    assert ps.remaining(url, package) == 0
    assert ps.consumes(url, second) == []
    run = ps.sweep(url)
    assert (run.candidates, run.posted) == (1, 1)
    assert len(ps.session_charges(url, second)) == 1
    assert ps.session_charges(url, first) == []
    no_key_has_both(url)


def test_an_approved_unconsumed_session_stays_charged_by_the_sweeper(
    module_seeded_url,
):
    # L3: approved with no credit, then a package bought for its date: the
    # approval decided, so the sweeper charges it (never covered later).
    url = module_seeded_url
    w = ps.world(url)
    session = ps.session_for(url, w, DAY)
    ps.approved(url, session)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    ps.set_floor(url, FLOOR)

    run = ps.sweep(url)

    assert (run.posted, run.package_covered) == (1, 0)
    assert len(ps.session_charges(url, session)) == 1
    assert ps.remaining(url, package) == 10
    no_key_has_both(url)


def test_a_session_after_the_expiry_date_is_charged(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    ps.sell(url, w.student, ps.product(url, validity_days=10), SOLD)
    after = ps.session_for(url, w, date(2031, 3, 12))
    last_day = ps.session_for(url, w, date(2031, 3, 11))
    ps.set_floor(url, FLOOR)

    run = ps.sweep(url)

    assert (run.posted, run.package_covered) == (1, 1)
    assert len(ps.session_charges(url, after)) == 1
    assert ps.session_charges(url, last_day) == []


def test_a_session_charged_before_the_purchase_stays_charged(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, FLOOR)
    assert ps.sweep(url).posted == 1
    package = ps.sell(url, w.student, ps.product(url), date(2031, 3, 5))

    ps.approved(url, session)

    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 10
    assert len(ps.session_charges(url, session)) == 1


def test_a_session_dated_before_the_purchase_is_not_covered(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    session = ps.session_for(url, w, date(2031, 2, 20))
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    ps.set_floor(url, FLOOR)

    assert ps.sweep(url).posted == 1
    assert len(ps.session_charges(url, session)) == 1
    assert ps.remaining(url, package) == 10


def test_a_group_session_is_never_covered(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)

    async def _group_price(session: AsyncSession) -> None:
        await persist_service_price(
            session,
            subject_id=w.subject,
            class_type="group",
            versions=[(ps.PRICE_FROM, "120.00")],
        )

    in_uow(url, _group_price)
    group = ps.session_for(url, w, DAY, kind="group")
    ps.set_floor(url, FLOOR)

    run = ps.sweep(url)

    assert (run.posted, run.package_covered) == (1, 0)
    [charge] = ps.session_charges(url, group)
    assert Money(charge.amount) == Money("120.00")
    ps.hook(url, group)
    assert ps.consumes(url, group) == []
    assert ps.remaining(url, package) == 10


def test_post_charge_refuses_a_consumed_key(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    ps.hook(url, session)

    async def _post(db: AsyncSession) -> None:
        await billing.post_charge(
            db,
            billing.ChargeDraft(
                student_id=w.student,
                kind="session",
                class_session_id=session,
                service_date=DAY,
                scheduled_minutes=60,
                list_amount=Money("250.00"),
                due_date_base=DAY,
            ),
        )

    with pytest.raises(billing.SessionSettledByPackageError):
        in_uow(url, _post)
    assert ps.session_charges(url, session) == []


def test_the_sweepers_step_reports_a_consumed_key_as_covered(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, FLOOR)
    ps.hook(url, session)
    item = ps.scs.Item(session, w.student, w.subject, w.level, w.family, None)

    assert ps.scs.post_one(url, item) == "package_covered"
    assert ps.session_charges(url, session) == []
    no_key_has_both(url)
