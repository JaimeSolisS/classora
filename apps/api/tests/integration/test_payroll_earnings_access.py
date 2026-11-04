"""Who reads a tutor's earnings and payslips (#113 C1, C4, C5; E2; spec §2;
ADR-0017 E12).

The compensation record policy: the tutor themselves (holding the tutor role)
or a ``payroll.view`` holder (Owner, Finance Staff). Everyone else, a delegated
Administrator included, and every id that isn't this tutor's, get the same
``404`` as an unknown id. Check order ``401`` → ``403
privacy_notice_required`` → ``422`` → ``404`` → ``200``.

The world is built once: tutor A with a finalized period (and its payslip), a
prepared one and an open one; tutor B with a finalized period and payslip.
Rendering a PDF needs Pango, so a download that succeeds is tested in
``test_payroll_payslips_pdf.py``; here a payslip route answers ``404``, or
``500`` for the failure cases.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.modules.payroll import service as payroll_service
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payslip_support as ps
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import staff
from tests.integration.invitation_support import sign_in
from tests.integration.payroll_calculation_support import (
    add_period_row,
    approved_lesson,
    clear,
)
from tests.integration.payroll_payment_support import NOW, STARTS, worked_period
from tests.integration.payroll_period_support import finalize as finalize_by_sql
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import OK, code, locked, prepare
from tests.integration.test_attendance import lesson

GONE = "00000000-0000-7000-8000-000000000000"


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass(frozen=True)
class World:
    a: CreatedUser
    b: CreatedUser
    a_final: uuid.UUID
    a_prepared: uuid.UUID
    a_open: uuid.UUID
    b_final: uuid.UUID
    a_payslip: uuid.UUID
    b_payslip: uuid.UUID
    cookies: dict[str, SetCookie]
    finance_tutor: CreatedUser
    finance_tutor_period: uuid.UUID


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: TestClient) -> Iterator[World]:
    url = module_seeded_url
    set_floor(url, "10-01")
    owner = staff(url, client, (OWNER,))
    finance = staff(url, client, (FINANCE_STAFF,))
    a = create_user(url, roles=(TUTOR,), display_name="Tutora A")
    b = create_user(url, roles=(TUTOR,), display_name="Tutor B")
    a_final = worked_period(url, client, owner, a)
    a_prepared = add_period_row(url, a.id, "11-09", "11-15", first=False)
    a_open = add_period_row(url, a.id, "11-16", "11-22", first=False)
    locked(client, url, finance, owner, a_final)
    approved_lesson(
        client, lesson(url, starts_at=STARTS + timedelta(days=7), tutor=a), owner.user
    )
    assert code(prepare(client, finance, a_prepared)) == OK
    b_final = worked_period(url, client, owner, b)
    locked(client, url, finance, owner, b_final)
    cookies = {
        "a": sign_in(client, a.username),
        "b": sign_in(client, b.username),
        OWNER: owner.cookie,
        FINANCE_STAFF: finance.cookie,
    }
    for name, roles, delegations in (
        (ADMINISTRATOR, (ADMINISTRATOR,), []),
        ("delegated", (ADMINISTRATOR,), ["payroll.finalize"]),
        (ACADEMIC_COORDINATOR, (ACADEMIC_COORDINATOR,), []),
        (GUARDIAN, (GUARDIAN,), []),
    ):
        user = create_user(url, roles=roles, delegations=delegations)
        cookies[name] = sign_in(client, user.username)
    finance_tutor = create_user(url, roles=(FINANCE_STAFF, TUTOR))
    cookies["finance_tutor"] = sign_in(client, finance_tutor.username)
    finance_tutor_period = add_period_row(url, finance_tutor.id, "11-02", "11-08")
    finalize_by_sql(url, finance_tutor_period, owner.user.id)
    yield World(
        a=a,
        b=b,
        a_final=a_final,
        a_prepared=a_prepared,
        a_open=a_open,
        b_final=b_final,
        a_payslip=ps.payslip_of(url, a_final).id,
        b_payslip=ps.payslip_of(url, b_final).id,
        cookies=cookies,
        finance_tutor=finance_tutor,
        finance_tutor_period=finance_tutor_period,
    )
    clear(url)
    set_floor(url, None)


def paths(w: World) -> list[str]:
    """A's three routes, with A's own ids."""
    return [
        ps.earnings_path(w.a.id),
        ps.period_path(w.a.id, w.a_final),
        ps.pdf_path(w.a.id, w.a_payslip),
    ]


def as_(client: TestClient, w: World, name: str, path: str) -> Response:
    return ps.get_as(client, w.cookies[name], path)


# --- C1: the check order --------------------------------------------------------------


def test_c1_anonymous_is_401(client, world):
    for path in paths(world):
        response = ps.get_as(client, None, path)
        assert response.status_code == 401, path
        assert response.json()["code"] == "authentication_required"


def test_c1_the_privacy_notice_comes_before_everything_else(
    module_seeded_url, client, world
):
    pending = create_user(
        module_seeded_url, roles=(FINANCE_STAFF,), privacy_notice_accepted=False
    )
    cookie = sign_in(client, pending.username)
    for path in [
        *paths(world),
        "/api/v1/payroll/tutors/x/earnings",
        ps.earnings_path(GONE),
    ]:
        response = ps.get_as(client, cookie, path)
        assert response.status_code == 403, path
        assert response.json()["code"] == "privacy_notice_required"


@pytest.mark.parametrize("name", ["a", ACADEMIC_COORDINATOR])
def test_c1_a_malformed_id_or_query_is_422_before_the_404(client, world, name):
    for path in (
        "/api/v1/payroll/tutors/not-a-uuid/earnings",
        f"/api/v1/payroll/tutors/{world.a.id}/earnings/not-a-uuid",
        f"/api/v1/payroll/tutors/{world.a.id}/payslips/not-a-uuid/pdf",
        ps.earnings_path(world.a.id) + "?limit=abc",
        ps.earnings_path(world.a.id) + "?offset=-1",
    ):
        response = as_(client, world, name, path)
        assert response.status_code == 422, (path, response.text)
        assert response.json()["code"] == "validation_error"


# --- C5: IDOR -------------------------------------------------------------------------


def test_c5_the_tutor_reads_their_own(client, world):
    assert as_(client, world, "a", ps.earnings_path(world.a.id)).status_code == 200
    detail = as_(client, world, "a", ps.period_path(world.a.id, world.a_final))
    assert detail.status_code == 200
    assert detail.json()["payslip"]["id"] == str(world.a_payslip)


def test_c5_tutor_a_cant_reach_anything_of_b(client, world):
    missing = as_(client, world, "a", ps.period_path(world.a.id, GONE))
    for path in (
        ps.earnings_path(world.b.id),
        ps.period_path(world.b.id, world.b_final),
        ps.period_path(world.a.id, world.b_final),
        ps.pdf_path(world.b.id, world.b_payslip),
        ps.pdf_path(world.a.id, world.b_payslip),
        ps.period_path(world.b.id, world.a_final),
        ps.pdf_path(world.b.id, world.a_payslip),
    ):
        assert_same_not_found(as_(client, world, "a", path), missing)


def test_c5_a_tutors_own_open_and_prepared_periods_are_404(client, world):
    missing = as_(client, world, "a", ps.period_path(world.a.id, GONE))
    for period in (world.a_prepared, world.a_open):
        assert_same_not_found(
            as_(client, world, "a", ps.period_path(world.a.id, period)), missing
        )


@pytest.mark.parametrize(
    "name", [GUARDIAN, ACADEMIC_COORDINATOR, ADMINISTRATOR, "delegated", "b"]
)
def test_c5_everyone_else_gets_the_same_404_on_all_three(client, world, name):
    missing = as_(client, world, name, ps.earnings_path(GONE))
    for path in paths(world):
        assert_same_not_found(as_(client, world, name, path), missing)


@pytest.mark.parametrize("name", [OWNER, FINANCE_STAFF])
def test_c5_finance_and_the_owner_read_any_tutor(client, world, name):
    for tutor, period in ((world.a, world.a_final), (world.b, world.b_final)):
        listed = as_(client, world, name, ps.earnings_path(tutor.id))
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [str(period)]
        assert (
            as_(client, world, name, ps.period_path(tutor.id, period)).status_code
            == 200
        )


@pytest.mark.parametrize("name", [OWNER, FINANCE_STAFF])
def test_c5_ids_that_arent_the_tutors_are_404_for_finance_too(
    module_seeded_url, client, world, name
):
    missing = as_(client, world, name, ps.earnings_path(GONE))
    non_tutor = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    for path in (
        ps.earnings_path(non_tutor.id),
        ps.period_path(world.a.id, world.b_final),
        ps.period_path(world.a.id, world.a_open),
        ps.period_path(world.a.id, world.a_prepared),
        ps.pdf_path(world.a.id, world.b_payslip),
        ps.pdf_path(world.b.id, world.a_payslip),
        ps.pdf_path(world.a.id, GONE),
    ):
        assert_same_not_found(as_(client, world, name, path), missing)


def test_c5_a_finance_member_who_tutors_reads_their_own(client, world):
    me = world.finance_tutor.id

    listed = as_(client, world, "finance_tutor", ps.earnings_path(me))
    detail = as_(
        client, world, "finance_tutor", ps.period_path(me, world.finance_tutor_period)
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [
        str(world.finance_tutor_period)
    ]
    assert detail.status_code == 200


# --- Edge cases: the leaver and the lost role -----------------------------------------


def test_finance_still_reads_an_archived_tutor(module_seeded_url, client, world):
    url = module_seeded_url
    leaver = create_user(url, roles=(TUTOR,))
    period = add_period_row(url, leaver.id, "11-02", "11-08")
    finalize_by_sql(url, period, leaver.id)
    run_sql(url, "UPDATE users SET state = 'archived' WHERE id = :u", {"u": leaver.id})

    response = as_(client, world, FINANCE_STAFF, ps.earnings_path(leaver.id))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(period)]


def test_a_user_who_lost_the_tutor_role_reads_nothing_finance_still_does(
    module_seeded_url, client, world
):
    url = module_seeded_url
    former = create_user(url, roles=(TUTOR, ACADEMIC_COORDINATOR))
    period = add_period_row(url, former.id, "11-02", "11-08")
    finalize_by_sql(url, period, former.id)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :u AND role_id ="
        " (SELECT id FROM roles WHERE code = 'tutor')",
        {"u": former.id},
    )
    cookie = sign_in(client, former.username)

    own = ps.get_as(client, cookie, ps.earnings_path(former.id))

    assert_same_not_found(own, ps.get_as(client, cookie, ps.earnings_path(GONE)))
    finance = as_(client, world, FINANCE_STAFF, ps.earnings_path(former.id))
    assert finance.status_code == 200
    assert len(finance.json()["items"]) == 1


# --- C4: render failures --------------------------------------------------------------


def test_c4_a_render_failure_is_a_logged_500_without_the_snapshot(
    client, world, monkeypatch, caplog
):
    def broken(stored: Any) -> bytes:
        raise RuntimeError(f"cannot render {stored.snapshot['tutor']['display_name']}")

    monkeypatch.setattr(payroll_service, "render_payslip", broken)

    with caplog.at_level(logging.ERROR, logger="app.workflows.payroll_earnings"):
        response = as_(client, world, "a", ps.pdf_path(world.a.id, world.a_payslip))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "internal_error"
    logged = [r for r in caplog.records if r.name == "app.workflows.payroll_earnings"]
    assert len(logged) == 1
    message = logged[0].getMessage()
    assert str(world.a_payslip) in message
    assert "RuntimeError" in message
    assert "Tutora A" not in message
    assert logged[0].exc_info is None


def test_c4_an_unknown_template_version_is_the_same_logged_500(
    module_seeded_url, client, world, caplog
):
    url = module_seeded_url
    period = add_period_row(url, world.b.id, "11-09", "11-15", first=False)
    finalize_by_sql(url, period, world.b.id)
    [row] = run_sql(
        url,
        "INSERT INTO payslips (number, payroll_period_id, tutor_user_id, issued_at,"
        " language, template_version, snapshot) VALUES ('P-2099-000001', :p, :t,"
        " now(), 'es', 2, '{\"v\": 2}') RETURNING id",
        {"p": period, "t": world.b.id},
    )

    with caplog.at_level(logging.ERROR, logger="app.workflows.payroll_earnings"):
        response = as_(client, world, "b", ps.pdf_path(world.b.id, row.id))

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    [logged] = [r for r in caplog.records if r.name == "app.workflows.payroll_earnings"]
    assert "UnknownPayslipTemplateError" in logged.getMessage()
    assert str(row.id) in logged.getMessage()
