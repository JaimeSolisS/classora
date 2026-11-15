"""Payment reversals through the real app (#100 F3; C3, B3, R5, R9, E1-E3).

Allocations across two charges of two students all released, re-inserted
ones too, already released ones untouched; the charges reopen; the account
credit and #96's credit allocation no longer use the payment;
``payment_already_reversed``; ``payment_has_refunds`` (#90 QA O3); a payment
without allocations; the ledger rows and Σ ``balance_effect``; the audit
events and the unchanged original rows; and the receipt PDF's "reversed"
banner with its number kept (R9; it renders a PDF, so it needs Pango: the
API image or CI).

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.identity.permissions import OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow

TODAY = ps.TODAY


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> cs.Staff:
    return cs.staff(module_seeded_url, client, (OWNER,))


def releases_of(url: str, payment: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT r.* FROM allocation_releases r JOIN payment_allocations a"
        " ON a.id = r.allocation_id WHERE a.payment_id = :p"
        " ORDER BY r.created_at, r.id",
        {"p": payment},
    )


def statuses(client: TestClient, cookie: Any, family: object) -> dict[str, str]:
    page = get(client, cookie, ps.account_path(family, "/charges")).json()
    return {c["id"]: c["status"] for c in page["items"]}


def test_a_reversal_releases_every_allocation_and_reopens_the_charges(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, [s1, s2] = cs.new_account(url, 2)
    c1, c2, c3 = ps.charges(
        url,
        (s1, "100.00", ps.YESTERDAY),
        (s2, "80.00", TODAY),
        (s2, "60.00", ps.TOMORROW),
    )
    payment = cs.recorded_payment(
        url,
        owner.user.id,
        account,
        "250.00",
        [(c1, "100.00"), (c2, "80.00"), (c3, "60.00")],
    )
    assert ps.account_credit(url, account) == Money("10.00")
    before = cs.snapshot(url, account)

    body = cs.created(cs.reverse(client, owner.cookie, fam, payment))

    reversal = body["reversal"]
    assert reversal["reason"] == "Transferencia rechazada"
    assert reversal["reversed_by_user_id"] == str(owner.user.id)
    assert {(r["charge_id"], r["amount"]) for r in body["released"]} == {
        (str(c1), "100.00"),
        (str(c2), "80.00"),
        (str(c3), "60.00"),
    }
    assert body["payment"]["is_reversed"] is True
    assert body["payment"]["reversed_at"] == reversal["reversed_at"]
    assert body["account_credit_after"] == "0.00"
    assert statuses(client, owner.cookie, fam) == {
        str(c1): "overdue",
        str(c2): "pending",
        str(c3): "pending",
    }
    releases = releases_of(url, payment)
    assert len(releases) == 3
    assert {r.payment_reversal_id for r in releases} == {uuid.UUID(reversal["id"])}
    assert {r.released_by_user_id for r in releases} == {owner.user.id}
    [event] = events(
        url, record_type="payment_reversals", record_id=uuid.UUID(reversal["id"])
    )
    assert (event.action, event.actor_user_id) == ("create", owner.user.id)
    assert "Transferencia rechazada" in json.dumps(event.changes, ensure_ascii=False)
    for release in releases:
        [made] = events(url, record_type="allocation_releases", record_id=release.id)
        assert (made.action, made.actor_user_id) == ("create", owner.user.id)
    cs.unchanged(before, cs.snapshot(url, account))
    cs.assert_invariants(url, account)
    # E3: the ledger shows the reversal and the releases; Σ balance_effect
    # is the net balance.
    ledger = get(client, owner.cookie, ps.account_path(fam, "/ledger"), limit=100)
    entries = ledger.json()["items"]
    [row] = [e for e in entries if e["entry_type"] == "payment_reversal"]
    assert (row["amount"], row["balance_effect"]) == ("250.00", "250.00")
    assert {
        e["balance_effect"] for e in entries if e["entry_type"] == "allocation_release"
    } == {"0.00"}
    summary = get(client, owner.cookie, ps.account_path(fam)).json()
    assert summary["net_balance"] == "240.00"
    assert Money.total(Money(e["balance_effect"]) for e in entries) == Money("240.00")


def test_re_inserted_allocations_are_released_and_released_ones_untouched(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    c1, c2 = ps.charges(url, (student, "100.00", TODAY), (student, "50.00", TODAY))
    payment = cs.recorded_payment(
        url, owner.user.id, account, "150.00", [(c1, "100.00"), (c2, "50.00")]
    )

    async def _credit(session: AsyncSession) -> None:
        await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=c1,
                kind="cancellation_credit",
                amount=Money("-40.00"),
                source_key=f"test:{uuid.uuid4()}",
            ),
        )

    in_uow(url, _credit)  # releases the 100.00 line, re-inserts 60.00
    before = {a.id: a.valid for a in ps.allocations_of(url, account)}
    released_already = {a for a, valid in before.items() if not valid}

    body = cs.created(cs.reverse(client, owner.cookie, fam, payment))

    assert {(r["charge_id"], r["amount"]) for r in body["released"]} == {
        (str(c1), "60.00"),
        (str(c2), "50.00"),
    }
    assert released_already.isdisjoint(
        {uuid.UUID(r["allocation_id"]) for r in body["released"]}
    )
    assert not any(a.valid for a in ps.allocations_of(url, account))
    cs.assert_invariants(url, account)


def test_a_payment_without_allocations_is_reversible_and_leaves_the_credit(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    kept = cs.recorded_payment(url, owner.user.id, account, "30.00")
    payment = cs.recorded_payment(url, owner.user.id, account, "70.00")

    body = cs.created(cs.reverse(client, owner.cookie, fam, payment))

    assert body["released"] == []
    assert body["account_credit_after"] == "30.00"
    assert ps.account_credit(url, account) == Money("30.00")
    del kept


def test_credit_allocation_never_draws_on_a_reversed_payment(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    older = cs.recorded_payment(url, owner.user.id, account, "70.00")
    newer = cs.recorded_payment(url, owner.user.id, account, "40.00")
    [charge] = ps.charges(url, (student, "200.00", TODAY))
    cs.created(cs.reverse(client, owner.cookie, fam, older))

    allocated = ps.allocate(client, owner.cookie, fam, ps.credit_body())

    assert allocated.status_code == 201, allocated.text
    lines = allocated.json()["allocations"]
    assert [(line["payment_id"], line["amount"]) for line in lines] == [
        (str(newer), "40.00")
    ]
    assert ps.valid_allocated(url, charge) == Money("40.00")
    cs.assert_invariants(url, account)


def test_a_second_reversal_is_409_and_writes_nothing(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "70.00")
    cs.created(cs.reverse(client, owner.cookie, fam, payment))

    again = cs.reverse(client, owner.cookie, fam, payment)

    assert code_of(again) == (409, "payment_already_reversed")
    assert (
        len(
            run_sql(
                url,
                "SELECT id FROM payment_reversals WHERE payment_id = :p",
                {"p": payment},
            )
        )
        == 1
    )


def test_a_refunded_payment_cant_be_reversed(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "70.00")
    cs.created(cs.refund(client, owner.cookie, fam, payment, cs.refund_body("10.00")))

    response = cs.reverse(client, owner.cookie, fam, payment)

    assert code_of(response) == (409, "payment_has_refunds")
    assert (
        run_sql(
            url,
            "SELECT id FROM payment_reversals WHERE payment_id = :p",
            {"p": payment},
        )
        == []
    )


def test_another_familys_payment_or_an_unknown_one_is_404(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, _, _ = cs.new_account(url)
    _, other_account, _ = cs.new_account(url)
    other = cs.recorded_payment(url, owner.user.id, other_account, "70.00")
    _, [no_account] = ps.family(url)

    for family, payment in (
        (fam, other),
        (fam, uuid.uuid4()),
        (uuid.uuid4(), other),
        (no_account, other),
    ):
        assert code_of(cs.reverse(client, owner.cookie, family, payment)) == (
            404,
            "not_found",
        )
    assert (
        run_sql(
            url, "SELECT id FROM payment_reversals WHERE payment_id = :p", {"p": other}
        )
        == []
    )


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_the_reason_is_required_and_bounded(module_seeded_url, client, owner, reason):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "70.00")

    response = cs.reverse(client, owner.cookie, fam, payment, reason=reason)

    assert code_of(response) == (422, "validation_error")
    assert response.json()["errors"][0]["path"] == ["body", "reason"]


def test_the_receipt_shows_the_reversed_banner_and_keeps_its_number(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "100.00", TODAY))
    recorded = rs.record(url, owner.user.id, rs.draft(account, "100.00", rs.OLDEST))
    receipt_before = rs.receipt_of_payment(url, recorded.payment_id)
    cs.created(cs.reverse(client, owner.cookie, fam, recorded.payment_id))
    response = rs.download(client, owner.cookie, fam, recorded.receipt_id)

    assert response.status_code == 200, response.text
    text = "".join(rs.pdf_text(response.content).split())
    assert recorded.receipt_number in text
    # The family has no recipient with a language: the receipt is es (#98 P5).
    # (The date is the reversal row's created_at, the database's clock.)
    assert "ANULADOel" in text
    assert rs.receipt_of_payment(url, recorded.payment_id) == receipt_before
    del charge
