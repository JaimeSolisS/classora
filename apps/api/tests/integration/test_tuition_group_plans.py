"""Group per-student pricing through linked tuition plans (#93 K6, H6, D9):
group sessions inside a linked plan's window are ``tuition_priced`` (not
charged per session, not listed), sessions outside it follow #92 J2, a
linked period never gets a flat fee, linking is refused over an existing
fee or in-window session charge, and a withdrawal stops later periods."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.core.pagination import OffsetParams
from app.workflows import enrollment_charges, session_charges
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration import group_support
from tests.integration import session_charge_support as sc
from tests.integration import tuition_support as ts
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get, post
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import Timeline, owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    sc.reset(module_seeded_url)
    ts.reset(module_seeded_url)


@pytest.fixture
def tl() -> Timeline:
    return timeline(next(ts.YEARS))


def student(url: str) -> uuid.UUID:
    found = new_student(url)
    ec.add_family(url, found)
    return found


def group_with(
    url: str, client: Any, tl: Timeline, subject: uuid.UUID, students: list[uuid.UUID]
) -> dict[str, Any]:
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        return ec.make_group(client, cookie, subject, tl.monday, students, count=24)


def period_item(
    url: str, subject: uuid.UUID, group_id: uuid.UUID | None, amount: str = "800.00"
) -> uuid.UUID:
    return ec.price(
        url,
        subject,
        amount,
        class_type="group",
        pricing_model="per_period",
        period_frequency="monthly",
        group_id=group_id,
    )


def link(
    url: str,
    client: Any,
    tl: Timeline,
    student_id: uuid.UUID,
    item: uuid.UUID,
    enrollment: uuid.UUID | None,
    **values: Any,
) -> Any:
    body = {
        "student_id": str(student_id),
        "service_price_id": str(item),
        "group_enrollment_id": None if enrollment is None else str(enrollment),
        "starts_on": str(tl.monday),
        **{k: str(v) if isinstance(v, date) else v for k, v in values.items()},
    }
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        return post(client, cookie, ts.PLANS, body)


def linked(url, client, tl, student_id, item, enrollment, **values) -> dict[str, Any]:
    response = link(url, client, tl, student_id, item, enrollment, **values)
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


def local_day(row: Any) -> date:
    return row.starts_at.astimezone(ts.MX).date()


def unbilled_sessions(url: str) -> list[Any]:
    page = in_uow(
        url,
        lambda s: session_charges.list_unbilled_items(
            s, page=OffsetParams(limit=200, offset=0)
        ),
    )
    return list(page.items)


def test_sessions_inside_the_window_are_tuition_priced(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a = student(url)
    group = group_with(url, client, tl, subject, [a])
    group_id = uuid.UUID(group["id"])
    item = period_item(url, subject, group_id)
    ec.group_session_price(url, subject, "150.00")
    enrollment = ec.group_enrollment_id(url, group_id, a)
    starts_on = tl.week(2, 1)
    ends_on = date(starts_on.year, starts_on.month + 1, starts_on.day) - timedelta(
        days=1
    )
    plan = linked(
        url, client, tl, a, item, enrollment, starts_on=starts_on, ends_on=ends_on
    )
    assert plan["group"]["id"] == str(group_id)
    assert plan["group_enrollment_id"] == str(enrollment)
    ec.set_floor(url, ec.floor_of(tl))
    sessions = ec.sessions_of(url, group_id=group_id)
    ec.confirm_sessions(url, [s.id for s in sessions])

    result = ec.run_sessions(url)

    days = [local_day(s) for s in sessions]
    inside = [d for d in days if starts_on <= d <= ends_on]
    outside = [d for d in days if not starts_on <= d <= ends_on]
    assert result.tuition_priced == len(inside) > 0
    charged = [c.service_date for c in ec.charges_of(url, a, "session")]
    assert charged == outside
    assert any(d < starts_on for d in charged) and any(d > ends_on for d in charged)
    assert unbilled_sessions(url) == []


def test_a_paused_periods_sessions_are_not_charged(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a = student(url)
    group = group_with(url, client, tl, subject, [a])
    group_id = uuid.UUID(group["id"])
    item = period_item(url, subject, None)  # unscoped, the group's subject
    ec.group_session_price(url, subject, "150.00")
    enrollment = ec.group_enrollment_id(url, group_id, a)
    plan = linked(url, client, tl, a, item, enrollment)
    second = date(tl.monday.year, tl.monday.month + 1, tl.monday.day)
    ts.pause(url, uuid.UUID(plan["id"]), second, None)
    ec.set_floor(url, ec.floor_of(tl))
    sessions = ec.sessions_of(url, group_id=group_id)
    ec.confirm_sessions(url, [s.id for s in sessions])

    result = ec.run_sessions(url)

    assert result.tuition_priced == len(sessions)
    assert ec.charges_of(url, a, "session") == []


def test_a_linked_period_never_gets_a_flat_fee(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a = student(url)
    group = group_with(url, client, tl, subject, [a])  # billing off: no fee
    group_id = uuid.UUID(group["id"])
    ec.group_fee(url, subject, "900.00", group_id=group_id)
    item = period_item(url, subject, group_id)
    enrollment = ec.group_enrollment_id(url, group_id, a)
    linked(url, client, tl, a, item, enrollment, starts_on=tl.week(3, 1))
    ec.set_floor(url, ec.floor_of(tl))

    result = ec.run_enrollments(url)

    assert result.tuition_priced == 1 and result.posted == 0
    assert ec.fees(url, group_enrollment=enrollment) == []
    listed = in_uow(
        url,
        lambda s: enrollment_charges.list_unbilled_enrollments(
            s, page=OffsetParams(limit=50, offset=0)
        ),
    )
    assert [i for i in listed.items if i.enrollment_id == enrollment] == []
    # Sessions before the plan's start are charged per session (the period
    # is never fee-priced), the ones inside are tuition_priced.
    ec.group_session_price(url, subject, "150.00")
    sessions = ec.sessions_of(url, group_id=group_id)
    ec.confirm_sessions(url, [s.id for s in sessions[:6]])
    swept = ec.run_sessions(url)
    assert swept.enrollment_priced == 0
    assert swept.posted == 4 and swept.tuition_priced == 2  # weeks 1-2, week 3


def test_linking_over_a_flat_fee_or_session_charges_is_refused(
    module_seeded_url, client, tl
):
    url = module_seeded_url
    subject = new_subject(url)
    a, b = student(url), student(url)
    ec.set_floor(url, ec.floor_of(tl))
    fee_group = new_subject(url)
    ec.group_fee(url, fee_group, "900.00")
    with_fee = group_with(url, client, tl, fee_group, [a])
    fee_item = period_item(url, fee_group, None)
    fee_enrollment = ec.group_enrollment_id(url, with_fee["id"], a)
    assert ec.fees(url, group_enrollment=fee_enrollment)  # posted by the hook

    response = link(url, client, tl, a, fee_item, fee_enrollment)
    assert code_of(response) == (409, "tuition_plan_enrollment_fee_charged")

    ec.group_session_price(url, subject, "150.00")
    group = group_with(url, client, tl, subject, [b])
    item = period_item(url, subject, None)
    enrollment = ec.group_enrollment_id(url, group["id"], b)
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [sessions[2].id])  # week 2, Monday
    assert ec.run_sessions(url).posted == 1

    refused = link(url, client, tl, b, item, enrollment)
    assert code_of(refused) == (409, "tuition_plan_sessions_charged")
    after = link(url, client, tl, b, item, enrollment, starts_on=tl.week(2, 2))
    assert after.status_code == 201, after.text


def test_linked_plan_references_are_checked(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a, other = student(url), student(url)
    group = group_with(url, client, tl, subject, [a, other])
    group_id = uuid.UUID(group["id"])
    enrollment = ec.group_enrollment_id(url, group_id, a)
    other_group = group_with(url, client, tl, subject, [])
    scoped_elsewhere = period_item(url, subject, uuid.UUID(other_group["id"]))
    individual = ts.world(url, subject=subject, student=a, family=False).item
    item = period_item(url, subject, group_id)

    def code(**values: Any) -> tuple[int, str | None]:
        defaults: dict[str, Any] = {
            "student_id": a,
            "item": item,
            "enrollment": enrollment,
        }
        defaults.update(values)
        response = link(
            url,
            client,
            tl,
            defaults.pop("student_id"),
            defaults.pop("item"),
            defaults.pop("enrollment"),
            **defaults,
        )
        return code_of(response)

    price = (422, "tuition_plan_price_invalid")
    assert code(item=individual) == price
    assert code(enrollment=None) == price
    assert code(item=scoped_elsewhere) == price
    assert code(enrollment=ec.group_enrollment_id(url, group_id, other)) == (
        422,
        "tuition_plan_enrollment_invalid",
    )
    assert code(enrollment=uuid.uuid4()) == (422, "tuition_plan_enrollment_invalid")
    assert code(starts_on=tl.now.date() - timedelta(days=1)) == (
        422,
        "tuition_plan_starts_on_invalid",
    )
    assert code() == (201, None)


def test_a_withdrawal_stops_later_periods(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a = student(url)
    group = group_with(url, client, tl, subject, [a])
    group_id = uuid.UUID(group["id"])
    item = ec.price(
        url,
        subject,
        "200.00",
        class_type="group",
        pricing_model="per_period",
        period_frequency="weekly",
    )
    enrollment = ec.group_enrollment_id(url, group_id, a)
    plan = uuid.UUID(linked(url, client, tl, a, item, enrollment)["id"])
    ec.set_floor(url, ec.floor_of(tl))
    assert ts.run(url, ts.local(tl.monday)).posted == 1
    with frozen_clock(tl.at(tl.week(2, 3), "18:00")):
        cookie = owner_cookie(url, client)
        ec.withdraw_group(url, client, cookie, group_id, enrollment)

    result = ts.run(url, ts.local(tl.week(5, 1)))

    assert result.posted == 1  # week 2 starts before the withdrawal
    assert result.enrollment_ended == 3
    assert ts.starts(url, plan) == [tl.monday, tl.week(2, 1)]
    with frozen_clock(tl.at(tl.week(2, 4), "10:00")):
        cookie = owner_cookie(url, client)
        detail = get(client, cookie, f"{ts.PLANS}/{plan}").json()
    assert detail["status"] == "ended"


def test_eligible_enrollments(module_seeded_url, client, tl):
    url = module_seeded_url
    subject = new_subject(url)
    a = student(url)
    ec.set_floor(url, ec.floor_of(tl))
    fee_subject = new_subject(url)
    ec.group_fee(url, fee_subject, "900.00")
    with_fee = group_with(url, client, tl, fee_subject, [a])
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = group_support.create_group(
            client,
            cookie,
            group_support.group_body(
                subject=subject,
                starts_on=tl.monday,
                students=[a],
                start_time="18:00",
                count=24,
            ),
        )
    assert response.status_code == 201, response.text
    plain = response.json()
    item = period_item(url, subject, None)
    plain_enrollment = ec.group_enrollment_id(url, plain["id"], a)
    linked(url, client, tl, a, item, plain_enrollment)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = get(
            client, cookie, f"{ts.PLANS}/eligible-enrollments", student_id=str(a)
        )

    assert response.status_code == 200, response.text
    found = {row["group"]["id"]: row for row in response.json()}
    assert set(found) == {with_fee["id"], plain["id"]}
    assert found[with_fee["id"]]["has_fee_charge"] is True
    assert found[with_fee["id"]]["has_plan"] is False
    assert found[plain["id"]]["has_plan"] is True
    assert found[plain["id"]]["has_fee_charge"] is False
    assert found[plain["id"]]["subject"]["id"] == str(subject)
    assert found[plain["id"]]["enrolled_on"] == str(tl.now.date())
