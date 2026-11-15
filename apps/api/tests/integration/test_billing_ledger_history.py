"""The ledger history (#90 E8; D4, A6; data-model §3.26).

``ledger_entries`` reads ``billing_ledger_entries``: every row type once
with the A6 values, newest first by ``(occurred_at, entry_id)``; the cursor
walks 25 rows in pages of 10 with no gap or repeat; the ``student_id``
filter keeps only that student's charge, adjustment, allocation and
release rows; a bad cursor is ``InvalidCursorError``; one statement per
page.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.core.pagination import CursorParams, InvalidCursorError, encode_cursor
from app.modules.billing import service as billing
from tests.factories import billing as factories
from tests.integration.billing_ledger_support import (
    LedgerRows,
    count_statements,
    ledger_rows,
)
from tests.integration.lifecycle_support import in_uow


@pytest.fixture(scope="module")
def rows(module_seeded_url: str) -> LedgerRows:
    return ledger_rows(module_seeded_url)


def _page(
    url: str,
    account: uuid.UUID,
    *,
    limit: int = 100,
    cursor: str | None = None,
    student_id: uuid.UUID | None = None,
) -> Any:
    return in_uow(
        url,
        lambda s: billing.ledger_entries(
            s,
            account,
            params=CursorParams(limit=limit, cursor=cursor),
            student_id=student_id,
        ),
    )


def test_every_row_type_appears_once_with_its_values(module_seeded_url, rows):
    page = _page(module_seeded_url, rows.account)

    got = {
        (e.entry_type, e.entry_id): (
            e.kind,
            e.student_id,
            e.charge_id,
            e.payment_id,
            str(e.amount),
            str(e.balance_effect),
        )
        for e in page.items
    }
    by_type = {entry_type for entry_type, _ in got}
    assert by_type == {
        "charge",
        "adjustment",
        "payment",
        "payment_reversal",
        "allocation",
        "allocation_release",
        "refund",
    }
    s, c = rows.student, rows.charge
    assert got[("charge", c)] == ("manual", s, c, None, "500.00", "500.00")
    assert got[("adjustment", rows.adjustment)] == (
        "manual",
        s,
        c,
        None,
        "100.00",
        "100.00",
    )
    assert got[("payment", rows.payment)] == (
        "cash",
        None,
        None,
        rows.payment,
        "300.00",
        "-300.00",
    )
    assert got[("payment_reversal", rows.reversal)] == (
        None,
        None,
        None,
        rows.reversed_payment,
        "100.00",
        "100.00",
    )
    assert got[("allocation", rows.allocation)] == (
        None,
        s,
        c,
        rows.payment,
        "200.00",
        "0.00",
    )
    assert got[("allocation_release", rows.release)] == (
        None,
        s,
        c,
        rows.reversed_payment,
        "100.00",
        "0.00",
    )
    assert got[("refund", rows.refund)] == (
        "cash",
        None,
        None,
        rows.payment,
        "50.00",
        "50.00",
    )
    # A: 1 charge, 1 adjustment, 2 payments, 1 reversal, 2 allocations,
    # 1 release, 1 refund. Nothing of account B.
    assert len(page.items) == 9
    assert {e.billing_account_id for e in page.items} == {rows.account}
    positions = [(e.occurred_at, e.entry_id) for e in page.items]
    assert positions == sorted(positions, reverse=True)
    assert page.next_cursor is None
    # Σ balance_effect = net balance: 600 − 400 + 100 + 50 = 350.
    assert Money.total(e.balance_effect for e in page.items) == Money("350.00")


def test_the_student_filter_keeps_that_students_rows(module_seeded_url, rows):
    page = _page(module_seeded_url, rows.account, student_id=rows.student)

    assert sorted(e.entry_type for e in page.items) == [
        "adjustment",
        "allocation",
        "allocation",
        "allocation_release",
        "charge",
    ]
    assert {e.student_id for e in page.items} == {rows.student}
    assert (
        _page(module_seeded_url, rows.account, student_id=rows.other_student).items
        == []
    )


def test_the_cursor_walks_every_row_once(module_seeded_url):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> uuid.UUID:
        _, [student] = await factories.family_with_students(session)
        posted = None
        for n in range(25):
            posted = await factories.charge(session, student, f"{n + 1}.00")
        assert posted is not None
        return posted.billing_account_id

    account = in_uow(url, _make)

    seen: list[uuid.UUID] = []
    sizes: list[int] = []
    cursor: str | None = None
    while True:
        count, page = count_statements(
            url,
            lambda s, c=cursor: billing.ledger_entries(
                s, account, params=CursorParams(limit=10, cursor=c)
            ),
        )
        assert count == 1
        seen.extend(e.entry_id for e in page.items)
        sizes.append(len(page.items))
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sizes == [10, 10, 5]
    assert len(seen) == len(set(seen)) == 25
    assert seen == [e.entry_id for e in _page(url, account).items]


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        encode_cursor(["2031-03-10T12:00:00", str(uuid.uuid4())]),
        encode_cursor(["yesterday", str(uuid.uuid4())]),
        encode_cursor(["2031-03-10T12:00:00+00:00", "not-a-uuid"]),
        encode_cursor([1, 2]),
    ],
)
def test_a_bad_cursor_is_refused(module_seeded_url, rows, cursor):
    with pytest.raises(InvalidCursorError):
        _page(module_seeded_url, rows.account, cursor=cursor)
