"""The full README lists the simulated accounts exactly (#586 HF-09, E2 a).

`_docs/Full_README.md` "Simulated operations data" lists every login the default seed
(``make seed-data-spanish`` / ``make seed-data-english``: the ``full``
profile, ``--seed 1``) creates. These tests rebuild each table from the
pure plan and compare it with the README text between the markers, so a
change to the seed's people fails here with the block to paste.
"""

from pathlib import Path

import pytest

from tests.factories.simulation.plan import (
    STAFF_PASSWORDS,
    STAFF_USERNAMES,
    AccountPlan,
    SimulationPlan,
    plan_simulation,
)
from tests.factories.simulation.texts import Language

README = Path(__file__).resolve().parents[4] / "_docs" / "Full_README.md"

SEED = 1
"""The ``--seed`` default the ``make seed-data-*`` targets run with."""

ROLE_LABELS = {
    "tutor": "Tutor",
    "guardian": "Parent or guardian",
    "student": "Student",
}
STAFF_LABELS = {
    "owner": "Owner",
    "administrator": "Administrator",
    "academic_coordinator": "Academic coordinator",
    "finance_staff": "Finance staff",
}
NO_FAMILY = "—"
HEADER = (
    "| Username | Name | Role | Family | Password |",
    "|---|---|---|---|---|",
)
STAFF_HEADER = "| Spanish username | English username | Password | Role |"


def _row(account: AccountPlan, family: str) -> str:
    return (
        f"| `{account.username}` | {account.display_name} "
        f"| {ROLE_LABELS[account.role]} | {family} | `{account.password}` |"
    )


def accounts_table(plan: SimulationPlan) -> str:
    """The README table: tutors, then guardians, then students (family order)."""
    rows = [_row(tutor.account, NO_FAMILY) for tutor in plan.tutors]
    rows += [
        _row(guardian.account, family.name)
        for family in plan.families
        for guardian in family.guardians
        if guardian.account is not None
    ]
    rows += [
        _row(student.account, family.name)
        for family in plan.families
        for student in family.students
        if student.account is not None
    ]
    return "\n".join((*HEADER, *rows))


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _block(text: str, language: Language) -> str:
    start = f"<!-- sim-accounts:{language}:start -->"
    end = f"<!-- sim-accounts:{language}:end -->"
    assert text.count(start) == 1, f"README needs exactly one {start}"
    assert text.count(end) == 1, f"README needs exactly one {end}"
    return text.split(start, 1)[1].split(end, 1)[0].strip("\n")


@pytest.mark.parametrize("language", ["es", "en"])
def test_readme_lists_every_simulated_login(language: Language) -> None:
    plan = plan_simulation(language, "full", SEED)
    expected = accounts_table(plan)

    assert _block(_readme(), language) == expected, (
        f"README sim-accounts:{language} block is out of date; paste this "
        f"between its markers:\n\n{expected}\n"
    )
    counts = [account.role for account in plan.accounts[len(plan.staff) :]]
    assert (
        counts.count("tutor"),
        counts.count("guardian"),
        counts.count("student"),
    ) == (12, 30, 6)


def test_readme_staff_table_matches_the_seed() -> None:
    text = _readme()
    assert text.count(STAFF_HEADER) == 1
    after = text.split(STAFF_HEADER, 1)[1].lstrip("\n").splitlines()
    rows = []
    for line in after[1:]:
        if not line.startswith("|"):
            break
        rows.append(line)

    expected = [
        f"| `{STAFF_USERNAMES['es'][role]}` | `{STAFF_USERNAMES['en'][role]}` "
        f"| `{STAFF_PASSWORDS[role]}` | {label} |"
        for role, label in STAFF_LABELS.items()
    ]
    assert rows == expected
