"""The M6-demo-b receipts as PDFs (E8): ``demo.guardian`` downloads PY1's
receipt and ``demo.finance`` the reversed PY3's, after a whole run on the
#299 frozen day.

It renders with WeasyPrint, so it needs Pango: on the owner's Mac it is not
run natively ("cannot load library" there is expected, BRIEF #383 D6); the
evidence is CI ``api-integration``. Receipts are rendered on download only
(ADR-0015), so the seed itself needs no Pango."""

from typing import Any

import pytest

from tests.factories import demo
from tests.factories.demo_billing_money import (
    DemoPaymentsGenerator,
    DemoReconciliationGenerator,
    DemoRemindersGenerator,
)
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    CHARGES_GENERATOR,
    FROZEN_NOW,
    demo_env,
    generators_through,
    migrate,
)
from tests.integration.receipt_support import download, pdf_text
from tests.integration.test_demo_billing import _families
from tests.integration.test_demo_schedule import signed_in


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _compact(value: str) -> str:
    return "".join(value.split())


def _receipt(url: str, payment_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, number FROM receipts WHERE payment_id = :p",
        {"p": payment_id},
    )
    return row


def test_the_demo_receipts_download(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    payments = DemoPaymentsGenerator()
    generators = [
        *generators_through(CHARGES_GENERATOR),
        DemoReconciliationGenerator(),
        payments,
        DemoRemindersGenerator(),
    ]
    assert demo.main([], generators=generators) == 0
    assert payments.recorded is not None
    families = {label: family for family, label in _families(migrated_url).items()}
    py1 = _receipt(migrated_url, payments.recorded.py1)
    py3 = _receipt(migrated_url, payments.recorded.py3)

    with auth_client(migrated_url) as client:
        cookie = signed_in(client, "demo.guardian")
        response = download(client, cookie, families["F1"], py1.id)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        text = _compact(pdf_text(response.content))
        assert _compact(py1.number) in text
        assert _compact("$1,500.00") in text

        cookie = signed_in(client, "demo.finance")
        response = download(client, cookie, families["F2"], py3.id)
        assert response.status_code == 200, response.text
        text = _compact(pdf_text(response.content))
        assert _compact(py3.number) in text
        # F2 has no recipient with a language: the receipt is es (#98 P5).
        assert "ANULADOel" in text
