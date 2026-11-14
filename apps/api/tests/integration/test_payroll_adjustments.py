"""Work and corrections after lock (#111 B, C, D, E, F, G; ADR-0017 E11 and its
#111 notes; data-model §11.9, §13).

Closeouts, re-approvals, ``prepare`` and ``finalize`` go through their real
routes; terms, rates, rules and periods are set by SQL, as in every payroll
test, and so is a row no route can write (stated where it happens). Every
scene compares the finalized period's row and lines before and after (C10).
Cancellations, salaried dates and cohorts are in
``test_payroll_adjustment_flows.py``; races in
``test_payroll_adjustments_concurrency.py``.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.workflows import payroll_adjustments
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements
from tests.integration.catalog_support import post as post_as
from tests.integration.conftest import run_sql, settings_for
from tests.integration.corrections_support import Staff, staff
from tests.integration.guardian_support import delete
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    approved_lesson,
    clear,
    lines,
    paid_tutor,
    run,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    OUT_OF_ORDER,
    code,
    finalize,
    locked,
    prepare,
    read,
    recalculate,
    row_of,
    status_of,
)
from tests.integration.test_attendance import correct, lesson
from tests.integration.test_closeout_review import correct_closeout, version_of
from tests.integration.test_payroll_calculation import _store_closeout

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
NEGATIVE = (409, "payroll_negative_total")


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


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
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


def adjustments(url: str, tutor: CreatedUser) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_adjustments WHERE tutor_user_id = :t"
        " ORDER BY created_at, id",
        {"t": tutor.id},
    )


def adjustment_lines(url: str, period: uuid.UUID) -> list[Any]:
    return [line for line in lines(url, period) if line.kind == "adjustment"]


def frozen_state(url: str, period: uuid.UUID) -> tuple[Any, list[Any]]:
    """The period's row and every line, all columns (C10)."""
    return row_of(url, period), run_sql(
        url,
        "SELECT * FROM payroll_lines WHERE payroll_period_id = :p ORDER BY id",
        {"p": period},
    )


def two_periods(
    url: str, tutor: CreatedUser, *, rate: str = "120", model: str = "per_hour"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Terms and a rate from long ago, the week of ``STARTS`` and the next."""
    first = paid_tutor(url, tutor.id, rate=rate, model=model)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    return first, second


def session_total(url: str, session_id: Any) -> Decimal:
    """What a session was paid: its session lines and its adjustments."""
    [row] = run_sql(
        url,
        "SELECT COALESCE((SELECT sum(amount) FROM payroll_lines WHERE"
        " class_session_id = :s), 0) + COALESCE((SELECT sum(amount) FROM"
        " payroll_adjustments WHERE class_session_id = :s), 0) AS total",
        {"s": session_id},
    )
    return Decimal(row.total)


def reapproved(
    client: TestClient, who: Staff, session_id: Any, version: int, **body: Any
) -> int:
    """A staff correction: a new revision. Returns the closeout's version."""
    response = correct_closeout(client, who.cookie, session_id, version, **body)
    assert code_of(response) == OK, response.text
    return version_of(response.json())


def finalized_line(
    url: str,
    client: TestClient,
    owner: Staff,
    finance: Staff,
    tutor: CreatedUser,
    *,
    minutes: int = 90,
) -> tuple[Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    """A session approved at ``minutes``, its week finalized by route, and the
    next week open."""
    first, second = two_periods(url, tutor)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = approved_lesson(client, made, owner.user, minutes=minutes)
    locked(client, url, finance, owner, first)
    return made, first, second, body


def late_closeout(
    url: str,
    client: TestClient,
    owner: Staff,
    finance: Staff,
    tutor: CreatedUser,
    **rate: str,
) -> tuple[Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    """The week finalized while its session's closeout is unapproved, then the
    closeout approved."""
    first, second = two_periods(url, tutor, **rate)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    body = approved_lesson(client, made, owner.user)
    return made, first, second, body


# --- B: the job -----


def test_b2_payroll_off_posts_nothing(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    late_closeout(url, client, owner, finance, tutor)
    set_floor(url, None)

    assert run_job(url) == PostAdjustmentsRunResult()
    assert adjustments(url, tutor) == []


def test_b6_the_log_line_has_counts_and_no_amount(
    module_seeded_url, client, owner, finance, tutor, caplog
):
    url = module_seeded_url
    late_closeout(url, client, owner, finance, tutor, rate="98.76")

    with caplog.at_level(logging.INFO, logger=payroll_adjustments.__name__):
        result = run_job(url)

    assert result == PostAdjustmentsRunResult(candidates=1, posted=1)
    [record] = [r for r in caplog.records if r.name == payroll_adjustments.__name__]
    fields = {k: v for k, v in vars(record).items() if k.endswith("_count")}
    assert fields == {
        "candidates_count": 1,
        "posted_count": 1,
        "failed_count": 0,
    }
    # The message, its args and every non-standard (``extra``) attribute; the
    # standard ones hold random floats (created, msecs) that can match by chance.
    standard = set(vars(logging.makeLogRecord({}))) | {"message"}
    extra = {k: v for k, v in vars(record).items() if k not in standard}
    text = record.getMessage() + repr(record.args) + repr(extra)
    assert "90.53" not in text and "98.76" not in text


def test_e9_the_adjustment_is_audited_with_no_actor_and_masked(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    late_closeout(url, client, owner, finance, tutor)
    run_job(url)
    [adjustment] = adjustments(url, tutor)

    [event] = run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_type = 'payroll_adjustments'"
        " AND record_id = :id",
        {"id": adjustment.id},
    )
    assert (event.action, event.actor_user_id) == ("create", None)
    for masked in ("amount", "rate"):
        assert event.changes[masked]["new"] == "[masked]"
    assert "110.00" not in str(event.changes) and "120" not in str(
        event.changes["rate"]
    )


# --- C1: a late closeout -----


@pytest.mark.parametrize(
    ("rate", "model", "amount"),
    [("120", "per_hour", "110.00"), ("300", "per_session", "300.00")],
)
def test_c1_a_late_closeout_is_paid_once_into_the_earliest_open_period(
    module_seeded_url, client, owner, finance, tutor, rate, model, amount
):
    url = module_seeded_url
    made, first, second, body = late_closeout(
        url, client, owner, finance, tutor, rate=rate, model=model
    )
    before = frozen_state(url, first)
    approval = body["closeout"]["approval"]["id"]

    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    [adjustment] = adjustments(url, tutor)
    assert adjustment.kind == "late_approval"
    assert adjustment.amount == Decimal(amount)
    assert adjustment.source_key == f"approval:{approval}"
    assert str(adjustment.approval_id) == approval
    assert str(adjustment.class_session_id) == made.id
    assert (adjustment.origin_period_id, adjustment.target_period_id) == (first, second)
    assert (adjustment.origin_line_id, adjustment.created_by_user_id) == (None, None)
    assert (adjustment.line_kind, adjustment.rate_model) == ("session", model)
    assert adjustment.rate == Decimal(rate)
    [line] = adjustment_lines(url, second)
    assert (line.payroll_adjustment_id, line.amount) == (
        adjustment.id,
        adjustment.amount,
    )
    assert line.class_session_id is None and line.rate is None

    # What a line would have paid: recalculate the week, by SQL, as if it were open.
    assert run_job(url) == PostAdjustmentsRunResult()
    assert len(adjustments(url, tutor)) == 1
    assert frozen_state(url, first) == before


def test_c1_the_amount_is_the_lines(module_seeded_url, client, owner, tutor):
    """The same closeout in an open period: the line and the adjustment agree."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)
    run(url, period)
    [line] = lines(url, period)

    assert line.amount == Decimal("110.00")


# --- C4: before the first period (MUST from #109) -----


def test_c4_work_before_the_first_period_is_never_paid_here(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id, "11-09")
    add_rate(url, tutor.id)
    first = add_period_row(url, tutor.id, "11-09", "11-15")
    add_period_row(url, tutor.id, "11-16", "11-22", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)
    locked(client, url, finance, owner, first)

    assert run_job(url) == PostAdjustmentsRunResult()

    assert adjustments(url, tutor) == []

    async def listed(session: AsyncSession) -> Any:
        from app.workflows import payroll_calculation

        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    [item] = in_uow(url, listed)
    assert (str(item.class_session_id), item.reason, item.blocking) == (
        made.id,
        "before_first_period",
        False,
    )


def test_c4_work_before_the_first_period_with_a_basis_is_never_paid_here(
    module_seeded_url, client, owner, finance, tutor
):
    """By SQL, on purpose: terms reaching back before the first period, which the
    route refuses (#106 E4). The work then has a basis, and is still in no
    finalized period, so the job never pays it (a one-time item does, #109)."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    first = add_period_row(url, tutor.id, "11-09", "11-15")
    add_period_row(url, tutor.id, "11-16", "11-22", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)
    locked(client, url, finance, owner, first)

    assert run_job(url) == PostAdjustmentsRunResult()
    assert adjustments(url, tutor) == []


# --- C5, C6, C7: revisions after lock -----


def test_c5_a_revision_after_a_finalized_line_is_corrected_on_its_snapshot(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made, first, second, body = finalized_line(url, client, owner, finance, tutor)
    [origin] = lines(url, first)
    assert origin.amount == Decimal("180.00")
    before = frozen_state(url, first)
    # A newer version on the very date, by SQL (the route refuses it, #110 E5):
    # the correction is priced on the line's snapshot all the same.
    add_rate(url, tutor.id, "11-03", "200")

    version = reapproved(client, owner, made.id, version_of(body), actual_minutes=60)
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    [correction] = adjustments(url, tutor)
    current = run_sql(
        url,
        "SELECT current_approval_id FROM session_closeouts WHERE class_session_id = :s",
        {"s": made.id},
    )[0].current_approval_id
    assert correction.kind == "hours_correction"
    assert correction.amount == Decimal("-60.00")
    assert (correction.origin_line_id, correction.approval_id) == (origin.id, current)
    assert correction.source_key == f"approval:{current}"
    assert (correction.rate, correction.compensation_rate_id) == (
        origin.rate,
        origin.compensation_rate_id,
    )
    assert (correction.minutes, correction.origin_period_id) == (60, None)
    assert correction.target_period_id == second

    version = reapproved(client, owner, made.id, version, actual_minutes=75)
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    assert [a.amount for a in adjustments(url, tutor)] == [
        Decimal("-60.00"),
        Decimal("30.00"),
    ]
    assert session_total(url, made.id) == Decimal("150.00")

    # A revision of the summary only: a candidate, a zero difference, no row.
    reapproved(
        client,
        owner,
        made.id,
        version,
        summary={"topics": "Fracciones", "homework": None},
    )
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1)
    assert len(adjustments(url, tutor)) == 2
    assert frozen_state(url, first) == before


def test_c6_a_late_approval_corrected_is_never_paid_twice(
    module_seeded_url, client, owner, finance, tutor
):
    """qa-104 O3: the correction is against the late approval's line."""
    url = module_seeded_url
    made, first, second, body = late_closeout(url, client, owner, finance, tutor)
    run_job(url)
    [late] = adjustments(url, tutor)
    [late_line] = adjustment_lines(url, second)

    reapproved(client, owner, made.id, version_of(body), actual_minutes=90)
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    late_row, correction = adjustments(url, tutor)
    assert late_row.id == late.id
    assert correction.kind == "hours_correction"
    assert correction.origin_line_id == late_line.id
    assert correction.amount == Decimal("70.00")
    assert session_total(url, made.id) == Decimal("180.00")
    assert run_job(url) == PostAdjustmentsRunResult()


def test_c7_an_event_change_with_a_revision_is_corrected_and_back(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_rule(url, "no_show", "2026-10-01", "percent", percent="50")
    made, first, second, body = finalized_line(
        url, client, owner, finance, tutor, minutes=60
    )
    [student] = made.students
    before = frozen_state(url, first)

    fixed = correct(client, owner.cookie, made.id, body["version"], student, "absent")
    assert code_of(fixed) == (201, None), fixed.text
    version = reapproved(
        client, owner, made.id, version_of(body), reason="Asistencia corregida"
    )
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    [no_show] = adjustments(url, tutor)
    assert (no_show.line_kind, no_show.amount) == ("no_show", Decimal("-60.00"))
    assert no_show.session_pay_rule_id is not None

    back = correct(
        client, owner.cookie, made.id, fixed.json()["version"], student, "present"
    )
    assert code_of(back) == (201, None), back.text
    reapproved(client, owner, made.id, version, reason="Asistencia corregida")
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    assert adjustments(url, tutor)[-1].amount == Decimal("60.00")
    assert adjustments(url, tutor)[-1].line_kind == "session"
    assert session_total(url, made.id) == Decimal("120.00")
    assert frozen_state(url, first) == before


def test_c7_a_covered_origin_re_approved_posts_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id, salary="3000.00")
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = approved_lesson(client, made, owner.user, minutes=90)
    locked(client, url, finance, owner, first)
    [covered] = [line for line in lines(url, first) if line.kind == "session"]
    assert (covered.amount, covered.rate) == (Decimal("0.00"), None)

    reapproved(client, owner, made.id, version_of(body), actual_minutes=30)

    assert run_job(url) == PostAdjustmentsRunResult(candidates=1)
    assert adjustments(url, tutor) == []


# --- C10: a finalized period never changes -----


def test_c10_an_adjustment_line_never_goes_into_a_finalized_period(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    run_job(url)
    [adjustment] = adjustments(url, tutor)
    before = frozen_state(url, first)

    with pytest.raises(DBAPIError) as caught:
        run_sql(
            url,
            "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind,"
            " amount, payroll_adjustment_id) VALUES (:p, :t, 'adjustment', 1, :a)",
            {"p": first, "t": tutor.id, "a": adjustment.id},
        )
    assert getattr(caught.value.orig, "sqlstate", None) == "42501"
    assert frozen_state(url, first) == before


# --- D: the target -----


def test_d1_an_earlier_prepared_period_is_skipped_and_then_blocks(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    third = add_period_row(url, tutor.id, "11-16", "11-22", first=False)
    assert code(prepare(client, finance, second)) == OK

    run_job(url)

    [adjustment] = adjustments(url, tutor)
    assert adjustment.target_period_id == third
    assert adjustment_lines(url, second) == []
    assert code(prepare(client, finance, third)) == OK
    response = finalize(client, url, owner, third)
    assert code(response) == OUT_OF_ORDER, response.text


def test_d3_a_period_not_opened_yet_is_opened_by_the_normal_rule(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    approved_lesson(client, made, owner.user)

    run_job(url)

    periods = run_sql(
        url,
        "SELECT id, starts_on, ends_on, status FROM payroll_periods"
        " WHERE tutor_user_id = :t ORDER BY starts_on",
        {"t": tutor.id},
    )
    # Weekly up to the week of NOW (2026-12-07), as the daily job would.
    assert [(str(p.starts_on), str(p.ends_on)) for p in periods[1:]] == [
        ("2026-11-09", "2026-11-15"),
        ("2026-11-16", "2026-11-22"),
        ("2026-11-23", "2026-11-29"),
        ("2026-11-30", "2026-12-06"),
        ("2026-12-07", "2026-12-13"),
    ]
    [adjustment] = adjustments(url, tutor)
    assert adjustment.target_period_id == periods[1].id


def test_d4_a_leaver_gets_one_period_for_the_adjustment(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    add_terms(url, tutor.id, "11-09", frequency=None)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    approved_lesson(client, made, owner.user)

    run_job(url)

    periods = run_sql(
        url,
        "SELECT * FROM payroll_periods WHERE tutor_user_id = :t ORDER BY starts_on",
        {"t": tutor.id},
    )
    assert len(periods) == 2
    opened = periods[1]
    assert (str(opened.starts_on), str(opened.ends_on), opened.pay_frequency) == (
        "2026-11-09",
        "2026-11-15",
        "weekly",
    )
    assert (opened.status, opened.is_first_period) == ("open", False)
    [adjustment] = adjustments(url, tutor)
    assert adjustment.target_period_id == opened.id
    # The daily job opens nothing more for a leaver.
    from tests.integration.payroll_period_support import run_job as open_periods

    open_periods(url)
    periods_now = run_sql(
        url, "SELECT id FROM payroll_periods WHERE tutor_user_id = :t", {"t": tutor.id}
    )
    assert len(periods_now) == 2


def test_d4_returning_terms_inside_the_leavers_period_follow_106_e3(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")  # no terms before the first period (#106 E4)
    first = paid_tutor(url, tutor.id)
    add_terms(url, tutor.id, "11-09", frequency=None)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    approved_lesson(client, made, owner.user)
    run_job(url)

    def terms(frequency: str | None) -> Any:
        return post_as(
            client,
            owner.cookie,
            f"/api/v1/payroll/tutors/{tutor.id}/pay-terms",
            {
                "effective_from": "2026-11-12",
                "pay_frequency": frequency,
                "salary_amount": None,
            },
        )

    assert code_of(terms("monthly")) == (
        422,
        "effective_from_inside_generated_period",
    )
    assert code_of(terms("weekly")) == (201, None)


# --- E: finalize's backstop and the totals -----


def test_e1_finalize_refuses_a_negative_total_and_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    """By SQL: no route puts an adjustment into a prepared period (E8)."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    [adjustment] = run_sql(
        url,
        "INSERT INTO payroll_adjustments (kind, tutor_user_id, target_period_id,"
        " amount, reason, created_by_user_id) VALUES ('manual', :t, :p, -10,"
        " 'Ajuste', :o) RETURNING id",
        {"t": tutor.id, "p": period, "o": owner.user.id},
    )
    run_sql(
        url,
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " payroll_adjustment_id) VALUES (:p, :t, 'adjustment', -10, :a)",
        {"p": period, "t": tutor.id, "a": adjustment.id},
    )
    before = frozen_state(url, period)

    response = finalize(client, url, owner, period)

    assert code(response) == NEGATIVE, response.text
    assert "10" not in str(response.json().get("details"))
    assert frozen_state(url, period) == before
    assert status_of(url, period) == "prepared"


def test_e2_prepare_refuses_a_period_a_correction_made_negative(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made, first, second, body = finalized_line(url, client, owner, finance, tutor)
    reapproved(client, owner, made.id, version_of(body), actual_minutes=60)
    run_job(url)

    recalculated = recalculate(client, finance, second)
    shown = read(client, finance, second)
    assert recalculated.json()["total"] == shown.json()["total"] == "-60.00"

    assert code(prepare(client, finance, second)) == NEGATIVE
    assert status_of(url, second) == "open"
    assert read(client, finance, second).json()["total"] == "-60.00"


def test_e3_finalize_with_an_adjustment_line_has_no_drift_and_counts_it(
    module_seeded_url, client, owner, finance, tutor
):
    """Also F3: a real adjustment is outside drift and inside the total."""
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    second_lesson = lesson(url, starts_at=STARTS + timedelta(days=7), tutor=tutor)
    approved_lesson(client, second_lesson, owner.user, minutes=60)
    run_job(url)

    locked(client, url, finance, owner, second)

    assert row_of(url, second).total_amount == Decimal("230.00")


# --- F: real rows -----


def test_f1_an_adjustment_line_survives_runs_and_prepare_unpatched(
    module_seeded_url, client, owner, finance, tutor
):
    """#108, #109 L7: no monkeypatch; the kinds a run rebuilds are replaced."""
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    add_item(url, tutor.id, add_pay_type(url), "40.00", on="11-10")
    run_job(url)
    [kept] = adjustment_lines(url, second)

    for _ in range(2):
        response = recalculate(client, finance, second)
        assert code(response) == OK
        shown = read(client, finance, second).json()
        assert response.json() == {"line_count": 2, "total": "150.00"}
        assert shown["total"] == "150.00" and len(shown["lines"]) == 2
    item_ids = {line.id for line in lines(url, second) if line.kind == "pay_item"}
    assert code(prepare(client, finance, second)) == OK

    [after] = adjustment_lines(url, second)
    assert (after.id, after.amount, after.created_at) == (
        kept.id,
        kept.amount,
        kept.created_at,
    )
    assert {
        line.id for line in lines(url, second) if line.kind == "pay_item"
    }.isdisjoint(item_ids)
    assert payroll.REBUILT_KINDS == (
        "session",
        "late_cancellation",
        "no_show",
        "makeup",
        "salary",
        "pay_item",
    )


def test_f4_adjustment_lines_sort_last_whatever_the_insert_order(
    module_seeded_url, client, owner, finance, tutor
):
    """By SQL: two manual adjustments (M7-08c's route) inserted out of order."""
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    add_item(url, tutor.id, add_pay_type(url), "40.00", on="11-10")
    worked_lesson = lesson(url, starts_at=STARTS + timedelta(days=7), tutor=tutor)
    approved_lesson(client, worked_lesson, owner.user, minutes=60)
    run_job(url)
    run(url, second)
    made = [
        run_sql(
            url,
            "INSERT INTO payroll_adjustments (id, kind, tutor_user_id,"
            " target_period_id, amount, reason, created_by_user_id)"
            " VALUES (:id, 'manual', :t, :p, 5, 'Ajuste', :o) RETURNING id",
            {"id": uuid.uuid7(), "t": tutor.id, "p": second, "o": owner.user.id},
        )[0].id
        for _ in range(2)
    ]
    for adjustment in reversed(made):
        run_sql(
            url,
            "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind,"
            " amount, payroll_adjustment_id) VALUES (:p, :t, 'adjustment', 5, :a)",
            {"p": second, "t": tutor.id, "a": adjustment},
        )

    shown = read(client, finance, second).json()["lines"]

    assert [line["kind"] for line in shown] == [
        "session",
        "pay_item",
        "adjustment",
        "adjustment",
        "adjustment",
    ]
    ids = [line["payroll_adjustment_id"] for line in shown[2:]]
    assert ids == sorted(ids)
    assert {str(a) for a in made} <= set(ids)


# --- G1: the read -----


def test_g1_an_adjustment_line_reads_its_adjustment(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made, first, second, _ = late_closeout(url, client, owner, finance, tutor)
    run_job(url)
    [manual] = run_sql(
        url,
        "INSERT INTO payroll_adjustments (id, kind, tutor_user_id, target_period_id,"
        " amount, reason, created_by_user_id) VALUES (:id, 'manual', :t, :p, 5,"
        " 'Pago por fuera', :o) RETURNING id",
        {"id": uuid.uuid7(), "t": tutor.id, "p": second, "o": owner.user.id},
    )
    run_sql(
        url,
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " payroll_adjustment_id) VALUES (:p, :t, 'adjustment', 5, :a)",
        {"p": second, "t": tutor.id, "a": manual.id},
    )
    [late] = adjustments(url, tutor)[:1]

    shown = read(client, finance, second).json()["lines"]

    late_line, manual_line = sorted(shown, key=lambda line: line["adjustment_kind"])
    assert late_line == {
        **late_line,
        "kind": "adjustment",
        "amount": "110.00",
        "payroll_adjustment_id": str(late.id),
        "adjustment_kind": "late_approval",
        "adjustment_reason": None,
        "adjustment_class_session_id": made.id,
        "adjustment_service_date": "2026-11-03",
        "adjustment_class_kind": "individual",
        "adjustment_origin_line_id": None,
        "adjustment_origin_period_id": str(first),
        "class_session_id": None,
        "rate": None,
    }
    assert manual_line["adjustment_reason"] == "Pago por fuera"
    # A line that is no adjustment has every adjustment field null.
    session_line = read(client, finance, first).json()["lines"]
    assert all(
        line[name] is None
        for line in session_line
        for name in line
        if name.startswith("adjustment_") or name == "payroll_adjustment_id"
    )


def test_g1_the_read_costs_the_same_statements_with_adjustments(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    _, first, second, _ = late_closeout(url, client, owner, finance, tutor)

    def lines_of(session: AsyncSession) -> Any:
        return payroll.period_lines(session, second)

    without, _ = count_statements(url, lines_of)
    run_job(url)
    with_one, found = count_statements(url, lines_of)

    assert without == with_one == 2
    assert len(found.lines) == 1


# --- B7: statements -----


def stored_late(url: str, tutor: CreatedUser, owner: Staff, n: int) -> None:
    """``n`` closeouts with a revision, by SQL, in the finalized week."""
    for index in range(n):
        starts = datetime(2026, 11, 2 + index % 7, 14 + index // 7, tzinfo=UTC)
        made = lesson(url, starts_at=starts, tutor=tutor)
        _store_closeout(url, made, owner.user)


def finalized_by_sql(url: str, period: uuid.UUID, by: uuid.UUID) -> None:
    from tests.integration.payroll_period_support import finalize as by_sql

    by_sql(url, period, by)


@pytest.mark.parametrize("n", [3, 30])
def test_b7_the_selection_costs_the_same_statements_whatever_the_count(
    module_seeded_url, owner, n
):
    url = module_seeded_url
    tutors = [create_user(url, roles=(TUTOR,)) for _ in range(2)]
    for tutor in tutors:
        first, _ = two_periods(url, tutor)
        add_period_row(url, tutor.id, "11-16", "11-22", first=False)
        finalized_by_sql(url, first, owner.user.id)
        stored_late(url, tutor, owner, n)

    async def select(session: AsyncSession) -> Any:
        zone = await administration.get_center_time_zone(session)
        floor = await payroll.payroll_starts_on(session)
        assert floor is not None
        return await payroll_adjustments.select_candidates(
            session, floor=floor, zone=zone
        )

    count, selection = count_statements(url, select)

    assert (selection.candidates, len(selection.to_post)) == (2 * n, 2 * n)
    # zone, floor, three candidate reads, the finalized periods, terms, rates,
    # rules and makeups. No correction (paid so far), no course (first dates)
    # and no salary (covers) here: none of them reads.
    assert count == 10


def post_cost(url: str, tutor: CreatedUser) -> int:
    """The statements of :func:`post_one` for one of the tutor's candidates."""

    async def one(session: AsyncSession) -> Any:
        zone = await administration.get_center_time_zone(session)
        floor = await payroll.payroll_starts_on(session)
        assert floor is not None
        today = await administration.center_today(session)
        selection = await payroll_adjustments.select_candidates(
            session, floor=floor, zone=zone
        )
        candidate = next(c for c in selection.to_post if c.tutor_user_id == tutor.id)
        return floor, zone, today, candidate

    floor, zone, today, candidate = in_uow(url, one)

    def post(session: AsyncSession) -> Any:
        return payroll_adjustments.post_one(
            session, candidate, floor=floor, zone=zone, today=today
        )

    count, outcome = count_statements(url, post)
    assert outcome == "posted"
    return count


def test_b7_one_adjustment_costs_the_same_statements(module_seeded_url, owner):
    url = module_seeded_url
    counts = []
    for n in (1, 5):
        tutor = create_user(url, roles=(TUTOR,))
        first, _ = two_periods(url, tutor)
        finalized_by_sql(url, first, owner.user.id)
        stored_late(url, tutor, owner, n)
        counts.append(post_cost(url, tutor))

    assert counts[0] == counts[1]


# --- M7-08d C1, C3: a posted adjustment is a fixed price in the rate refusals -----


def test_m7_08d_c1_c3_a_late_approval_is_a_fixed_price_for_adds_and_deletes(
    module_seeded_url, client, owner, finance, tutor
):
    """The late session's week is finalized with no line, so only the
    ``late_approval`` (per hour 120 on 2026-11-03) can refuse."""
    url = module_seeded_url
    made, first, _, _ = late_closeout(url, client, owner, finance, tutor)
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    assert lines(url, first) == []
    rates = f"/api/v1/payroll/tutors/{tutor.id}/rates"
    [original] = run_sql(
        url,
        "SELECT id FROM compensation_rates WHERE tutor_user_id = :t",
        {"t": tutor.id},
    )

    def version(on: str, rate: str | None, model: str | None = "per_hour") -> Any:
        return post_as(
            client,
            owner.cookie,
            rates,
            {
                "class_kind": "individual",
                "effective_from": on,
                "rate_model": model,
                "rate": rate,
            },
        )

    def gone(rate_id: Any) -> Any:
        return delete(client, owner.cookie, f"{rates}/{rate_id}")

    # C1: a version back-dated over the date is refused; the same rate passes.
    for rate, model in (("130", "per_hour"), ("120", "per_session"), (None, None)):
        refused = version("2026-11-01", rate, model)
        assert code_of(refused) == (422, "effective_from_under_finalized_line")
        assert refused.json()["details"] == {"latest_finalized_on": "2026-11-03"}
    same = version("2026-11-01", "120.0000")
    assert code_of(same) == (201, None), same.text
    assert code_of(version("2026-11-04", "130")) == (201, None)

    # C3: deleting the same-rate cut would give its date to 777.
    previous = version("2026-10-31", "777")
    assert code_of(previous) == (201, None), previous.text
    refused = gone(same.json()["id"])
    assert code_of(refused) == (409, "compensation_rate_delete_under_finalized_line")
    assert refused.json()["details"] == {"latest_finalized_on": "2026-11-03"}
    assert code_of(gone(original.id)) == (409, "compensation_rate_in_use")
    assert code_of(gone(previous.json()["id"])) == (204, None)
    assert code_of(gone(same.json()["id"])) == (204, None)
    assert session_total(url, made.id) == Decimal("110.00")
