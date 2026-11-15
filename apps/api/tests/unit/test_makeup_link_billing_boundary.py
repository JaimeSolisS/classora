"""Billing never reads the makeup link (#423 D4; ADR-0006 E3)."""

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
COLUMN = "makeup_of_session_id"


def test_no_billing_or_workflow_source_names_the_link():
    sources = [
        *(APP / "modules" / "billing").rglob("*.py"),
        *(APP / "workflows").rglob("*.py"),
    ]

    assert sources
    assert [p for p in sources if COLUMN in p.read_text(encoding="utf-8")] == []
