"""The job inventory: every registered background task is classified by how it
checks the user it writes for or acts as (M10-01b, #640 E1, E2), and the
check's self-test."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core import jobs
from app.core.config import Settings
from app.modules.administration import service as administration
from tests.authz.csrf import origin_for
from tests.authz.jobs import (
    JOBS_PATH,
    check_inventory,
    failure_message,
    load_inventory,
    order_problems,
    parse_inventory,
)

DSN = "postgresql://jobs_user:Inventory-pw-1@db:5432/classora"


def registered_tasks() -> set[str]:
    """The task registry the retry classification pins read
    (``test_job_rules.py``), in ``local`` so the demo tasks count."""
    settings = Settings(
        app_env="local",
        app_origin=origin_for("local"),
        database_url=SecretStr(DSN),
        database_direct_url=SecretStr(DSN),
    )
    app = jobs.create_job_app(jobs.worker_connector(settings), app_env="local")
    return set(jobs.task_parameter_types(app))


# --- The real registry ---------------------------------------------------------------


def test_every_registered_task_has_a_job_entry():
    problems = check_inventory(
        registered_tasks(),
        administration.registered_notification_types(),
        load_inventory(),
    )

    assert not problems, failure_message(problems)


def test_the_registry_includes_the_demo_tasks_and_matches_the_retry_pin():
    tasks = registered_tasks()

    assert {"demo:succeed", "demo:fail", "demo:periodic"} <= tasks
    assert tasks == jobs.RETRYABLE_TASKS | jobs.NON_RETRYABLE_TASKS


def test_jobs_file_is_sorted_and_has_a_how_to_header():
    text = JOBS_PATH.read_text(encoding="utf-8")

    assert order_problems(load_inventory()) == []
    assert text.startswith("# Job inventory")
    assert "Adding an entry" in text


def test_the_640_classification():
    kinds = {entry.name: entry.kind for entry in load_inventory()}

    assert {name for name, kind in kinds.items() if kind == "actor_rechecked"} == {
        "administration:run_imports"
    }
    assert {name for name, kind in kinds.items() if kind == "reader_checked"} == {
        "academics:create_progress_report_placeholders",
        "billing:send_charge_reminders",
        "reporting:run_exports",
        "scheduling:expire_offers",
        "scheduling:materialize_series",
    }


# --- Self-test on a synthetic registry and inventory --------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tests = tmp_path / "tests" / "integration"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text(
        "def test_lost():\n    pass\n\n\nclass TestGroup:\n"
        "    def test_member(self):\n        pass\n",
        encoding="utf-8",
    )
    return tmp_path


TYPES = frozenset({"x.ready"})
GOOD = [
    {
        "name": "a:actor",
        "kind": "actor_rechecked",
        "check": "reloads the actor",
        "tests": ["tests/integration/test_x.py::TestGroup::test_member"],
    },
    {
        "name": "b:reader",
        "kind": "reader_checked",
        "check": "re-checks the reader",
        "tests": ["tests/integration/test_x.py::test_lost"],
        "notifications": ["x.ready"],
    },
    {"name": "c:purge", "kind": "no_reader_output", "reason": "deletes rows"},
]
TASKS = {"a:actor", "b:reader", "c:purge"}


def problems_of(entries: list[dict], root: Path, tasks: set[str] = TASKS) -> list[str]:
    return check_inventory(tasks, TYPES, parse_inventory({"task": entries}), root=root)


def changed(index: int, **change: object) -> list[dict]:
    entries = [dict(entry) for entry in GOOD]
    for name, value in change.items():
        if value is None:
            entries[index].pop(name, None)
        else:
            entries[index][name] = value
    return entries


def test_a_good_inventory_has_no_problem(root):
    assert problems_of(GOOD, root) == []


def test_a_missing_and_a_stale_entry(root):
    problems = problems_of(GOOD, root, TASKS - {"c:purge"} | {"d:new"})

    assert problems == [
        "c:purge: stale entry (not a registered task)",
        "d:new: missing job inventory entry",
    ]


@pytest.mark.parametrize(
    ("index", "change", "expected"),
    [
        (2, {"check": "x"}, "c:purge: no_reader_output takes a reason, not check"),
        (2, {"tests": ["tests/integration/test_x.py::test_lost"]}, "c:purge: no_r"),
        (2, {"reason": "  "}, "c:purge: no_reader_output needs a non-empty reason"),
        (1, {"tests": None}, "b:reader: reader_checked needs a non-empty tests"),
        (1, {"tests": []}, "b:reader: reader_checked needs a non-empty tests"),
        (0, {"check": ""}, "a:actor: actor_rechecked needs a non-empty check"),
        (0, {"reason": "why"}, "a:actor: reason is only for no_reader_output"),
        (0, {"kind": "trusted"}, "a:actor: kind must be one of"),
        (0, {"kind": None}, "a:actor: kind must be one of"),
        (0, {"owner": "me"}, "a:actor: unknown field 'owner'"),
    ],
)
def test_the_wrong_fields_for_the_kind(root, index, change, expected):
    problems = problems_of(changed(index, **change), root)

    assert any(problem.startswith(expected) for problem in problems), problems


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ("tests/integration/test_x.py::test_gone", "no such test function"),
        ("tests/integration/test_missing.py::test_lost", "no such file"),
        ("tests/integration/test_x.py::test_lost[a]", "without its [...]"),
        ("tests/unit/test_x.py::test_lost", "is not under tests/integration/"),
        ("test_lost", "is not a 'file.py::test_name' node id"),
    ],
)
def test_a_test_node_must_resolve(root, node, expected):
    problems = problems_of(changed(1, tests=[node]), root)

    assert len(problems) == 1 and expected in problems[0], problems


def test_out_of_order_and_duplicate_entries(root):
    entries = [GOOD[1], GOOD[0], GOOD[2], GOOD[2]]

    problems = problems_of(entries, root)

    assert "a:actor: out of order (must come before b:reader)" in problems
    assert "c:purge: duplicate entry" in problems


@pytest.mark.parametrize(
    ("notifications", "expected"),
    [
        (["x.gone"], "b:reader: notification type 'x.gone' is not registered"),
        ([], "b:reader: notifications must be a non-empty list when given"),
    ],
)
def test_notifications_name_only_registered_types(root, notifications, expected):
    assert problems_of(changed(1, notifications=notifications), root) == [expected]


def test_the_failure_message_points_to_the_guidelines():
    message = failure_message(["x: missing job inventory entry"])

    assert "tests/authz/jobs.toml" in message
    assert message.endswith("x: missing job inventory entry")
