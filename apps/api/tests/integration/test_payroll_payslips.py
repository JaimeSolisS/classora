"""Issuing payslips at finalize through the real routes (#113 B1-B8; ADR-0017
E14; ADR-0006 E13; ADR-0015; data-model §11.12, P10).

Finance prepares and the Owner finalizes, oldest first: a period finalized by
SQL (``payroll_period_support.finalize``) issues nothing, so these tests never
use it. Two finalizes on real connections are at the end (B2). The PDF is in
``test_payroll_payslips_pdf.py`` (it needs Pango).
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.db import create_sessionmaker
from app.core.numbering import NumberSeriesExhaustedError
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import payslips as payslips_module
from app.modules.payroll.payslips import parse_snapshot, payslip_html
from app.modules.payroll.service import StoredPayslip
from app.workflows import payroll_workflow
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payslip_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.corrections_support import Staff, staff
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    clear,
    paid_tutor,
)
from tests.integration.payroll_manual_support import carry, manual
from tests.integration.payroll_payment_support import (
    NOW,
    body,
    pay,
    reverse,
    worked_period,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    code,
    finalize,
    locked,
    new_key,
    prepare,
    prepared_at,
    status_of,
    team_at,
)
from tests.integration.test_attendance import principal_of

ZONE = ZoneInfo("America/Mexico_City")
YEAR = NOW.year
SERVER_ERROR = (500, "internal_error")


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
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url, roles=(TUTOR,), display_name="Tutora Núñez Peña"
    )


def stored_of(row: Any) -> StoredPayslip:
    return StoredPayslip(
        id=row.id,
        number=row.number,
        payroll_period_id=row.payroll_period_id,
        issued_at=row.issued_at,
        language=row.language,
        template_version=row.template_version,
        snapshot=row.snapshot,
    )


# --- B1: one payslip, after the D5 writes ---------------------------------------------


def test_b1_a_finalize_with_lines_issues_exactly_one_payslip(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    before = ps.series_value(url, YEAR)

    locked(client, url, finance, owner, period)

    row = ps.payslip_of(url, period)
    assert row is not None
    assert row.number == f"P-{YEAR}-{before + 1:06d}"
    assert ps.series_value(url, YEAR) == before + 1
    assert row.tutor_user_id == tutor.id
    assert row.language == "es"
    assert row.template_version == 1
    assert row.issued_at == NOW
    assert ps.payslips_of_tutor(url, tutor.id) == [row]
    snapshot = parse_snapshot(row.snapshot)
    assert str(snapshot.total) == "110.00"
    [period_row] = run_sql(
        url, "SELECT * FROM payroll_periods WHERE id = :p", {"p": period}
    )
    assert period_row.total_amount == snapshot.total.amount
    assert snapshot.period.id == period
    assert snapshot.tutor.user_id == tutor.id
    assert snapshot.tutor.display_name == "Tutora Núñez Peña"
    # 2026-12-07 18:00 UTC is noon in the center's zone.
    assert str(snapshot.issued_on) == "2026-12-07"
    assert str(snapshot.period.finalized_on) == "2026-12-07"


def test_b1_a_finalized_period_with_no_lines_gets_no_payslip(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    empty = paid_tutor(url, tutor.id)
    before = ps.series_value(url, YEAR)

    locked(client, url, finance, owner, empty)

    assert status_of(url, empty) == "finalized"
    assert ps.payslip_of(url, empty) is None
    assert ps.series_value(url, YEAR) == before


def test_b1_the_number_is_the_last_lock_after_the_inputs(
    module_seeded_url, client, owner, finance, tutor
):
    """The series ``UPDATE`` comes after every read of the inputs, and only the
    payslip and its audit row follow it."""
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    seen: list[str] = []
    review = datetime.fromisoformat(prepared_at(url, period) or "")
    principal = principal_of(owner.user.id, OWNER)

    async def _run() -> None:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, many) -> None:  # type: ignore[no-untyped-def]
            seen.append(" ".join(statement.split()))

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine), owner.user.id) as uow:
                await payroll_workflow.finalize(uow.session, principal, period, review)
        finally:
            await engine.dispose()

    asyncio.run(_run())

    update = next(i for i, s in enumerate(seen) if s.startswith("UPDATE number_series"))
    after = seen[update + 1 :]
    assert [s.split(" (")[0] for s in after] == [
        "INSERT INTO payslips",
        "INSERT INTO audit_events",
    ], after
    reads = [i for i, s in enumerate(seen) if "FROM payroll_lines" in s]
    assert reads and max(reads) < update
    assert any("FROM business_settings" in s for s in seen[:update])
    assert any("FROM user_preferences" in s for s in seen[:update])


# --- B2: gap-free ---------------------------------------------------------------------


def test_b2_a_failure_after_the_number_rolls_back_the_whole_finalize(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    k = ps.series_value(url, YEAR) + 1
    real = payslips_module.payslip_number

    def failing(year: int, value: int) -> str:
        real(year, value)
        raise RuntimeError("injected after next_number")

    monkeypatch.setattr(payslips_module, "payslip_number", failing)
    response = finalize(client, url, owner, period)
    assert code(response) == SERVER_ERROR
    assert status_of(url, period) == "prepared"
    assert ps.payslip_of(url, period) is None
    assert ps.series_value(url, YEAR) == k - 1

    monkeypatch.undo()
    assert code(finalize(client, url, owner, period)) == OK

    assert ps.payslip_of(url, period).number == f"P-{YEAR}-{k:06d}"


def test_b2_a_replay_returns_the_stored_response_and_issues_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    key = new_key()
    first = finalize(client, url, owner, period, key=key)
    assert code(first) == OK
    value = ps.series_value(url, YEAR)

    again = finalize(client, url, owner, period, key=key)

    assert code(again) == OK
    assert again.json() == first.json()
    assert set(first.json()) == {"id", "status", "prepared_at", "finalized_at"}
    assert len(ps.payslips_of_tutor(url, tutor.id)) == 1
    assert ps.series_value(url, YEAR) == value


def test_b2_the_unique_period_is_the_backstop(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    locked(client, url, finance, owner, period)

    async def _again() -> None:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), owner.user.id) as uow:
                facts = await payroll_workflow.payroll.period_row(uow.session, period)
                await payroll_workflow.payroll_earnings.issue_for(uow.session, facts)
        finally:
            await engine.dispose()

    with pytest.raises(Exception, match="uq_payslips_payroll_period_id"):
        asyncio.run(_again())
    assert len(ps.payslips_of_tutor(url, tutor.id)) == 1


# --- B3: the center-local year, the overflow ------------------------------------------


def test_b3_the_year_is_the_center_local_year_of_issue(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    assert ps.series_value(url, 2027) == 0  # this module never used 2027
    old = ps.series_value(url, 2026)
    late_tutor = create_user(url, roles=(TUTOR,))
    early_tutor = create_user(url, roles=(TUTOR,))
    late = worked_period(url, client, owner, late_tutor)
    early = worked_period(url, client, owner, early_tutor)
    # 2026-12-31 23:30 local is 2027-01-01 05:30 UTC; the clock gives UTC.
    late_at = datetime(2027, 1, 1, 5, 30, tzinfo=UTC)
    early_at = datetime(2027, 1, 1, 6, 10, tzinfo=UTC)

    team = team_at(url, client, late_at)
    with frozen_clock(late_at):
        locked(client, url, team.finance, team.owner, late)
    team = team_at(url, client, early_at)
    with frozen_clock(early_at):
        locked(client, url, team.finance, team.owner, early)

    late_row = ps.payslip_of(url, late)
    early_row = ps.payslip_of(url, early)
    assert late_row.number == f"P-2026-{old + 1:06d}"
    assert late_row.issued_at.year == 2027  # already 2027 in UTC
    assert str(parse_snapshot(late_row.snapshot).issued_on) == "2026-12-31"
    assert early_row.number == "P-2027-000001"
    assert (ps.series_value(url, 2026), ps.series_value(url, 2027)) == (old + 1, 1)


def test_b3_past_999999_the_finalize_fails_and_nothing_is_written(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    run_sql(
        url,
        "INSERT INTO number_series (code, year, last_value) VALUES ('P', 2040, 999999)",
    )
    period = worked_period(url, client, owner, tutor)
    at = datetime(2040, 6, 1, 12, 0, tzinfo=ZONE)
    team = team_at(url, client, at)

    with frozen_clock(at):
        assert code(prepare(client, team.finance, period)) == OK
        response = finalize(client, url, team.owner, period)

    assert code(response) == SERVER_ERROR
    assert status_of(url, period) == "prepared"
    assert ps.payslip_of(url, period) is None
    assert ps.series_value(url, 2040) == 999999


def test_b3_the_exhausted_series_is_the_numbering_error(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    run_sql(
        url,
        "INSERT INTO number_series (code, year, last_value) VALUES ('P', 2041, 999999)"
        " ON CONFLICT DO NOTHING",
    )
    period = worked_period(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    review = datetime.fromisoformat(prepared_at(url, period) or "")
    principal = principal_of(owner.user.id, OWNER)

    async def _run() -> None:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), owner.user.id) as uow:
                await payroll_workflow.finalize(uow.session, principal, period, review)
        finally:
            await engine.dispose()

    with frozen_clock(datetime(2041, 3, 1, 12, 0, tzinfo=ZONE)):
        with pytest.raises(NumberSeriesExhaustedError):
            asyncio.run(_run())
    assert status_of(url, period) == "prepared"


# --- B4: the language -----------------------------------------------------------------


def _prefer(url: str, user: uuid.UUID, language: str | None) -> None:
    run_sql(
        url,
        "INSERT INTO user_preferences (user_id, language) VALUES (:u, :l)"
        " ON CONFLICT (user_id) DO UPDATE SET language = :l",
        {"u": user, "l": language},
    )


@pytest.mark.parametrize(
    ("stored", "issued"), [("en", "en"), ("es", "es"), (None, "es")]
)
def test_b4_the_language_is_the_tutors_stored_preference(
    module_seeded_url, client, owner, finance, tutor, stored, issued
):
    url = module_seeded_url
    _prefer(url, tutor.id, stored)
    period = worked_period(url, client, owner, tutor)

    locked(client, url, finance, owner, period)
    _prefer(url, tutor.id, "es" if issued == "en" else "en")

    assert ps.payslip_of(url, period).language == issued


# --- B5: the snapshot -----------------------------------------------------------------


def test_b5_the_snapshot_holds_every_kind_of_line_without_ids_or_students(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    row = ps.payslip_of(url, rich.second)
    data = row.snapshot

    assert set(data) == {
        "v",
        "center",
        "tutor",
        "period",
        "issued_on",
        "lines",
        "summary",
        "total",
    }
    [settings] = run_sql(url, "SELECT * FROM business_settings")
    assert data["center"] == {
        "name": settings.center_name,
        "address": settings.address,
        "phone": settings.contact_phone,
        "email": settings.contact_email,
    }
    assert data["tutor"] == {
        "user_id": str(tutor.id),
        "display_name": "Tutora Núñez Peña",
    }
    assert data["period"]["id"] == str(rich.second)
    assert data["period"]["starts_on"] == "2026-11-09"
    assert data["period"]["pay_frequency"] == "weekly"
    kinds = [line["kind"] for line in data["lines"]]
    assert sorted(kinds) == sorted(
        ["session", "salary", "pay_item", "pay_item", "adjustment"]
    )
    by_kind = {line["kind"]: line for line in data["lines"]}
    session = by_kind["session"]
    assert session["amount"] == "110.00"
    assert session["minutes"] == 55
    assert session["rate_model"] == "per_hour"
    assert session["rate"] == "120.0000"
    assert session["covered_by_salary"] is False
    assert session["pay_rule"] is None
    salary = by_kind["salary"]["salary"]
    assert salary["amount"] == "600.00"
    assert salary["basis"] == "calendar_days"
    items = {
        line["pay_item"]["type_name"]: line
        for line in data["lines"]
        if line["pay_item"]
    }
    assert items[ps.BONUS_TYPE]["amount"] == "200.00"
    assert items[ps.BONUS_TYPE]["pay_item"]["direction"] == "bonus"
    assert items[ps.BONUS_TYPE]["pay_item"]["description"] == "Noviembre puntual"
    assert items[ps.DEDUCTION_TYPE]["amount"] == "-50.00"
    adjustment = by_kind["adjustment"]
    assert adjustment["amount"] == "25.00"
    assert adjustment["adjustment"] == {
        "kind": "manual",
        "reason": ps.REASON,
        "service_date": "2026-11-03",
        "class_kind": "individual",
        "origin_period": {"starts_on": "2026-11-02", "ends_on": "2026-11-08"},
        "paired_period": None,
    }
    summary = data["summary"]
    assert summary["class_line_count"] == 1
    assert summary["class_minutes"] == 55
    assert summary["bonuses"] == "200.00"
    assert summary["deductions"] == "-50.00"
    assert summary["adjustments"] == "25.00"
    [stored_total] = run_sql(
        url,
        "SELECT total_amount FROM payroll_periods WHERE id = :p",
        {"p": rich.second},
    )
    assert data["total"] == str(stored_total.total_amount)
    # No ids but the period's and the tutor's, no student or subject name.
    text = json.dumps(data)
    for forbidden in ("class_session_id", "approval_id", "student", "subject", '_id":'):
        assert forbidden not in text.replace('"user_id":', ""), forbidden


def test_b5_a_carried_forward_period_gets_a_payslip_at_0(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "200.00")
    assert code(carry(client, finance, period)) == (201, None)
    locked(client, url, finance, owner, period)

    data = ps.payslip_of(url, period).snapshot

    assert data["total"] == "0.00"
    [carried] = [line for line in data["lines"] if line["kind"] == "adjustment"]
    assert carried["adjustment"]["kind"] == "carry_forward"
    assert carried["adjustment"]["reason"] is None
    assert carried["adjustment"]["origin_period"] == {
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-08",
    }
    assert carried["adjustment"]["paired_period"]["starts_on"] == "2026-11-09"


# --- B6: frozen -----------------------------------------------------------------------


def test_b6_later_changes_leave_an_issued_payslip_alone(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rich = ps.rich_periods(url, client, owner, finance, tutor)
    before = ps.payslip_of(url, rich.second)
    html = payslip_html(stored_of(before))
    [settings] = run_sql(url, "SELECT center_name FROM business_settings")
    third = add_period_row(url, tutor.id, "11-16", "11-22", first=False)

    run_sql(
        url,
        "UPDATE pay_item_types SET name = 'Otro nombre' WHERE id = :t",
        {"t": rich.bonus_type},
    )
    run_sql(
        url,
        "UPDATE users SET display_name = 'Nombre Nuevo' WHERE id = :u",
        {"u": tutor.id},
    )
    run_sql(url, "UPDATE business_settings SET center_name = 'Centro Renombrado'")
    try:
        corrected = manual(
            client,
            owner,
            tutor.id,
            "-10.00",
            origin_line_id=ps.line_id(url, rich.second),
        )
        assert code(corrected) == (201, None), corrected.text
        paid = pay(client, finance, rich.second, body("100.00"))
        assert code(paid) == (201, None), paid.text
        assert code(reverse(client, owner, paid.json()["id"])) == (201, None)

        after = ps.payslip_of(url, rich.second)
        assert after._mapping == before._mapping
        assert payslip_html(stored_of(after)) == html
        assert ps.payslip_of(url, third) is None
    finally:
        run_sql(
            url,
            "UPDATE business_settings SET center_name = :n",
            {"n": settings.center_name},
        )


def test_b6_payslips_refuse_raw_updates_and_deletes(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    locked(client, url, finance, owner, period)
    row = ps.payslip_of(url, period)

    for statement in (
        "UPDATE payslips SET language = 'en' WHERE id = :id",
        "DELETE FROM payslips WHERE id = :id",
    ):
        with pytest.raises(Exception, match="append-only"):
            run_sql(url, statement, {"id": row.id})


# --- B8: no pay in side channels ------------------------------------------------------


def test_b8_no_amount_or_text_reaches_logs_events_notifications_or_keys(
    module_seeded_url, client, owner, finance, tutor, caplog
):
    url = module_seeded_url
    with caplog.at_level(logging.DEBUG):
        rich = ps.rich_periods(url, client, owner, finance, tutor)
    row = ps.payslip_of(url, rich.second)

    secrets = (
        "885.00",
        "600.00",
        "200.00",
        "Noviembre puntual",
        "acordada",
        ps.BONUS_TYPE,
    )
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in secrets:
        assert secret not in logged, secret
    events = run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_id = ANY(:ids)",
        {"ids": [rich.second, row.id]},
    )
    assert events
    payslip_events = [e for e in events if e.record_id == row.id]
    assert len(payslip_events) == 1
    for audit in events:
        text = json.dumps(audit.details, default=str) + json.dumps(
            getattr(audit, "changes", None), default=str
        )
        for secret in secrets:
            assert secret not in text, (audit.action, secret)
    notes = run_sql(
        url,
        "SELECT params FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'payroll.period_finalized'",
        {"u": tutor.id},
    )
    assert len(notes) == 2
    for note in notes:
        assert set(note.params) == {"payroll_period_id", "starts_on", "ends_on"}
    keys = run_sql(
        url,
        "SELECT response_body FROM idempotency_keys WHERE endpoint LIKE '%/finalize'"
        " AND user_id = :u",
        {"u": owner.user.id},
    )
    for key in keys:
        text = json.dumps(key.response_body, default=str)
        for secret in secrets:
            assert secret not in text, secret
        assert "payslip" not in text


# --- B2: two finalizes on real connections --------------------------------------------


@pytest.mark.parametrize("attempt", range(2))
def test_b2_two_tutors_finalized_at_once_get_consecutive_numbers(
    module_seeded_url, client, owner, finance, attempt
):
    url = module_seeded_url
    tutors = [create_user(url, roles=(TUTOR,)) for _ in range(2)]
    periods = [worked_period(url, client, owner, t) for t in tutors]
    for period in periods:
        assert code(prepare(client, finance, period)) == OK
    before = ps.series_value(url, YEAR)
    principal = principal_of(owner.user.id, OWNER)
    reviews = [datetime.fromisoformat(prepared_at(url, p) or "") for p in periods]

    async def one(period: uuid.UUID, review: datetime) -> Any:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), owner.user.id) as uow:
                return await payroll_workflow.finalize(
                    uow.session, principal, period, review
                )
        finally:
            await engine.dispose()

    async def both() -> list[Any]:
        return list(
            await asyncio.wait_for(
                asyncio.gather(
                    *(one(p, r) for p, r in zip(periods, reviews, strict=True))
                ),
                30,
            )
        )

    results = asyncio.run(both())

    assert [r.status for r in results] == ["finalized", "finalized"]
    numbers = sorted(int(ps.payslip_of(url, p).number[-6:]) for p in periods)
    assert numbers == [before + 1, before + 2]
    assert ps.series_value(url, YEAR) == before + 2
