"""The weekly dependency audit (#150 SR-01; ADR-0016 amendment).

``.github/workflows/security-audit.yml`` runs ``make audit-deps`` on a
schedule, so a new advisory against unchanged lockfiles is noticed between
pull requests. actionlint (``make check-infra``) checks that the file is a
valid workflow; this checks what it must contain. Text checks only: the
API's test dependencies have no YAML parser.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[4] / ".github/workflows"
AUDIT_WORKFLOW = WORKFLOWS / "security-audit.yml"
APPROVED_ACTIONS = {
    "actions/checkout",
    "actions/setup-node",
    "actions/cache",
    "astral-sh/setup-uv",
}


def _lines() -> list[str]:
    return [
        line.split(" #", 1)[0].rstrip()
        for line in AUDIT_WORKFLOW.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]


def test_the_audit_runs_weekly_on_a_schedule():
    text = "\n".join(_lines())

    assert re.search(r"^on:\n  schedule:\n    - cron: ", text, re.MULTILINE)
    [cron] = re.findall(r'- cron: "([^"]+)"', text)
    minute, hour, day_of_month, month, day_of_week = cron.split()
    # Once a week: a fixed minute and hour on one weekday.
    assert minute.isdigit() and hour.isdigit()
    assert (day_of_month, month) == ("*", "*")
    assert re.fullmatch(r"[0-6]", day_of_week)


def test_the_audit_runs_make_audit_deps_with_read_only_permissions():
    lines = _lines()

    assert "permissions:" in lines
    assert lines[lines.index("permissions:") + 1] == "  contents: read"
    assert "        run: make audit-deps" in lines
    assert not any("secrets." in line for line in lines)


def test_the_audit_uses_only_approved_actions_pinned_by_sha():
    uses = [line.split("uses:", 1)[1].strip() for line in _lines() if "uses:" in line]

    assert uses
    for reference in uses:
        action, _, ref = reference.partition("@")
        assert action in APPROVED_ACTIONS, reference
        assert re.fullmatch(r"[0-9a-f]{40}", ref), reference
