"""Manual payroll adjustments through the real route (M7-08c A, B1, B3, D4-D7,
E1, E2, E4, E5; ADR-0017 E11 and its M7-08c notes; data-model §11.9, §13.2).

Closeouts, re-approvals, rate, terms and item writes, ``prepare``,
``finalize`` and ``recalculate`` go through their routes; terms, rates and
periods are set by SQL, as in every payroll test, and so is a row no route
can write (stated where it happens). Per-course scenes and the ``no_rate``
trap are in ``test_payroll_manual_adjustment_flows.py``, the carry-forward in
``test_payroll_carry_forward.py``, races in
``test_payroll_manual_adjustments_concurrency.py``.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import REPLAYED_HEADER
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.workflows import (
    payroll_adjustments,
    payroll_calculation,
    payroll_manual_adjustments,
)
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import post as post_as
from tests.integration.conftest import run_sql, run_sql_async, settings_for
from tests.integration.corrections_support import Staff, staff
from tests.integration.guardian_support import delete
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_terms,
    approved_lesson,
    clear,
    lines,
    paid_tutor,
)
from tests.integration.payroll_manual_support import (
    BEFORE_FIRST,
    CREATED,
    DENIED,
    LINE_NOT_FINALIZED,
    NOT_FOUND,
    NOT_UNRESOLVED,
    OWN_PAY,
    POSTED_KEYS,
    REASON,
    REAUTH,
    SETTLED,
    adjustments,
    audit_rows,
    manual,
    period_total,
    send,
    stored_keys,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    UNRESOLVED,
    code,
    locked,
    new_key,
    prepare,
    read,
    recalculate,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson
from tests.integration.test_closeout_review import correct_closeout, version_of

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    """Per test: the tests read the keys this user stored."""
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


# --- Helpers -----


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def items(url: str, tutor: CreatedUser) -> list[tuple[str, str]]:
    async def read_items(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    found = in_uow(url, read_items)
    return [(str(item.class_session_id), item.reason) for item in found]


def blocking(url: str, period: uuid.UUID) -> list[str]:
    async def read_blocking(session: AsyncSession) -> Any:
        from app.modules.payroll import service as payroll

        summary = (await payroll.period_lines(session, period)).period
        return await payroll_calculation.blocking_work(session, summary)

    return [str(item.class_session_id) for item in in_uow(url, read_blocking)]


def frozen_state(url: str, period: uuid.UUID) -> tuple[Any, list[Any]]:
    """The period's row and every line, all columns."""
    return row_of(url, period), run_sql(
        url,
        "SELECT * FROM payroll_lines WHERE payroll_period_id = :p ORDER BY id",
        {"p": period},
    )


def unpriced(url: str, tutor: CreatedUser) -> tuple[uuid.UUID, uuid.UUID]:
    """Hourly terms from long ago and no rate: the week of ``STARTS`` and the
    next."""
    first = paid_tutor(url, tutor.id, rate=None)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    return first, second


def late_unpriced(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> tuple[Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    """#110 round 3's shape: the week finalized by route while its closeout was
    unapproved, then approved with no rate: ``no_rate`` work in a finalized
    period."""
    first, second = unpriced(url, tutor)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    body = approved_lesson(client, made, owner.user, minutes=60)
    return made, first, second, body


def paid_week(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> tuple[Any, uuid.UUID, uuid.UUID]:
    """90 minutes at 120.00 an hour, finalized by route; the next week open."""
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user, minutes=90)
    locked(client, url, finance, owner, first)
    return made, first, second


def errors_of(response: Any) -> list[tuple[Any, str]]:
    return [(tuple(e["path"]), e["code"]) for e in response.json()["errors"]]


def rate_route(client: TestClient, who: Staff, tutor: CreatedUser, on: str) -> Any:
    return post_as(
        client,
        who.cookie,
        f"/api/v1/payroll/tutors/{tutor.id}/rates",
        {
            "class_kind": "individual",
            "effective_from": on,
            "rate_model": "per_hour",
            "rate": "150",
        },
    )


# --- A1: the body -----


@pytest.mark.parametrize(
    ("amount", "reason", "error"),
    [
        ("0.00", REASON, (("body", "amount"), "amount_zero")),
        ("-0.00", REASON, (("body", "amount"), "amount_zero")),
        ("1.001", REASON, (("body", "amount"), "money_precision")),
        ("1000000000000.00", REASON, (("body", "amount"), "money_out_of_range")),
        ("5.00", "   ", (("body", "reason"), "text_empty")),
        ("5.00", "x" * 1001, (("body", "reason"), "text_too_long")),
        ("5.00", "a\x00b", (("body", "reason"), "text_invalid")),
    ],
)
def test_a1_every_body_rule_is_422_and_writes_nothing(
    module_seeded_url, client, owner, tutor, amount, reason, error
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)

    response = manual(client, owner, tutor.id, amount, reason=reason)

    assert code(response) == (422, "validation_error"), response.text
    assert errors_of(response) == [error]
    assert adjustments(url, tutor.id) == []
    assert stored_keys(url, owner.user.id) == []


def test_a1_a_reason_of_1000_characters_is_trimmed_and_kept(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)

    response = manual(client, owner, tutor.id, reason=f"  {'x' * 1000}  ")

    assert code(response) == CREATED, response.text
    [row] = adjustments(url, tutor.id)
    assert row.reason == "x" * 1000


def test_a1_both_references_an_unknown_field_and_a_lone_surrogate_are_422(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)

    both = manual(
        client,
        owner,
        tutor.id,
        origin_line_id=uuid.uuid4(),
        class_session_id=uuid.uuid4(),
    )
    assert code(both) == (422, "validation_error")
    assert errors_of(both) == [(("body",), "adjustment_references_both")]

    extra = send(
        client,
        owner,
        f"/api/v1/payroll/tutors/{tutor.id}/adjustments",
        {"amount": "5.00", "reason": REASON, "kind": "late_approval"},
    )
    assert code(extra) == (422, "validation_error")

    client.cookies.clear()
    raw = json.dumps({"amount": "5.00", "reason": "@@"}).replace("@@", "a\\ud800b")
    surrogate = client.post(
        f"/api/v1/payroll/tutors/{tutor.id}/adjustments",
        content=raw.encode(),
        headers={
            **signed_headers(owner.cookie),
            "Content-Type": "application/json",
            "Idempotency-Key": new_key(),
        },
    )
    assert code(surrogate) == (422, "validation_error")
    assert errors_of(surrogate) == [(("body",), "text_invalid")]
    assert adjustments(url, tutor.id) == []
    assert stored_keys(url, owner.user.id) == []


# --- A2: the order -----


def test_a2_permission_reauthentication_and_key_come_before_the_body(
    module_seeded_url, client, tutor
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    path = f"/api/v1/payroll/tutors/{tutor.id}/adjustments"
    coordinator = staff(url, client, (ACADEMIC_COORDINATOR,))
    unconfirmed = staff(url, client, (OWNER,), reauth=False)
    confirmed = staff(url, client, (OWNER,))

    assert code(send(client, coordinator, path, {}, keyed=False)) == DENIED
    assert code(send(client, unconfirmed, path, {}, keyed=False)) == REAUTH
    assert code(send(client, confirmed, path, {}, keyed=False)) == (
        400,
        "idempotency_key_required",
    )
    for who in (coordinator, unconfirmed, confirmed):
        assert stored_keys(url, who.user.id) == []


def test_a2_replay_reuse_and_a_refused_body_keep_one_effect(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    key = new_key()

    # A refused body claims no key: the same key then runs.
    assert code(manual(client, owner, tutor.id, "0.00", key=key))[0] == 422
    first = manual(client, owner, tutor.id, "25.00", key=key)
    assert code(first) == CREATED, first.text
    replay = manual(client, owner, tutor.id, "25.00", key=key)
    reused = manual(client, owner, tutor.id, "26.00", key=key)

    assert code(replay) == CREATED
    assert replay.headers.get(REPLAYED_HEADER) == "true"
    assert replay.json() == first.json()
    assert code(reused) == (422, "idempotency_key_reused")
    assert len(adjustments(url, tutor.id)) == 1


def test_a2_own_pay_then_the_tutor(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    teller = staff(url, client, (FINANCE_STAFF, TUTOR))
    paid_tutor(url, teller.user.id)
    not_a_tutor = create_user(url, roles=(ACADEMIC_COORDINATOR,))

    assert code(manual(client, teller, teller.user.id)) == OWN_PAY
    assert code(manual(client, finance, uuid.uuid4())) == NOT_FOUND
    assert code(manual(client, finance, not_a_tutor.id)) == NOT_FOUND
    assert adjustments(url, teller.user.id) == []


# --- A3, D5: on a line -----


def test_a3_a_line_of_another_tutor_or_none_is_404(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,))
    _, other_first, _ = paid_week(url, client, owner, finance, other)
    paid_tutor(url, tutor.id)
    [other_line] = lines(url, other_first)

    for line_id in (other_line.id, uuid.uuid4()):
        response = manual(client, owner, tutor.id, origin_line_id=line_id)
        assert code(response) == NOT_FOUND, response.text
    assert adjustments(url, tutor.id) == []


def test_a3_a_line_of_an_open_or_prepared_period_is_409(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    _, _, second = paid_week(url, client, owner, finance, tutor)
    approved_lesson(
        client, lesson(url, starts_at=STARTS + WEEK, tutor=tutor), owner.user
    )
    assert code(recalculate(client, finance, second)) == OK
    [open_line] = lines(url, second)

    opened = manual(client, owner, tutor.id, origin_line_id=open_line.id)
    assert code(opened) == LINE_NOT_FINALIZED

    assert code(prepare(client, finance, second)) == OK
    [prepared_line] = lines(url, second)
    prepared = manual(client, owner, tutor.id, origin_line_id=prepared_line.id)
    assert code(prepared) == LINE_NOT_FINALIZED
    assert adjustments(url, tutor.id) == []


def test_d5_a_wrong_finalized_rate_is_a_new_version_and_a_manual_on_the_line(
    module_seeded_url, client, owner, finance, tutor
):
    """#110 E5: the new version after the line's date (201), and the difference
    by hand on the line. The finalized period never changes."""
    url = module_seeded_url
    _, first, second = paid_week(url, client, owner, finance, tutor)
    [line] = lines(url, first)
    assert line.amount == Decimal("180.00")
    before = frozen_state(url, first)

    assert code_of(rate_route(client, owner, tutor, "2026-11-04")) == CREATED
    response = manual(client, owner, tutor.id, "45.00", origin_line_id=line.id)

    assert code(response) == CREATED, response.text
    assert response.json()["target_period_id"] == str(second)
    [row] = adjustments(url, tutor.id)
    assert (row.kind, row.origin_line_id, row.class_session_id) == (
        "manual",
        line.id,
        None,
    )
    assert (row.created_by_user_id, row.reason) == (owner.user.id, REASON)
    assert frozen_state(url, first) == before
    assert run_job(url) == PostAdjustmentsRunResult()
    assert code(prepare(client, finance, second)) == OK
    # 90 minutes at 150.00 an hour, all told.
    assert line.amount + period_total(url, second) == Decimal("225.00")


# --- A4, D4, B3: on unresolved work in a finalized period -----


def test_d4_late_work_with_no_rate_is_paid_by_hand_and_settled(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made, first, second, body = late_unpriced(url, client, owner, finance, tutor)
    session_id = str(made.id)
    before = frozen_state(url, first)
    assert items(url, tutor) == [(session_id, "no_rate")]
    assert blocking(url, second) == [session_id]
    assert code(prepare(client, finance, second)) == UNRESOLVED

    response = manual(client, owner, tutor.id, "110.00", class_session_id=made.id)

    assert code(response) == CREATED, response.text
    posted = response.json()
    assert set(posted) == POSTED_KEYS
    assert (posted["kind"], posted["tutor_user_id"], posted["target_period_id"]) == (
        "manual",
        str(tutor.id),
        str(second),
    )
    assert "110.0" not in response.text and REASON not in response.text
    [row] = adjustments(url, tutor.id)
    assert str(row.id) == posted["id"]
    assert (str(row.class_session_id), row.origin_line_id) == (session_id, None)
    assert row.amount == Decimal("110.00")
    assert items(url, tutor) == []
    assert blocking(url, second) == []

    # B3: a rate that now prices the date, and a later revision: the job
    # posts nothing for settled work.
    add_rate(url, tutor.id)
    assert run_job(url) == PostAdjustmentsRunResult()
    correction = correct_closeout(
        client, owner.cookie, made.id, version_of(body), actual_minutes=90
    )
    assert code_of(correction) == OK, correction.text
    assert run_job(url) == PostAdjustmentsRunResult()
    assert items(url, tutor) == []
    assert len(adjustments(url, tutor.id)) == 1

    # The next period prepares and finalizes; the finalized one never moved.
    locked(client, url, finance, owner, second)
    assert row_of(url, second).total_amount == Decimal("110.00")
    assert frozen_state(url, first) == before
    shown = [
        line
        for line in read(client, finance, second).json()["lines"]
        if line["kind"] == "adjustment"
    ]
    assert [(line["adjustment_kind"], line["adjustment_reason"]) for line in shown] == [
        ("manual", REASON)
    ]


def test_a4_work_that_isnt_an_item_is_refused(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,))
    other_made, _, _, _ = late_unpriced(url, client, owner, finance, other)
    paid_made, _, _ = paid_week(url, client, owner, finance, tutor)

    for session_id in (paid_made.id, other_made.id, uuid.uuid4()):
        response = manual(client, owner, tutor.id, class_session_id=session_id)
        assert code(response) == NOT_UNRESOLVED, (session_id, response.text)
    assert adjustments(url, tutor.id) == []


def test_a4_settled_work_is_refused_the_second_time(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made, _, _, _ = late_unpriced(url, client, owner, finance, tutor)
    assert code(manual(client, owner, tutor.id, class_session_id=made.id)) == CREATED

    again = manual(client, finance, tutor.id, class_session_id=made.id)

    assert code(again) == SETTLED
    assert len(adjustments(url, tutor.id)) == 1


def test_a4_work_before_the_first_period_is_paid_by_an_item(
    module_seeded_url, client, owner, tutor
):
    """E2: a one-time item pays it (#109 E4), so a manual one is refused."""
    url = module_seeded_url
    add_terms(url, tutor.id, "11-09")
    add_period_row(url, tutor.id, "11-09", "11-15")
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)
    assert items(url, tutor) == [(str(made.id), "before_first_period")]

    response = manual(client, owner, tutor.id, class_session_id=made.id)

    assert code(response) == BEFORE_FIRST
    assert adjustments(url, tutor.id) == []


def test_a4_the_first_payment_index_answers_settled(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    """The checks pass, then a first payment commits before the write (a row by
    SQL, into the finalized week no lock of the request holds): the index
    refuses the second, and nothing is written."""
    url = module_seeded_url
    made, first, _, _ = late_unpriced(url, client, owner, finance, tutor)
    real = payroll_manual_adjustments.lock_target

    async def first_payment_meanwhile(*args: Any, **kwargs: Any) -> Any:
        await run_sql_async(
            url,
            "INSERT INTO payroll_adjustments (kind, tutor_user_id, target_period_id,"
            " amount, reason, class_session_id, created_by_user_id) VALUES"
            " ('manual', :t, :p, 1, 'Otro pago', :s, :o)",
            {"t": tutor.id, "p": first, "s": made.id, "o": owner.user.id},
        )
        return await real(*args, **kwargs)

    monkeypatch.setattr(
        payroll_manual_adjustments, "lock_target", first_payment_meanwhile
    )

    response = manual(client, owner, tutor.id, class_session_id=made.id)

    assert code(response) == SETTLED, response.text
    assert [row.reason for row in adjustments(url, tutor.id)] == ["Otro pago"]
    assert stored_keys(url, owner.user.id) == []


# --- B1: settled work gets no line -----


def test_b1_settled_work_in_an_open_period_lands_there_and_gets_no_line(
    module_seeded_url, client, owner, finance, tutor
):
    """The edge case: the item's own period is the earliest open one."""
    url = module_seeded_url
    first, _ = unpriced(url, tutor)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user, minutes=60)
    assert code(prepare(client, finance, first)) == UNRESOLVED

    response = manual(client, owner, tutor.id, "100.00", class_session_id=made.id)

    assert code(response) == CREATED, response.text
    assert response.json()["target_period_id"] == str(first)
    # A rate added later for that day: no line for the session.
    add_rate(url, tutor.id)
    assert code(recalculate(client, finance, first)) == OK
    assert [line.kind for line in lines(url, first)] == ["adjustment"]
    locked(client, url, finance, owner, first)
    assert row_of(url, first).total_amount == Decimal("100.00")
    assert run_job(url) == PostAdjustmentsRunResult()


def test_b1_settling_work_of_a_prepared_period_lets_it_finalize_with_no_drift(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first, second = unpriced(url, tutor)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    assert code(prepare(client, finance, first)) == OK
    approved_lesson(client, made, owner.user, minutes=60)
    from tests.integration.payroll_workflow_support import finalize

    assert code(finalize(client, url, owner, first)) == UNRESOLVED

    response = manual(client, finance, tutor.id, "100.00", class_session_id=made.id)

    assert code(response) == CREATED, response.text
    # The prepared period is no target.
    assert response.json()["target_period_id"] == str(second)
    add_rate(url, tutor.id)  # would draft a line, so drift, if it weren't settled
    finalized = finalize(client, url, owner, first)
    assert code(finalized) == OK, finalized.text
    assert lines(url, first) == []
    assert row_of(url, first).total_amount == Decimal("0.00")
    assert run_job(url) == PostAdjustmentsRunResult()


# --- D6: a late stop inside a finalized salaried week (pm-108) -----


def test_d6_a_late_stop_is_recovered_by_hand_on_the_salary_line(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")  # no terms before the first period (#106 E4)
    first = paid_tutor(url, tutor.id, rate=None, salary="3000.00")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    locked(client, url, finance, owner, first)
    [salary] = lines(url, first)
    assert salary.kind == "salary"
    before = frozen_state(url, first)

    stop = post_as(
        client,
        owner.cookie,
        f"/api/v1/payroll/tutors/{tutor.id}/pay-terms",
        {"effective_from": "2026-11-06", "pay_frequency": None, "salary_amount": None},
    )
    assert code_of(stop) == CREATED, stop.text
    assert run_job(url) == PostAdjustmentsRunResult()
    assert adjustments(url, tutor.id) == []

    response = manual(client, owner, tutor.id, "-1285.71", origin_line_id=salary.id)

    assert code(response) == CREATED, response.text
    assert response.json()["target_period_id"] == str(second)
    assert frozen_state(url, first) == before
    assert period_total(url, second) == Decimal("-1285.71")


# --- D7, E5 of the grooming: a leaver's stranded item -----


def test_d7_a_stranded_item_is_deleted_then_paid_once_by_hand(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    add_terms(url, tutor.id, "11-09", frequency=None)
    locked(client, url, finance, owner, first)
    item = add_item(url, tutor.id, add_pay_type(url), "300.00", on="11-12")
    path = f"/api/v1/payroll/tutors/{tutor.id}/pay-items/{item}"

    assert delete(client, owner.cookie, path).status_code == 204
    response = manual(client, owner, tutor.id, "300.00", reason="Bono pendiente")

    assert code(response) == CREATED, response.text
    opened = uuid.UUID(response.json()["target_period_id"])
    assert (str(row_of(url, opened).starts_on), status_of(url, opened)) == (
        "2026-11-09",
        "open",
    )
    assert code(recalculate(client, finance, opened)) == OK
    assert [line.kind for line in lines(url, opened)] == ["adjustment"]
    locked(client, url, finance, owner, opened)
    assert row_of(url, opened).total_amount == Decimal("300.00")


# --- E1, E2, E4, E5 -----


def test_e1_a_delegated_administrator_gets_403_on_both_routes(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    delegated = staff(url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"])

    assert code(manual(client, delegated, tutor.id)) == DENIED
    from tests.integration.payroll_manual_support import carry

    assert code(carry(client, delegated, period)) == DENIED
    assert stored_keys(url, delegated.user.id) == []


def test_e2_a_reauthentication_older_than_five_minutes_is_refused(
    module_seeded_url, client, tutor, frozen
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    fresh = staff(url, client, (FINANCE_STAFF,))

    frozen.set(NOW + timedelta(minutes=4, seconds=59))
    assert code(manual(client, fresh, tutor.id)) == CREATED
    frozen.set(NOW + timedelta(minutes=5, seconds=1))
    assert code(manual(client, fresh, tutor.id)) == REAUTH

    assert len(adjustments(url, tutor.id)) == 1
    assert len(stored_keys(url, fresh.user.id)) == 1


def test_e4_the_key_store_holds_no_amount_and_no_reason(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    reason = "Bono secreto acordado"

    response = manual(client, owner, tutor.id, "777.77", reason=reason)

    assert code(response) == CREATED
    [stored] = stored_keys(url, owner.user.id)
    assert set(stored.response_body) == POSTED_KEYS
    assert "777.77" not in json.dumps(stored.response_body)
    assert reason not in json.dumps(stored.response_body)


def test_e5_own_pay_is_refused_but_to_an_owner(module_seeded_url, client, owner):
    url = module_seeded_url
    teller = staff(url, client, (FINANCE_STAFF, TUTOR))
    boss = staff(url, client, (OWNER, TUTOR))
    paid_tutor(url, teller.user.id)
    period = paid_tutor(url, boss.user.id)

    assert code(manual(client, teller, teller.user.id)) == OWN_PAY
    assert stored_keys(url, teller.user.id) == []
    response = manual(client, boss, boss.user.id)
    assert code(response) == CREATED, response.text
    assert response.json()["target_period_id"] == str(period)


def test_a5_the_audit_masks_and_no_log_line_carries_the_amount_or_reason(
    module_seeded_url, client, owner, tutor, caplog
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    reason = "Motivo privado 4242"

    with caplog.at_level(logging.DEBUG):
        response = manual(client, owner, tutor.id, "98.76", reason=reason)

    assert code(response) == CREATED
    [row] = adjustments(url, tutor.id)
    audits = audit_rows(url, [row.id])
    assert [a.action for a in audits] == ["create"]
    changes = audits[0].changes
    for masked in ("amount", "rate", "reason"):
        assert changes[masked]["new"] == "[masked]"
    assert audits[0].actor_user_id == owner.user.id
    for text in (json.dumps(changes, default=str), caplog.text):
        assert "98.76" not in text
        assert reason not in text
    [line] = [line for line in lines(url, row.target_period_id)]
    assert line.payroll_adjustment_id == row.id
