"""The withdrawal preview (#101 F1; C1, B1-B7, S1, S6): the calculation per
subject and the statement bound.

Cohort fee on time and prorated at enrollment; a center-cancelled session
excluded; projected (unmaterialized) occurrences counted; a withdrawal after
the course ended; a finite and an open-ended group fee; a session-priced
group (no line, automatic credits listed); a linked tuition plan (the
current period pro rata inside its window, earlier periods excluded); the
J5 earlier fee as the line; billing off; a student with no family; minutes
reported; at most 20 statements."""

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import clock as app_clock
from app.workflows import withdrawals
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import auth_client
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject
from tests.integration.tuition_support import PLANS, YEARS
from tests.integration.tuition_support import run as run_tuition


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _line(found: dict[str, Any]) -> dict[str, Any]:
    [line] = found["lines"]
    return line


def test_a_fee_before_the_first_session_is_credited_whole(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        world = ws.cohort_world(url, client, tl)
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)
    assert line["basis"] == {"consumed": 0, "unused": 10, "denominator": 10}
    assert line["computed_credit"] == "3000.00"
    assert found["enrollment_status"] == "active"
    assert found["enrollment_version"] == 1
    assert found["student"]["id"] == str(world.student)
    assert found["family"]["id"] == str(world.family)
    assert found["units"] is None
    assert found["sessions"]["unused_minutes"] == 900


def test_a_prorated_fee_with_a_center_cancellation(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        ec.set_floor(url, ec.floor_of(tl))
        subject = new_subject(url)
        student = new_student(url)
        ec.add_family(url, student)
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "3000.00", cohort_id=uuid.UUID(cohort["id"]))
        clock.set(tl.at(tl.week(2, 2), "12:00"))  # Mon, Wed, Mon held: 7 left
        cookie = owner_cookie(url, client)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)
        [fee] = ec.fees(url, cohort_enrollment=enrolled["id"])
        assert f"{fee.amount:.2f}" == "2100.00"
        sessions = ec.sessions_of(url, cohort_id=cohort["id"])
        ec.cancel_session(url, sessions[-1].id)
        clock.set(tl.at(tl.week(3, 2), "12:00"))  # Wed 2 and Mon 3 held
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, cohort_enrollment_id=enrolled["id"])
    line = _line(found)
    assert line["basis"] == {"consumed": 2, "unused": 4, "denominator": 6}
    assert line["computed_credit"] == "1400.00"


def test_projected_occurrences_count(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl, session_count=30)  # 15 weeks
        materialized = ec.sessions_of(url, cohort_id=world.owner_id)
        assert len(materialized) < 30
        clock.set(ws.thursday(tl, 3))  # 6 held
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)
    assert line["basis"] == {"consumed": 6, "unused": 24, "denominator": 30}
    assert line["computed_credit"] == "2400.00"


def test_after_the_course_ended_nothing_is_credited(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 6))  # all 10 held
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)
    assert (line["policy"], line["computed_credit"]) == ("fully_used", "0.00")
    assert found["sessions"]["unused"] == 0


def test_a_finite_group_fee_is_pro_rata(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee="2000.00", count=10)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)
    assert line["kind"] == "group_enrollment"
    assert (line["policy"], line["computed_credit"]) == ("pro_rata", "1200.00")
    assert found["automatic_credits"] == []


def test_an_open_ended_group_fee_is_not_credited(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee="2000.00", count=None)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)
    assert (line["policy"], line["computed_credit"]) == ("open_ended_fee", "0.00")
    assert "open_ended_fee" in [w["code"] for w in found["warnings"]]


def test_a_session_priced_group_has_no_line_but_automatic_credits(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee=None, session_price="150.00")
        sessions = ec.sessions_of(url, group_id=world.owner_id)
        ec.confirm_sessions(url, [s.id for s in sessions[:8]])
        ec.run_sessions(url)
        staff = ws.owner(url, client)
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "300.00",
            [
                (c.id, "150.00")
                for c in ec.charges_of(url, world.student, "session")[-2:]
            ],
            ws.day(tl.now),
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    assert found["lines"] == []
    assert len(found["automatic_credits"]) == 4  # sessions 5-8
    assert found["totals"]["automatic_credits"] == "600.00"
    assert found["totals"]["credit"] == "0.00"
    assert found["totals"]["refundable_cash"] == "300.00"


def test_a_linked_tuition_period_is_pro_rata_inside_its_window(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline(next(YEARS))  # plan dates must be before 2100
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee=None, count=24)
        item = ec.price(
            url,
            world.subject,
            "200.00",
            class_type="group",
            pricing_model="per_period",
            period_frequency="weekly",
        )
        ec.set_floor(url, None)
        cookie = owner_cookie(url, client)
        response = post(
            client,
            cookie,
            PLANS,
            {
                "student_id": str(world.student),
                "service_price_id": str(item),
                "group_enrollment_id": world.enrollment_id,
                "starts_on": str(tl.monday),
            },
        )
        assert response.status_code == 201, response.text
        ec.set_floor(url, ec.floor_of(tl))
        assert run_tuition(url, tl.at(tl.week(2, 1), "08:00")).posted == 2
        clock.set(tl.at(tl.week(2, 2), "12:00"))  # Mon 2 held, Wed 2 ahead
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    line = _line(found)  # week 1 ended before the withdrawal: not a line
    assert line["kind"] == "tuition_period"
    assert line["service_date"] == str(tl.week(2, 1))
    assert line["basis"] == {"consumed": 1, "unused": 1, "denominator": 2}
    assert line["computed_credit"] == "100.00"


def test_the_j5_earlier_fee_is_the_line(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee="2000.00", count=10)
        [first_fee] = ec.fees(url, group_enrollment=world.enrollment_id)
        cookie = owner_cookie(url, client)
        ec.withdraw_group(url, client, cookie, world.owner_id, world.enrollment_id)
        clock.set(tl.at(tl.week(1, 1), "08:00"))
        cookie = owner_cookie(url, client)
        again = ec.enroll_group(client, cookie, world.owner_id, world.student)
        assert ec.fees(url, group_enrollment=again["id"]) == []  # J5
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, group_enrollment_id=again["id"])
    line = _line(found)
    assert line["charge_id"] == str(first_fee.id)
    assert line["basis"]["denominator"] == 10  # from the first period's enrolled_at
    assert line["computed_credit"] == "1200.00"


def test_billing_off_lists_nothing(module_seeded_url: str, client: TestClient) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        world = ws.cohort_world(url, client, tl, fee=None)
        ec.cohort_fee(
            url, world.subject, "1000.00", cohort_id=uuid.UUID(world.owner_id)
        )
        ec.set_floor(url, None)
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    assert found["lines"] == []
    assert found["totals"]["credit"] == "0.00"
    assert found["blocking"] == []


def test_a_student_without_a_family_is_blocking(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        world = ws.cohort_world(url, client, tl, fee=None, family=False)
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        confirm = ws.credit(client, staff.cookie, ws.body(found))
    assert [b["code"] for b in found["blocking"]] == ["student_has_no_family"]
    assert found["family"] is None
    assert code_of(confirm)[1] == "student_has_no_family"


def test_lines_on_two_accounts_are_blocking(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee="2000.00", count=24)
        [fee] = ec.fees(url, group_enrollment=world.enrollment_id)
        # The student moves to another family, which then gets a tuition charge
        # of a plan linked to the period (raw rows, as a fixture may).
        run_sql(
            url,
            "DELETE FROM family_students WHERE student_id = :s",
            {"s": world.student},
        )
        other = ec.add_family(url, world.student)
        item = ec.price(
            url,
            world.subject,
            "200.00",
            class_type="group",
            pricing_model="per_period",
            period_frequency="weekly",
        )
        _raw_plan_charge(url, other, world, item, tl.monday, fee.billing_account_id)
        clock.set(tl.at(tl.week(1, 2), "12:00"))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
    assert [b["code"] for b in found["blocking"]] == ["withdrawal_spans_accounts"]
    assert len(found["blocking"][0]["details"]["billing_account_ids"]) == 2


def _raw_plan_charge(
    url: str,
    family: uuid.UUID,
    world: ws.World,
    item: uuid.UUID,
    starts_on: date,
    _first_account: Any,
) -> None:
    [account] = run_sql(
        url,
        "INSERT INTO billing_accounts (family_id, due_offset_days) VALUES (:f, 0)"
        " RETURNING id",
        {"f": family},
    )
    [plan] = run_sql(
        url,
        "INSERT INTO tuition_plans (billing_account_id, student_id, service_price_id,"
        " group_enrollment_id, frequency, starts_on, due_offset_days)"
        " VALUES (:a, :s, :i, :e, 'weekly', :d, 0) RETURNING id",
        {
            "a": account.id,
            "s": world.student,
            "i": item,
            "e": world.enrollment_id,
            "d": starts_on,
        },
    )
    [version] = run_sql(
        url, "SELECT id FROM price_versions WHERE service_price_id = :i", {"i": item}
    )
    run_sql(
        url,
        "INSERT INTO charges (billing_account_id, student_id, kind, tuition_plan_id,"
        " service_date, service_period_end, price_version_id, list_amount,"
        " discount_amount, amount, due_date, posted_at)"
        " VALUES (:a, :s, 'tuition_period', :p, :d, :e, :v, 200, 0, 200, :d, now())",
        {
            "a": account.id,
            "s": world.student,
            "p": plan.id,
            "d": starts_on,
            "e": date.fromordinal(starts_on.toordinal() + 6),
            "v": version.id,
        },
    )


def test_an_unknown_or_ambiguous_subject(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    with frozen_clock(timeline().now):
        staff = ws.owner(url, client)
        unknown = ws.preview(client, staff.cookie, package_id=uuid.uuid4())
        none = ws.preview(client, staff.cookie)
        two = ws.preview(
            client,
            staff.cookie,
            package_id=uuid.uuid4(),
            group_enrollment_id=uuid.uuid4(),
        )
    assert code_of(unknown) == (404, "withdrawal_subject_not_found")
    assert code_of(none) == (422, "withdrawal_subject_invalid")
    assert code_of(two) == (422, "withdrawal_subject_invalid")


def test_the_preview_is_bounded_and_writes_nothing(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee="2000.00", count=10)
        clock.set(ws.thursday(tl, 2))
        before = run_sql(url, "SELECT count(*) AS n FROM audit_events")[0].n
        count, preview = count_statements(
            url,
            lambda s: withdrawals.preview_withdrawal(
                s,
                "group_enrollment",
                uuid.UUID(world.enrollment_id),
                now=app_clock.now(),
            ),
        )
        after = run_sql(url, "SELECT count(*) AS n FROM audit_events")[0].n
    assert count <= 20, count
    assert len(preview.lines) == 1
    assert before == after
