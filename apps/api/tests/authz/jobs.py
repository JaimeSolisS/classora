"""The job inventory (M10-01b, #640): every background task and how it checks
the user it writes for, or acts as.

``jobs.toml`` lists every registered Procrastinate task. :func:`check_inventory`
compares it with the task registry and the registered notification types, and
lists every problem as ``task: reason``. ``tests/unit/test_job_inventory.py``
runs it on the real registry (``app_env="local"``, so the demo tasks count).

Kinds (#640 E1, E2):

- ``reader_checked``: the job writes output for a reader (a notification, a
  file, a draft) and checks that reader's authorization when it writes;
- ``actor_rechecked``: the job writes records **as** a user and re-checks
  that user's permission while it runs;
- ``no_reader_output``: the job writes nothing addressed to a reader.

The first two need ``check`` (one sentence) and ``tests`` (node ids, resolved
with the IDOR inventory's :func:`tests.authz.idor.node_problem`); the last one
needs ``reason``. ``notifications`` (optional) names the notification types
the job creates.
"""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tests.authz.idor import API_ROOT, node_problem

JOBS_PATH = Path(__file__).with_name("jobs.toml")

CHECKED_KINDS: Final = frozenset({"reader_checked", "actor_rechecked"})
NO_OUTPUT: Final = "no_reader_output"
KINDS: Final = CHECKED_KINDS | {NO_OUTPUT}
ENTRY_FIELDS: Final = frozenset(
    {"name", "kind", "check", "tests", "reason", "notifications"}
)


@dataclass(frozen=True)
class JobEntry:
    """One ``[[task]]`` table of ``jobs.toml``, kept as written."""

    name: Any
    kind: Any = None
    check: Any = None
    tests: Any = None
    reason: Any = None
    notifications: Any = None
    unknown_fields: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return str(self.name)


def parse_inventory(document: Mapping[str, Any]) -> list[JobEntry]:
    """The entries of a parsed inventory document (``{"task": [...]}``)."""
    entries = []
    for table in document.get("task", []):
        values = {name: table[name] for name in ENTRY_FIELDS if name in table}
        entries.append(
            JobEntry(
                **{"name": None, **values},
                unknown_fields=tuple(sorted(set(table) - ENTRY_FIELDS)),
            )
        )
    return entries


def load_inventory(path: Path = JOBS_PATH) -> list[JobEntry]:
    with path.open("rb") as file:
        return parse_inventory(tomllib.load(file))


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def order_problems(entries: Iterable[JobEntry]) -> list[str]:
    """Entries must be sorted by task name."""
    names = [str(entry.name) for entry in entries]
    return [
        f"{after}: out of order (must come before {before})"
        for before, after in zip(names, names[1:], strict=False)
        if after < before
    ]


def entry_problems(
    entry: JobEntry,
    notification_types: frozenset[str],
    root: Path,
    cache: dict[Path, set[str]],
) -> list[str]:
    """Field problems of one entry (kind, check/tests/reason, node ids,
    notification types)."""
    problems = [f"unknown field {name!r}" for name in entry.unknown_fields]
    if entry.kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(sorted(KINDS))}")
        return problems
    if entry.kind in CHECKED_KINDS:
        if entry.reason is not None:
            problems.append(f"reason is only for {NO_OUTPUT} (use check)")
        if not _text(entry.check):
            problems.append(f"{entry.kind} needs a non-empty check")
        if not isinstance(entry.tests, list) or not entry.tests:
            problems.append(f"{entry.kind} needs a non-empty tests list")
        else:
            problems.extend(
                problem
                for node_id in entry.tests
                if (problem := node_problem(node_id, root, cache)) is not None
            )
    else:
        if entry.check is not None or entry.tests is not None:
            problems.append(f"{NO_OUTPUT} takes a reason, not check or tests")
        if not _text(entry.reason):
            problems.append(f"{NO_OUTPUT} needs a non-empty reason")
    if entry.notifications is not None:
        if not isinstance(entry.notifications, list) or not entry.notifications:
            problems.append("notifications must be a non-empty list when given")
        else:
            problems.extend(
                f"notification type {code!r} is not registered"
                for code in entry.notifications
                if code not in notification_types
            )
    return problems


def check_inventory(
    registered_tasks: Iterable[str],
    notification_types: Iterable[str],
    inventory: list[JobEntry],
    *,
    root: Path = API_ROOT,
) -> list[str]:
    """Every problem of ``inventory`` against the registered tasks, sorted."""
    tasks = set(registered_tasks)
    types = frozenset(notification_types)
    problems: list[str] = []
    seen: set[str] = set()
    cache: dict[Path, set[str]] = {}
    for entry in inventory:
        if not _text(entry.name):
            problems.append(f"{entry.label}: name must be a non-empty task name")
            continue
        if entry.name in seen:
            problems.append(f"{entry.label}: duplicate entry")
        seen.add(entry.name)
        if entry.name not in tasks:
            problems.append(f"{entry.label}: stale entry (not a registered task)")
        problems.extend(
            f"{entry.label}: {problem}"
            for problem in entry_problems(entry, types, root, cache)
        )
    problems.extend(
        f"{task}: missing job inventory entry" for task in tasks if task not in seen
    )
    problems.extend(order_problems(inventory))
    return sorted(set(problems))


def failure_message(problems: list[str]) -> str:
    return (
        "Job inventory problems (tests/authz/jobs.toml, see"
        " _docs/testing-guidelines.md 'Job inventory'):\n" + "\n".join(problems)
    )
