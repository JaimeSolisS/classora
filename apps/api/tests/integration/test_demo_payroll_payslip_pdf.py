"""The M7-demo payslips as PDFs (#499 E9): ``demo.tutor`` downloads the payslip
of its last finalized week, ``demo.finance`` one of ``demo.tutor2``'s, and
``demo.tutor2`` can't read ``demo.tutor``'s, after a whole run on the #299
frozen day.

It renders with WeasyPrint, so it needs Pango: on the owner's Mac it is not
run natively ("cannot load library" there is expected, BRIEF #383 D6); the
evidence is CI ``api-integration``. Payslips are rendered on download only, so
the seed itself needs no Pango."""

from datetime import date
from typing import Any

import pytest

from app.core import i18n
from tests.factories import demo
from tests.factories.demo_payroll import BONUS_ITEM, MANUAL_REASON
from tests.integration import payslip_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.demo_support import FROZEN_NOW, demo_env, migrate
from tests.integration.test_demo_schedule import signed_in

L1_DAY = date(2026, 10, 19)
"""L1's session date, shown on its late approval (``T`` = 2026-11-04)."""


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _compact(value: str) -> str:
    return "".join(value.split())


def _payslips(url: str, username: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT s.id, s.number, s.tutor_user_id FROM payslips s"
        " JOIN users u ON u.id = s.tutor_user_id"
        " JOIN payroll_periods p ON p.id = s.payroll_period_id"
        " WHERE u.username = :u ORDER BY p.starts_on",
        {"u": username},
    )


def test_the_demo_payslips_download(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    wn = _payslips(migrated_url, "demo.tutor")[-1]
    other = _payslips(migrated_url, "demo.tutor2")[0]

    with auth_client(migrated_url) as client:
        cookie = signed_in(client, "demo.tutor")
        response = ps.download(client, cookie, wn.tutor_user_id, wn.id)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        text = _compact(rs.pdf_text(response.content))
        assert _compact(wn.number) in text
        assert _compact(BONUS_ITEM) in text
        assert _compact(MANUAL_REASON) in text
        assert _compact(i18n.format_date("es", L1_DAY)) in text
        assert _compact("$970.00") in text

        cookie = signed_in(client, "demo.finance")
        response = ps.download(client, cookie, other.tutor_user_id, other.id)
        assert response.status_code == 200, response.text
        text = _compact(rs.pdf_text(response.content))
        assert _compact(other.number) in text
        assert _compact("$2,800.00") in text

        cookie = signed_in(client, "demo.tutor2")
        response = ps.download(client, cookie, wn.tutor_user_id, wn.id)
        assert response.status_code == 404
