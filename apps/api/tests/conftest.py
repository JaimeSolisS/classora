"""Apply the ``unit`` / ``integration`` marker from each test's directory.

Every test must live under ``tests/unit/`` or ``tests/integration/``. A test
anywhere else is a collection error, so it cannot escape both ``-m`` selections.
"""

from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).parent
MARKER_BY_DIRECTORY = {
    TESTS_ROOT / "unit": pytest.mark.unit,
    TESTS_ROOT / "integration": pytest.mark.integration,
}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    misplaced: list[str] = []
    for item in items:
        path = item.path
        for directory, marker in MARKER_BY_DIRECTORY.items():
            if path.is_relative_to(directory):
                item.add_marker(marker)
                break
        else:
            misplaced.append(item.nodeid)
    if misplaced:
        raise pytest.UsageError(
            "Tests must live in tests/unit/ or tests/integration/. Misplaced: "
            + ", ".join(misplaced)
        )
