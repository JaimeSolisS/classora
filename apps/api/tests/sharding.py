"""Deterministic test sharding for CI (#217): ``pytest --shard=K/N``.

After the ``-m``/``-k`` selection, the remaining test files are sorted by path
and dealt round-robin: file number ``i`` (0-based) belongs to shard
``i % N + 1``. A file is never split, so module-scoped fixtures (seeded shared
databases) are set up in one shard only. The N shards of the same selection
are disjoint and together run every selected test exactly once.

Reproduce a CI shard locally with ``make test-integration SHARD=K/N``.
"""

import re

import pytest

SHARD_PATTERN = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


def parse_shard(value: str) -> tuple[int, int]:
    """``"K/N"`` -> ``(K, N)`` with ``1 <= K <= N``."""
    match = SHARD_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"--shard must look like K/N (for example 2/4), got {value!r}")
    index, total = int(match.group(1)), int(match.group(2))
    if index > total:
        raise ValueError(f"--shard {value}: K must be between 1 and N")
    return index, total


def files_for_shard(paths: list[str], index: int, total: int) -> set[str]:
    """The files of shard ``index`` (1-based) out of ``total``."""
    ordered = sorted(set(paths))
    return {path for i, path in enumerate(ordered) if i % total == index - 1}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        default=None,
        metavar="K/N",
        help="run only shard K of N of the selected test files (by path)",
    )


# trylast: shard what is left after the marker (-m) and keyword (-k) selection.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    value = config.getoption("shard")
    if value is None:
        return
    try:
        index, total = parse_shard(value)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error

    root = config.rootpath
    keep = files_for_shard(
        [item.path.relative_to(root).as_posix() for item in items], index, total
    )
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if item.path.relative_to(root).as_posix() in keep:
            selected.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
