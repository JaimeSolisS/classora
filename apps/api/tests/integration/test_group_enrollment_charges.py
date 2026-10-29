"""Group fees and group per-session charges (#92 G3, G9; B1, C2, C4, E1-E3,
F1, J2, J3, J8): a fee-priced period gets one flat fee (prorated only for a
finite group) and its sessions are ``enrollment_priced``; a session-priced
period's confirmed sessions are charged by ``billing:post_session_charges``
at ``group/per_session``; a group is never charged both ways."""

import uuid
from datetime import date
from typing import Any

import pytest

from app.core.pagination import OffsetParams
from app.workflows import session_charges
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import auth_client
from tests.integration.lifecycle_support import in_uow
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


def students(url: str, n: int, *, family: bool = True) -> list[uuid.UUID]:
    found = [new_student(url) for _ in range(n)]
    if family:
        for student in found:
            ec.add_family(url, student)
    return found


def unbilled_sessions(url: str) -> list[Any]:
    page = in_uow(
        url,
        lambda s: session_charges.list_unbilled_items(
            s, page=OffsetParams(limit=200, offset=0)
        ),
    )
    return list(page.items)


def test_a_fee_priced_group_gets_one_fee_per_period_and_no_session_charges(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    a, b, late = students(url, 3)
    ec.group_fee(url, subject, "900.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a, b], count=10)
    with frozen_clock(tl.at(tl.week(2, 1), "18:00")):
        cookie = owner_cookie(url, client)
        ec.enroll_group(client, cookie, group["id"], late)

    for student, proration, amount in (
        (a, (10, 10), "900.00"),
        (b, (10, 10), "900.00"),
        (late, (7, 10), "630.00"),
    ):
        period = ec.group_enrollment_id(url, group["id"], student)
        [fee] = ec.fees(url, group_enrollment=period)
        assert fee.kind == "group_enrollment"
        assert (fee.proration_remaining, fee.proration_total) == proration
        assert f"{fee.amount:.2f}" == amount
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [s.id for s in sessions[3:6]])

    result = ec.run_sessions(url)

    assert result.enrollment_priced == 9  # 3 sessions × 3 students
    assert result.posted == 0
    assert all(ec.charges_of(url, s, "session") == [] for s in (a, b, late))
    assert unbilled_sessions(url) == []


def test_an_open_ended_group_pays_the_full_fee_without_proration(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [late] = students(url, 1)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday)
    ec.group_fee(url, subject, "900.00", group_id=uuid.UUID(group["id"]))
    with frozen_clock(tl.at(tl.week(3, 1), "18:00")):
        cookie = owner_cookie(url, client)
        ec.enroll_group(client, cookie, group["id"], late)

    [fee] = ec.fees(
        url, group_enrollment=ec.group_enrollment_id(url, group["id"], late)
    )
    assert (fee.proration_remaining, fee.proration_total) == (None, None)
    assert f"{fee.amount:.2f}" == "900.00"


def test_a_session_priced_group_is_charged_per_session(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    a, b = students(url, 2)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a, b])
    group_id = uuid.UUID(group["id"])
    ec.group_session_price(url, subject, "100.00")  # unscoped, any duration
    ec.group_session_price(url, subject, "120.00", group_id=group_id)  # wins
    assert ec.charges_of(url, a, "group_enrollment") == []
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [s.id for s in sessions[:2]])

    result = ec.run_sessions(url)

    assert (result.posted, result.enrollment_priced) == (4, 0)
    for student in (a, b):
        charges = ec.charges_of(url, student, "session")
        assert [f"{c.amount:.2f}" for c in charges] == ["120.00", "120.00"]
    assert ec.run_enrollments(url).candidates == 0  # no full_course item


def test_a_duration_specific_price_ranks_above_an_any_duration_one(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [a] = students(url, 1)
    ec.group_session_price(url, subject, "100.00")
    ec.group_session_price(url, subject, "150.00", duration_minutes=60)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a])
    [first, *_] = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [first.id])

    assert ec.run_sessions(url).posted == 1
    [charge] = ec.charges_of(url, a, "session")
    assert f"{charge.amount:.2f}" == "150.00"
    assert charge.scheduled_minutes == 60


def test_a_group_without_prices_lists_its_sessions(module_seeded_url, client, subject):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [a] = students(url, 1)
    [orphan] = students(url, 1, family=False)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a, orphan])
    [first, *_] = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [first.id])

    items = {item.student.id: item for item in unbilled_sessions(url)}
    assert items[a].reason == "no_price"
    assert items[orphan].reason == "no_billing_account"
    assert items[a].class_type == "group"
    assert items[a].group is not None
    assert (str(items[a].group.id), items[a].group.name) == (
        group["id"],
        group["name"],
    )
    result = ec.run_sessions(url)
    assert (result.no_price, result.no_billing_account) == (1, 1)


def test_with_both_prices_the_fee_wins(module_seeded_url, client, subject):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [a] = students(url, 1)
    ec.group_session_price(url, subject, "100.00")
    ec.group_fee(url, subject, "800.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a], count=4)
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [s.id for s in sessions])

    result = ec.run_sessions(url)

    assert (result.posted, result.enrollment_priced) == (0, 4)
    [fee] = ec.charges_of(url, a, "group_enrollment")
    assert f"{fee.amount:.2f}" == "800.00"
    assert ec.charges_of(url, a, "session") == []


def test_a_back_dated_fee_never_lands_on_session_charges(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [a] = students(url, 1)
    ec.group_session_price(url, subject, "100.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a])
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [s.id for s in sessions[:2]])
    assert ec.run_sessions(url).posted == 2

    ec.group_fee(url, subject, "800.00", effective_from=date(2000, 1, 1))
    result = ec.run_enrollments(url)

    assert (result.candidates, result.session_priced, result.posted) == (1, 1, 0)
    assert ec.charges_of(url, a, "group_enrollment") == []
    ec.confirm_sessions(url, [s.id for s in sessions[2:4]])
    assert ec.run_sessions(url).posted == 2
    assert len(ec.charges_of(url, a, "session")) == 4


def test_a_per_session_price_added_after_the_fee_charges_no_session(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    [a] = students(url, 1)
    ec.group_fee(url, subject, "800.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a])
    assert len(ec.charges_of(url, a, "group_enrollment")) == 1
    ec.group_session_price(url, subject, "100.00")
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [s.id for s in sessions[:3]])

    result = ec.run_sessions(url)

    assert (result.posted, result.enrollment_priced) == (0, 3)
    assert ec.charges_of(url, a, "session") == []


def test_individual_sessions_are_charged_as_before(module_seeded_url, subject):
    # #91 regression (G9): an individual session still posts at its
    # individual/per_session price.
    from tests.integration import session_charge_support as sc

    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    it = sc.item(url, starts_at=sc.local(tl.monday, 9))

    assert ec.run_sessions(url).posted == 1
    [charge] = sc.charges_of(url, it.session)
    assert f"{charge.amount:.2f}" == "250.00"
