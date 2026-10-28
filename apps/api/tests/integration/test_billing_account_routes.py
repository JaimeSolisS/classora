"""Billing account read routes (#364 D1-D5; B1-B4).

The #90 E6-style fixture (``test_billing_ledger_balances``): one account,
two students, a charge in every status, partial and full allocations, an
allocation released by a credit, a reversed payment, a refund and an
unallocated payment; plus a third current member without charges (S3), and
S2 moved out of the family after its charges were posted (still listed:
it has charges on the account). The routes run with the clock frozen at
23:30 on 2031-03-10 in the center's zone, and must match
``account_balance``/``charge_balances``/``ledger_entries`` for that date.

Outstanding 700 (pending 400, partially paid 150, overdue 150), account
credit 290, net balance 410; 23 ledger rows.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.core.pagination import CursorParams
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.families import service as families
from app.modules.families.models import FamilyStudent
from app.modules.identity.permissions import GUARDIAN, OWNER, ROLE_PERMISSIONS
from app.workflows import billing_accounts
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.factories.identity import persist_user
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")
TODAY = date(2031, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LATE_EVENING = datetime(2031, 3, 10, 23, 30, tzinfo=ZONE)


def path(family_id: object, suffix: str = "") -> str:
    return f"/api/v1/families/{family_id}/billing-account{suffix}"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def read(client: TestClient, owner: CreatedUser, url: str, **params: Any) -> Any:
    """``GET`` as the Owner with the clock at :data:`LATE_EVENING`."""
    with frozen_clock(LATE_EVENING):
        response = get(client, sign_in(client, owner.username), url, **params)
    assert response.status_code == 200, response.text
    return response.json()


@dataclass(frozen=True)
class Fixture:
    family: uuid.UUID
    account: uuid.UUID
    s1: uuid.UUID
    s2: uuid.UUID
    s3: uuid.UUID
    charges: dict[str, uuid.UUID]


def _credit(charge_id: uuid.UUID, amount: str) -> AdjustmentDraft:
    return AdjustmentDraft(
        charge_id=charge_id,
        kind="cancellation_credit",
        amount=Money(amount),
        source_key=f"test:{uuid.uuid4()}",
    )


@pytest.fixture(scope="module")
def fixture(module_seeded_url: str) -> Fixture:
    async def _make(session: AsyncSession) -> Fixture:
        user = await factories.staff_user_id(session)
        family_id, [s1, s2, s3] = await factories.family_with_students(session, 3)

        async def post(student: uuid.UUID, amount: str, due: date) -> uuid.UUID:
            posted = await factories.charge(session, student, amount, due=due)
            return posted.charge_id

        c = {
            "c1": await post(s1, "100.00", TOMORROW),
            "c2": await post(s1, "200.00", TODAY),
            "c3": await post(s1, "150.00", TODAY),
            "c4": await post(s1, "120.00", YESTERDAY),
            "c5": await post(s1, "80.00", YESTERDAY),
            "c6": await post(s2, "60.00", YESTERDAY),
            "c7": await post(s2, "300.00", TODAY),
            "c8": await post(s2, "50.00", YESTERDAY),
        }
        account = await billing.ensure_billing_account(session, family_id)
        p1 = await factories.payment(session, account, "300.00", recorded_by=user)
        await factories.allocation(session, p1, c["c2"], "50.00")
        await factories.allocation(session, p1, c["c3"], "150.00")
        await factories.refund(session, p1, "30.00")
        p2 = await factories.payment(session, account, "200.00", recorded_by=user)
        await factories.allocation(session, p2, c["c4"], "20.00")
        await factories.allocation(session, p2, c["c6"], "60.00")
        p3 = await factories.payment(session, account, "100.00", recorded_by=user)
        await factories.allocation(session, p3, c["c7"], "100.00")
        await factories.reversal(session, p3)
        await factories.payment(session, account, "40.00", recorded_by=user)
        await billing.post_adjustment(session, _credit(c["c5"], "-80.00"))
        await billing.post_adjustment(session, _credit(c["c6"], "-60.00"))
        moved = await session.scalar(
            select(FamilyStudent).where(FamilyStudent.student_id == s2)
        )
        await session.delete(moved)
        await session.flush()
        return Fixture(
            family=family_id, account=account, s1=s1, s2=s2, s3=s3, charges=c
        )

    return in_uow(module_seeded_url, _make)


def _names(url: str, *ids: uuid.UUID) -> dict[str, str]:
    rows = run_sql(
        url,
        "SELECT id, given_names || ' ' || family_names AS name FROM students"
        " WHERE id = ANY(:ids)",
        {"ids": list(ids)},
    )
    return {str(row.id): row.name for row in rows}


# --- D1: no account yet ------------------------------------------------------------


def test_a_family_without_an_account_answers_with_zeros(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    family_id, students = in_uow(url, lambda s: factories.family_with_students(s, 2))

    body = read(client, owner, path(family_id))

    zero = "0.00"
    names = _names(url, *students)
    assert body["account_id"] is None
    assert body["due_offset_days"] == 0
    assert body["today"] == TODAY.isoformat()
    assert body["family"]["id"] == str(family_id)
    assert body["family"]["is_active"] is True
    for key in (
        "outstanding",
        "account_credit",
        "net_balance",
        "pending",
        "partially_paid",
        "overdue",
    ):
        assert body[key] == zero, key
    assert [s["student"] for s in body["students"]] == sorted(
        ({"id": sid, "name": name} for sid, name in names.items()),
        key=lambda s: (s["name"].casefold(), s["id"]),
    )
    assert all(
        s[k] == zero
        for s in body["students"]
        for k in ("outstanding", "pending", "partially_paid", "overdue")
    )
    charges = read(client, owner, path(family_id, "/charges"))
    assert (charges["items"], charges["total"]) == ([], 0)
    assert read(client, owner, path(family_id, "/ledger")) == {
        "items": [],
        "next_cursor": None,
    }
    rows = run_sql(
        url,
        "SELECT count(*) AS n FROM billing_accounts WHERE family_id = :f",
        {"f": family_id},
    )
    assert rows[0].n == 0


def test_a_cursor_on_a_family_without_an_account_is_invalid(
    module_seeded_url, client, owner
):
    family_id, _ = in_uow(
        module_seeded_url, lambda s: factories.family_with_students(s, 0)
    )
    response = get(
        client,
        sign_in(client, owner.username),
        path(family_id, "/ledger"),
        cursor="WyJ4Il0",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


# --- D2: the fixture ------------------------------------------------------------


def test_the_summary_matches_the_account_balance(
    module_seeded_url, client, owner, fixture
):
    url = module_seeded_url
    balance = in_uow(
        url, lambda s: billing.account_balance(s, fixture.account, today=TODAY)
    )

    body = read(client, owner, path(fixture.family))

    assert body["account_id"] == str(fixture.account)
    assert body["today"] == "2031-03-10"
    assert (
        body["outstanding"],
        body["account_credit"],
        body["net_balance"],
        body["pending"],
        body["partially_paid"],
        body["overdue"],
    ) == ("700.00", "290.00", "410.00", "400.00", "150.00", "150.00")
    assert body["outstanding"] == str(balance.outstanding)
    assert body["account_credit"] == str(balance.account_credit)
    by_student = {s.student_id: s for s in balance.by_student}
    names = _names(url, fixture.s1, fixture.s2, fixture.s3)
    expected = []
    for student in (fixture.s1, fixture.s2, fixture.s3):
        row = by_student.get(student)
        expected.append(
            {
                "student": {"id": str(student), "name": names[str(student)]},
                "outstanding": str(row.outstanding) if row else "0.00",
                "pending": str(row.pending) if row else "0.00",
                "partially_paid": str(row.partially_paid) if row else "0.00",
                "overdue": str(row.overdue) if row else "0.00",
            }
        )
    expected.sort(key=lambda s: (s["student"]["name"].casefold(), s["student"]["id"]))
    # S2 left the family but has charges; S3 is a member without any.
    assert body["students"] == expected


def test_the_charges_match_the_charge_balances(
    module_seeded_url, client, owner, fixture
):
    url = module_seeded_url
    balances = in_uow(
        url, lambda s: billing.charge_balances(s, fixture.account, today=TODAY)
    )

    body = read(client, owner, path(fixture.family, "/charges"), limit=100)

    assert body["total"] == 8
    # One service date: newest posted first.
    assert [item["id"] for item in body["items"]] == [
        str(b.charge_id) for b in reversed(balances)
    ]
    by_id = {str(b.charge_id): b for b in balances}
    for item in body["items"]:
        b = by_id[item["id"]]
        assert (
            item["student"]["id"],
            item["kind"],
            item["service_date"],
            item["due_date"],
            item["amount"],
            item["adjustments_total"],
            item["net"],
            item["allocated"],
            item["open"],
            item["status"],
        ) == (
            str(b.student_id),
            b.kind,
            b.service_date.isoformat(),
            b.effective_due_date.isoformat(),
            str(b.amount),
            str(b.adjustments_total),
            str(b.net),
            str(b.allocated),
            str(b.open),
            b.status,
        )
        assert item["list_amount"] == item["amount"]
        assert item["discount_amount"] == "0.00"
        assert item["description"] == "Cargo de prueba"
        assert item["class_session_id"] is None
        assert item["group_enrollment_id"] is None
        assert item["cohort_enrollment_id"] is None
    assert {item["status"] for item in body["items"]} == {
        "pending",
        "partially_paid",
        "paid",
        "overdue",
        "void",
    }


def test_the_ledger_matches_the_view(module_seeded_url, client, owner, fixture):
    url = module_seeded_url
    page = in_uow(
        url,
        lambda s: billing.ledger_entries(
            s, fixture.account, params=CursorParams(limit=100, cursor=None)
        ),
    )

    body = read(client, owner, path(fixture.family, "/ledger"), limit=100)

    assert body["next_cursor"] is None
    assert len(body["items"]) == 23
    assert body["items"] == [
        {
            "entry_type": e.entry_type,
            "entry_id": str(e.entry_id),
            "kind": e.kind,
            "student_id": None if e.student_id is None else str(e.student_id),
            "charge_id": None if e.charge_id is None else str(e.charge_id),
            "payment_id": None if e.payment_id is None else str(e.payment_id),
            "amount": str(e.amount),
            "balance_effect": str(e.balance_effect),
            "occurred_at": body_time(e.occurred_at),
        }
        for e in page.items
    ]
    assert Money.total(
        Money(item["balance_effect"]) for item in body["items"]
    ) == Money("410.00")


def body_time(value: datetime) -> str:
    """How the API serializes an aware datetime."""
    return str(TypeAdapter(datetime).dump_python(value, mode="json"))


def test_the_student_filter_narrows_charges_and_ledger(client, owner, fixture):
    charges = read(
        client, owner, path(fixture.family, "/charges"), student_id=str(fixture.s2)
    )
    assert {item["id"] for item in charges["items"]} == {
        str(fixture.charges[k]) for k in ("c6", "c7", "c8")
    }
    assert charges["total"] == 3

    ledger = read(
        client,
        owner,
        path(fixture.family, "/ledger"),
        student_id=str(fixture.s2),
        limit=100,
    )
    assert ledger["items"]
    assert {item["student_id"] for item in ledger["items"]} == {str(fixture.s2)}
    s2_charges = {str(fixture.charges[k]) for k in ("c6", "c7", "c8")}
    assert {item["charge_id"] for item in ledger["items"]} <= s2_charges


# --- D3: filters -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["pending"], {"c1", "c7"}),
        (["overdue"], {"c4", "c8"}),
        (["void"], {"c5", "c6"}),
        (["paid"], {"c3"}),
        (["partially_paid"], {"c2"}),
        (["paid", "void"], {"c3", "c5", "c6"}),
        (["overdue", "pending", "overdue"], {"c1", "c4", "c7", "c8"}),
    ],
)
def test_the_status_filter(client, owner, fixture, statuses, expected):
    body = read(client, owner, path(fixture.family, "/charges"), status=statuses)
    assert {item["id"] for item in body["items"]} == {
        str(fixture.charges[k]) for k in expected
    }
    assert body["total"] == len(expected)
    assert {item["status"] for item in body["items"]} == set(statuses)


def test_status_and_student_together(client, owner, fixture):
    body = read(
        client,
        owner,
        path(fixture.family, "/charges"),
        status=["overdue"],
        student_id=str(fixture.s1),
    )
    assert [item["id"] for item in body["items"]] == [str(fixture.charges["c4"])]


def test_a_student_of_another_family_gives_an_empty_page(
    module_seeded_url, client, owner, fixture
):
    _, [other] = in_uow(
        module_seeded_url, lambda s: factories.family_with_students(s, 1)
    )
    for suffix in ("/charges", "/ledger"):
        body = read(client, owner, path(fixture.family, suffix), student_id=str(other))
        assert body["items"] == []
    assert (
        read(client, owner, path(fixture.family, "/charges"), student_id=str(other))[
            "total"
        ]
        == 0
    )


@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("/charges", {"status": "open"}),
        ("/charges", {"student_id": "nope"}),
        ("/charges", {"limit": 0}),
        ("/charges", {"offset": -1}),
        ("/ledger", {"student_id": "nope"}),
        ("/ledger", {"limit": 101}),
    ],
)
def test_bad_query_values_are_422(client, owner, fixture, suffix, params):
    response = get(
        client, sign_in(client, owner.username), path(fixture.family, suffix), **params
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_a_malformed_family_id_is_422(client, owner):
    response = get(client, sign_in(client, owner.username), path("nope"))
    assert response.status_code == 422


def test_paging_past_the_end_keeps_the_total(client, owner, fixture):
    body = read(client, owner, path(fixture.family, "/charges"), limit=5, offset=5)
    assert (len(body["items"]), body["total"]) == (3, 8)
    body = read(client, owner, path(fixture.family, "/charges"), limit=5, offset=20)
    assert (body["items"], body["total"]) == ([], 8)


def test_charges_are_newest_service_date_first(module_seeded_url, client, owner):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, list[uuid.UUID]]:
        family_id, [student] = await factories.family_with_students(session, 1)
        ids = []
        for day in (date(2031, 1, 5), date(2031, 1, 7), date(2031, 1, 6)):
            posted = await factories.charge(session, student, service_date=day)
            ids.append(posted.charge_id)
        return family_id, ids

    family_id, [d5, d7, d6] = in_uow(url, _make)
    body = read(client, owner, path(family_id, "/charges"))
    assert [item["id"] for item in body["items"]] == [str(d7), str(d6), str(d5)]


# --- B4: reads never write ------------------------------------------------------


def test_reads_write_nothing(module_seeded_url, client, owner, fixture):
    url = module_seeded_url

    def snapshot() -> Any:
        return run_sql(
            url,
            "SELECT count(*) AS accounts, max(updated_at) AS updated,"
            " sum(version) AS versions FROM billing_accounts",
        )[0]

    before = snapshot()
    for suffix in ("", "/charges", "/ledger"):
        read(client, owner, path(fixture.family, suffix))
    assert snapshot() == before


# --- D4: statement bound --------------------------------------------------------


@pytest.mark.parametrize("charges", [1, 40])
def test_the_summary_costs_at_most_seven_statements(module_seeded_url, charges):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        recorder = await factories.staff_user_id(session)
        family = await persist(session, FamilyFactory)
        student = await persist_student(session, contacts=0)
        user = await persist_user(session, roles=(GUARDIAN,))
        guardian = await persist(session, GuardianFactory, user_id=user.id)
        await add_to_family(session, family, student, guardian)
        posted = None
        for _ in range(charges):
            posted = await factories.charge(session, student.id, "10.00")
        assert posted is not None
        paid = await factories.payment(
            session, posted.billing_account_id, "15.00", recorded_by=recorder
        )
        await factories.allocation(session, paid, posted.charge_id, "5.00")
        return family.id, user.id

    family_id, guardian_user = in_uow(url, _make)
    staff = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )
    guardian = Principal(
        user_id=guardian_user,
        roles=frozenset({GUARDIAN}),
        permissions=ROLE_PERMISSIONS[GUARDIAN],
    )

    for principal in (staff, guardian):
        with frozen_clock(LATE_EVENING):
            count, summary = count_statements(
                url,
                lambda s, p=principal: billing_accounts.get_summary(s, p, family_id),
            )
        assert count <= 7, principal
        assert summary.outstanding == Money("10.00") * charges - Money("5.00")
        assert summary.account_credit == Money("10.00")


def test_a_guardian_reads_their_familys_summary(module_seeded_url):
    """The workflow's guardian branch gives the same summary as staff's."""
    url = module_seeded_url

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        family = await persist(session, FamilyFactory)
        user = await persist_user(session, roles=(GUARDIAN,))
        guardian = await persist(session, GuardianFactory, user_id=user.id)
        await add_to_family(session, family, guardian)
        return family.id, user.id

    family_id, user_id = in_uow(url, _make)
    principal = Principal(
        user_id=user_id,
        roles=frozenset({GUARDIAN}),
        permissions=ROLE_PERMISSIONS[GUARDIAN],
    )
    summary = in_uow(
        url, lambda s: billing_accounts.get_summary(s, principal, family_id)
    )
    assert summary.account_id is None
    assert summary.students == []
    own = in_uow(url, lambda s: families.own_family_id(s, principal))
    assert own == family_id


# --- D5: the ledger cursor ------------------------------------------------------


def test_the_ledger_cursor_walks_every_row_once(module_seeded_url, client, owner):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> uuid.UUID:
        family_id, [student] = await factories.family_with_students(session, 1)
        for _ in range(25):
            await factories.charge(session, student, "10.00")
        return family_id

    family_id = in_uow(url, _make)
    cookie = sign_in(client, owner.username)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        response = get(client, cookie, path(family_id, "/ledger"), **params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["entry_id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == 25
    assert len(set(seen)) == 25

    response = get(client, cookie, path(family_id, "/ledger"), cursor="not-a-cursor")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"
