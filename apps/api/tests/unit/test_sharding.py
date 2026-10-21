"""The CI test sharding (#217): shards are disjoint, cover every file and are
deterministic."""

import pytest

from tests.sharding import files_for_shard, parse_shard

PATHS = [f"tests/integration/test_{name}.py" for name in "kfjaicbhgde"]


@pytest.mark.parametrize("total", [1, 2, 3, 4, 11, 12])
def test_shards_partition_the_files(total: int):
    shards = [files_for_shard(PATHS, index, total) for index in range(1, total + 1)]

    assert set().union(*shards) == set(PATHS)
    assert sum(len(shard) for shard in shards) == len(PATHS)


def test_shards_are_round_robin_over_sorted_paths_and_order_independent():
    ordered = sorted(PATHS)

    assert files_for_shard(PATHS, 1, 4) == {ordered[0], ordered[4], ordered[8]}
    assert files_for_shard(PATHS, 4, 4) == {ordered[3], ordered[7]}
    assert files_for_shard(list(reversed(PATHS)), 2, 4) == files_for_shard(PATHS, 2, 4)


def test_parse_shard():
    assert parse_shard("1/1") == (1, 1)
    assert parse_shard("3/4") == (3, 4)


@pytest.mark.parametrize(
    "value", ["0/4", "5/4", "4", "1/0", "a/b", "1/4/2", " 1/4", "-1/4"]
)
def test_parse_shard_rejects_malformed_values(value: str):
    with pytest.raises(ValueError, match="--shard"):
        parse_shard(value)
