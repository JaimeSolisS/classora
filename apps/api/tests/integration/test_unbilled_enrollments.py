"""``GET /api/v1/billing/unbilled-enrollments`` (#92 F2, F3, G8, J9) through
the real app, and ``list_unbilled_enrollments`` directly for the statement
bound.

Both reasons and kinds (a cohort's ``no_price`` before
``no_billing_account``); the ``reason``, ``kind`` and ``student_id``
filters; the order; pagination; billing off; posted, session-priced and
already-charged periods excluded; the statement bound; list ⇔ sweeper
consistency; the access matrix. Each test sets the floor in its own decade
(:mod:`tests.integration.enrollment_charge_support`), so a page holds the
test's own periods only.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import enrollment_charges
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject

UNBILLED = "/api/v1/billing/unbilled-enrollments"
ITEM_KEYS = {
    "kind",
    "enrollment_id",
    "enrolled_at",
    "withdrawn_at",
    "service_date",
    "student",
    "family",
    "education_level",
    "subject",
    "group",
    "cohort",
    "reason",
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> dict[str, Any]:
    response = get(client, cookie, UNBILLED, **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def scenario(url: str, client: TestClient) -> dict[str, Any]:
    """In a fresh decade, with billing on:

    - cohort ``bare`` (no price): ``a`` (family) and ``b`` (no family), both
      ``no_price``;
    - cohort ``priced``: ``c`` (no family) ``no_billing_account``, ``d``
      (family) posted;
    - group ``fee`` (a group fee): ``e`` (no family) ``no_billing_account``;
    - group ``per`` (only a per-session price): ``f`` not listed;
    - cohort ``priced`` again: ``d`` withdrawn and re-enrolled, the new
      period ``already_charged``, not listed.
    """
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    other = new_subject(url)
    a, b, c, d, e, f = (new_student(url) for _ in range(6))
    for student in (a, d, f):
        ec.add_family(url, student)
    ec.group_session_price(url, other, "90.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        bare = ec.make_cohort(client, cookie, subject, tl.monday)
        priced = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "800.00", cohort_id=uuid.UUID(priced["id"]))
        fee = ec.make_group(client, cookie, subject, tl.monday)
        ec.group_fee(url, subject, "500.00", group_id=uuid.UUID(fee["id"]))
        per = ec.make_group(client, cookie, other, tl.monday, [f])
        periods = {
            "a": ec.enroll_cohort(client, cookie, bare["id"], a)["id"],
            "b": ec.enroll_cohort(client, cookie, bare["id"], b)["id"],
            "c": ec.enroll_cohort(client, cookie, priced["id"], c)["id"],
            "d": ec.enroll_cohort(client, cookie, priced["id"], d)["id"],
        }
        ec.enroll_group(client, cookie, fee["id"], e)
    periods["e"] = str(ec.group_enrollment_id(url, fee["id"], e))
    with frozen_clock(tl.now + timedelta(minutes=5)):
        cookie = owner_cookie(url, client)
        ec.withdraw_cohort(url, client, cookie, priced["id"], periods["d"])
        periods["d2"] = ec.enroll_cohort(client, cookie, priced["id"], d)["id"]
    return {
        "tl": tl,
        "students": {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f},
        "periods": periods,
        "bare": bare,
        "priced": priced,
        "fee": fee,
        "per": per,
        "subject": subject,
    }


def by_period(body: dict[str, Any]) -> dict[str, str]:
    return {item["enrollment_id"]: item["reason"] for item in body["items"]}


def test_both_reasons_and_kinds_with_the_item_shape(client, staff, module_seeded_url):
    url = module_seeded_url
    s = scenario(url, client)
    p = s["periods"]

    body = listed(client, staff)

    assert by_period(body) == {
        p["a"]: "no_price",
        p["b"]: "no_price",  # J9 (choice): no_price first for a cohort
        p["c"]: "no_billing_account",
        p["e"]: "no_billing_account",
    }
    assert body["total"] == 4
    item = next(i for i in body["items"] if i["enrollment_id"] == p["e"])
    assert set(item) == ITEM_KEYS
    assert item["kind"] == "group_enrollment"
    assert item["group"] == {"id": s["fee"]["id"], "name": s["fee"]["name"]}
    assert item["cohort"] is None
    assert item["family"] is None
    assert item["withdrawn_at"] is None
    assert item["service_date"] == s["tl"].now.date().isoformat()
    assert item["subject"]["id"] == str(s["subject"])
    cohort_item = next(i for i in body["items"] if i["enrollment_id"] == p["a"])
    assert cohort_item["kind"] == "cohort_enrollment"
    assert cohort_item["cohort"] == {
        "id": s["bare"]["id"],
        "name": s["bare"]["name"],
    }
    assert cohort_item["group"] is None
    assert cohort_item["family"] is not None


def test_filters_order_and_pagination(client, staff, module_seeded_url):
    url = module_seeded_url
    s = scenario(url, client)
    p = s["periods"]

    assert set(by_period(listed(client, staff, reason="no_price"))) == {
        p["a"],
        p["b"],
    }
    assert set(by_period(listed(client, staff, kind="group_enrollment"))) == {p["e"]}
    both = listed(client, staff, kind=["group_enrollment", "cohort_enrollment"])
    assert both["total"] == 4
    assert set(
        by_period(listed(client, staff, student_id=str(s["students"]["c"])))
    ) == {p["c"]}

    body = listed(client, staff)
    order = [
        (i["service_date"], i["student"]["name"].casefold(), i["enrollment_id"])
        for i in body["items"]
    ]
    assert order == sorted(order)
    first = listed(client, staff, limit=3, offset=0)
    rest = listed(client, staff, limit=3, offset=3)
    assert (first["total"], rest["total"]) == (4, 4)
    assert [i["enrollment_id"] for i in first["items"] + rest["items"]] == [
        i["enrollment_id"] for i in body["items"]
    ]


@pytest.mark.parametrize(
    "params",
    [{"reason": "billable"}, {"kind": "session"}, {"student_id": "x"}, {"limit": 0}],
)
def test_an_invalid_query_is_422(client, staff, params):
    assert code_of(get(client, staff, UNBILLED, **params)) == (
        422,
        "validation_error",
    )


def test_billing_off_is_an_empty_page(client, staff, module_seeded_url):
    url = module_seeded_url
    scenario(url, client)
    ec.set_floor(url, None)

    assert listed(client, staff) == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_the_list_matches_what_the_sweeper_skips(client, staff, module_seeded_url):
    url = module_seeded_url
    s = scenario(url, client)
    body = listed(client, staff)
    reasons = list(by_period(body).values())

    result = ec.run_enrollments(url)

    assert result.no_price == reasons.count("no_price")
    assert result.no_billing_account == reasons.count("no_billing_account")
    assert result.already_charged == 1  # d's second period
    assert result.posted == 0
    assert result.failed == 0
    assert s["periods"]["d2"] not in by_period(body)


def test_the_statement_bound(client, module_seeded_url):
    url = module_seeded_url
    scenario(url, client)

    count, page = count_statements(
        url,
        lambda session: enrollment_charges.list_unbilled_enrollments(
            session, page=OffsetParams(limit=100, offset=0)
        ),
    )

    assert page.total == 4
    # Thirteen statements (the floor, the zone, candidates and ledger facts
    # per kind, three for the schedules, students, families, custom prices,
    # level names) and the memoized fee lookups (two cohorts, one group).
    assert count <= 13 + 3, count


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (FINANCE_STAFF, True),
        (ACADEMIC_COORDINATOR, False),
        (TUTOR, False),
        (STUDENT, False),
        (GUARDIAN, False),
    ],
)
def test_access(client, module_seeded_url, role, allowed):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    response = get(client, cookie, UNBILLED)

    if allowed:
        assert response.status_code == 200, response.text
    else:
        assert code_of(response) == (403, "permission_denied")


def test_anonymous_is_401(client):
    client.cookies.clear()
    assert client.get(UNBILLED).status_code == 401
