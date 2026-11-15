"""Package consumption (#94 D1, D2, E1; L2-L4, L6; G3 order, G4
insufficient credit, G5 double approval): first-expiring-first across
packages and unit kinds, subject scoping, the student's current family only,
scheduled (not actual) minutes, all or nothing, and approval revision 1
only."""

import uuid
from datetime import date, timedelta

import pytest

from app.core.errors import AppError
from app.modules.billing import service as billing
from tests.integration import package_support as ps
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

DAY = date(2031, 3, 10)
SOLD = date(2031, 3, 1)


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ps.reset(module_seeded_url)


def test_the_earlier_expiring_sessions_package_is_used_first(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    late = ps.sell(url, w.student, ps.product(url, validity_days=120), SOLD)
    early = ps.sell(url, w.student, ps.product(url, validity_days=30), SOLD)
    session = ps.session_for(url, w, DAY, minutes=120)

    result = ps.consume(url, session, w.student)

    assert result.outcome == "consumed"
    assert result.consumed == ((early, 1),)
    [row] = ps.consumes(url, session)
    assert (row.package_id, row.units, row.student_id) == (early, -1, w.student)
    assert ps.remaining(url, early) == 9
    assert ps.remaining(url, late) == 10


def test_minutes_are_split_across_two_packages(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    first = ps.sell(url, w.student, ps.product(url, unit="minutes", quantity=30), SOLD)
    second = ps.sell(
        url,
        w.student,
        ps.product(url, unit="minutes", quantity=60, validity_days=120),
        SOLD,
    )
    session = ps.session_for(url, w, DAY, minutes=60)

    assert ps.consume(url, session, w.student).consumed == ((first, 30), (second, 30))
    assert {(r.package_id, r.units) for r in ps.consumes(url, session)} == {
        (first, -30),
        (second, -30),
    }
    assert ps.remaining(url, first) == 0
    assert ps.remaining(url, second) == 30


def test_mixed_units_take_the_path_whose_first_package_expires_first(
    module_seeded_url,
):
    url = module_seeded_url
    w = ps.world(url)
    sessions = ps.sell(url, w.student, ps.product(url, validity_days=120), SOLD)
    minutes = ps.sell(
        url,
        w.student,
        ps.product(url, unit="minutes", quantity=120, validity_days=30),
        SOLD,
    )
    first = ps.session_for(url, w, DAY, minutes=60)
    second = ps.session_for(url, w, DAY + timedelta(days=1), minutes=90)

    assert ps.consume(url, first, w.student).consumed == ((minutes, 60),)
    # 60 minutes left can't cover 90: the sessions path covers it whole.
    assert ps.consume(url, second, w.student).consumed == ((sessions, 1),)
    assert ps.remaining(url, minutes) == 60
    assert ps.remaining(url, sessions) == 9


def test_subject_scoped_and_any_subject_packages(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    other = ps.world(url)
    scoped = ps.sell(
        url,
        w.student,
        ps.product(url, subject=other.subject, validity_days=10),
        SOLD,
    )
    any_subject = ps.sell(url, w.student, ps.product(url, validity_days=60), SOLD)
    mine = ps.session_for(url, w, DAY)
    theirs = ps.session_for(url, w, DAY, subject=other.subject)

    # The scoped package expires first but is for another subject.
    assert ps.consume(url, mine, w.student).consumed == ((any_subject, 1),)
    assert ps.consume(url, theirs, w.student).consumed == ((scoped, 1),)


def test_a_siblings_package_is_never_used(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    brother = ps.sibling(url, w)
    package = ps.sell(url, brother, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)

    assert ps.consume(url, session, w.student).outcome == "no_package"
    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 10


def test_after_a_family_move_the_old_familys_package_is_never_used(
    module_seeded_url,
):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    family = ps.move_to_new_family(url, w.student)
    # The new family has an account too, so the lookup reaches the packages.
    in_uow(url, lambda db: billing.ensure_billing_account(db, family))
    session = ps.session_for(url, w, DAY)
    item = billing.CoverageQuery(
        class_session_id=session,
        student_id=w.student,
        subject_id=w.subject,
        service_date=DAY,
        scheduled_minutes=60,
    )

    assert in_uow(url, lambda db: billing.package_covered_keys(db, [item])) == set()
    assert ps.consume(url, session, w.student).outcome == "no_package"
    assert ps.remaining(url, package) == 10


def test_minutes_consumed_are_the_scheduled_minutes(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, unit="minutes", quantity=300), SOLD
    )
    session = ps.session_for(url, w, DAY, minutes=90)
    run_sql(
        url,
        "UPDATE class_sessions SET actual_minutes = 40 WHERE id = :s",
        {"s": session},
    )

    ps.hook(url, session)

    assert [r.units for r in ps.consumes(url, session)] == [-90]
    assert ps.remaining(url, package) == 210


def test_validity_is_judged_by_the_service_date(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url, validity_days=10), SOLD)
    last_day = ps.session_for(url, w, date(2031, 3, 11))
    after = ps.session_for(url, w, date(2031, 3, 12))
    before = ps.session_for(url, w, date(2031, 2, 28))

    assert ps.consume(url, last_day, w.student).outcome == "consumed"
    assert ps.consume(url, after, w.student).outcome == "no_package"
    assert ps.consume(url, before, w.student).outcome == "no_package"
    assert ps.remaining(url, package) == 9


def test_a_90_minute_session_with_60_minutes_left_is_short(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, unit="minutes", quantity=60), SOLD
    )
    session = ps.session_for(url, w, DAY, minutes=90)

    assert ps.consume(url, session, w.student).outcome == "short"
    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 60


def test_a_sessions_package_with_nothing_left_is_not_eligible(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url, quantity=1), SOLD)
    first = ps.session_for(url, w, DAY)
    second = ps.session_for(url, w, DAY + timedelta(days=1))

    assert ps.consume(url, first, w.student).outcome == "consumed"
    assert ps.consume(url, second, w.student).outcome == "no_package"
    assert ps.remaining(url, package) == 0


def test_a_charged_session_is_never_consumed(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, date(2031, 1, 1))
    item = ps.scs.Item(session, w.student, w.subject, w.level, w.family, None)
    assert ps.scs.post_one(url, item) == "posted"  # no package yet
    package = ps.sell(url, w.student, ps.product(url), SOLD)

    assert ps.consume(url, session, w.student).outcome == "charged"
    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 10


def test_an_absent_participant_is_consumed_at_approval(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)

    ps.approve(url, ps.submit(url, session, w.student, status="absent"))

    assert [(r.package_id, r.units) for r in ps.consumes(url, session)] == [
        (package, -1)
    ]


def test_group_and_course_sessions_are_never_consumed(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    group = ps.session_for(url, w, DAY, kind="group")

    ps.hook(url, group)

    assert ps.consumes(url, group) == []
    assert ps.remaining(url, package) == 10


def test_a_double_approval_and_a_correction_consume_once(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    closed = ps.submit(url, session, w.student)

    ps.approve(url, closed)
    ps.hook(url, session)  # the hook again for revision 1: already consumed
    ps.correct(url, closed, ps.closeout_version(url, session), 45)  # revision 2

    assert [(r.package_id, r.units) for r in ps.consumes(url, session)] == [
        (package, -1)
    ]
    assert ps.remaining(url, package) == 9
    with pytest.raises(AppError) as refused:
        ps.approve(url, closed, version=ps.closeout_version(url, session))
    assert refused.value.code == "closeout_not_submitted"
    assert ps.remaining(url, package) == 9


def test_a_later_revision_consumes_nothing(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)

    ps.hook(url, session, supersedes=uuid.uuid7())

    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 10


def test_a_cancelled_sessions_claim_is_accepted(module_seeded_url):
    # #99 consumes for a cancelled session's 'charge' outcome: the primitive
    # doesn't check the state.
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY, state="cancelled")

    assert ps.consume(url, session, w.student).outcome == "consumed"
    assert ps.remaining(url, package) == 9


def test_coverage_is_one_batch_of_at_most_three_statements(module_seeded_url):
    url = module_seeded_url
    items = []
    for n in range(6):
        w = ps.world(url)
        ps.sell(url, w.student, ps.product(url, quantity=1 + n % 2), SOLD)
        session = ps.session_for(url, w, DAY + timedelta(days=n))
        items.append(
            billing.CoverageQuery(
                class_session_id=session,
                student_id=w.student,
                subject_id=w.subject,
                service_date=DAY + timedelta(days=n),
                scheduled_minutes=60,
            )
        )
    approved = billing.CoverageQuery(**{**vars(items[0]), "closeout_approved": True})

    count, covered = count_statements(
        url, lambda s: billing.package_covered_keys(s, items)
    )
    _, approved_covered = count_statements(
        url, lambda s: billing.package_covered_keys(s, [approved])
    )

    assert count <= 3, count
    assert covered == {(i.class_session_id, i.student_id) for i in items}
    assert approved_covered == set()  # approved and not consumed: not covered
