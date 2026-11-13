"""The DEV data reset's host side without a host (#674 AC3, AC8 `--check`).

``infrastructure/host/bin/dev-data-reset.sh``, ``manage.sh reset-dev-data``
and ``install-units.sh`` run here against a fake tree: ``CLASSORA_TEST_ROOT``
prefixes their paths, and fake ``compose.sh``, ``aws``, ``systemctl``,
``journalctl``, ``install`` and ``id`` record every call. Also pins the
workflow wiring (deploy-dev's ``dev-data`` job, nothing in release-prod) and
the reset's grants against migration 0096.
"""

import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
HOST_BIN = ROOT / "infrastructure" / "host" / "bin"
RESET = HOST_BIN / "dev-data-reset.sh"
MANAGE = HOST_BIN / "manage.sh"
INSTALL_UNITS = HOST_BIN / "install-units.sh"
SHA = "a" * 40
OLD_SHA = "b" * 40

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Host:
    """A fake host tree and fake commands; ``calls`` lists what ran."""

    def __init__(self, base: Path) -> None:
        self.root = base / "root"
        self.fakes = base / "fakes"
        self.calls = base / "calls.log"
        self.calls.write_text("")
        record = f'echo "$(basename "$0") $*" >>{self.calls}\n'
        bin_dir = self.root / "opt" / "classora" / "bin"
        bin_dir.mkdir(parents=True)
        shutil.copy(HOST_BIN / "backup-common.sh", bin_dir / "backup-common.sh")
        _executable(bin_dir / "compose.sh", record)
        _executable(bin_dir / "db-roles.sh", record)
        for name in ("aws", "flock", "install", "journalctl"):
            _executable(self.fakes / name, record)
        _executable(self.fakes / "id", "echo 0\n")
        _executable(
            self.fakes / "systemctl",
            record + 'exit "${FAKE_SYSTEMCTL_EXIT:-0}"\n',
        )
        (self.root / "etc" / "classora").mkdir(parents=True)
        self.data = self.root / "srv" / "classora-data"
        self.cache = self.data / "dev-data"

    def host_env(self, env_name: str | None) -> None:
        lines = ["AWS_REGION=us-east-2", "OPS_BUCKET=classora-dev-ops-x"]
        if env_name is not None:
            lines.insert(0, f"CLASSORA_ENV={env_name}")
        (self.root / "etc" / "classora" / "host.env").write_text(
            "\n".join(lines) + "\n"
        )

    def release(self, sha: str = SHA) -> None:
        releases = self.data / "releases"
        (releases / sha).mkdir(parents=True)
        (releases / sha / "images.env").write_text("")
        (releases / "current").symlink_to(releases / sha)

    def marker(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "ENABLED").write_text("")

    def cached_dump(self, sha: str = SHA) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{sha}.dev-data.dump").write_bytes(b"PGDMP")
        (self.cache / f"{sha}.dev-data.counts.json").write_text(
            '{"tables": {"public.users": 1}}'
        )

    def run(self, script: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
        environment = {
            "PATH": f"{self.fakes}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "CLASSORA_TEST_ROOT": str(self.root),
            **env,
        }
        return subprocess.run(  # noqa: S603 - a fixed script, no shell
            [str(BASH), str(script), *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def called(self) -> list[str]:
        return [line for line in self.calls.read_text().splitlines() if line]

    def tree(self) -> dict[str, tuple[int, int]]:
        return {
            str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in self.root.rglob("*")
            if p.is_file()
        }


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


# --- dev-data-reset.sh: refusals ------------------------------------------------------


@pytest.mark.parametrize("env_name", ["prod", "monitoring", "", "DEV", None])
@pytest.mark.parametrize("args", [(), ("--check",)])
def test_reset_refuses_every_host_but_dev_before_anything(host, env_name, args):
    host.host_env(env_name)
    host.release()
    host.marker()
    host.cached_dump()
    before = host.tree()

    result = host.run(RESET, *args)

    assert result.returncode == 2
    assert "not dev" in result.stderr
    assert result.stdout == ""
    assert host.called() == []
    assert host.tree() == before


def test_reset_refuses_without_host_env(host):
    result = host.run(RESET, "--check")

    assert result.returncode == 2
    assert "this host is 'unknown', not dev" in result.stderr
    assert host.called() == []


@pytest.mark.parametrize("args", [("--force",), ("--check", "x"), ("check",)])
def test_reset_usage_errors_exit_2(host, args):
    host.host_env("dev")

    result = host.run(RESET, *args)

    assert result.returncode == 2
    assert result.stderr.startswith("usage: dev-data-reset.sh [--check]")
    assert host.called() == []


def test_a_real_run_without_the_marker_is_refused(host):
    host.host_env("dev")
    host.release()
    host.cached_dump()
    before = host.tree()

    result = host.run(RESET)

    assert result.returncode == 2
    assert "ENABLED is missing" in result.stderr
    assert host.called() == []
    assert host.tree() == before


# --- dev-data-reset.sh --check: a dry run ---------------------------------------------


def test_check_plans_the_cached_dump_and_changes_nothing(host):
    host.host_env("dev")
    host.release()
    host.marker()
    host.cached_dump()
    before = host.tree()

    result = host.run(RESET, "--check")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "nothing is changed" in out
    assert f"release:  {SHA}" in out
    assert f"cached {host.cache}/{SHA}.dev-data.dump" in out
    assert "present" in out
    assert host.called() == []  # no compose, no aws, no lock
    assert host.tree() == before


def test_check_plans_the_download_when_nothing_is_cached(host):
    host.host_env("dev")
    host.release()
    before = host.tree()

    result = host.run(RESET, "--check")

    assert result.returncode == 0, result.stderr
    assert "MISSING (the unit is skipped" in result.stdout
    assert (
        f"download s3://classora-dev-ops-x/releases/{SHA}.dev-data.dump"
        in result.stdout
    )
    assert host.called() == [
        "aws s3api head-object --region us-east-2 --bucket classora-dev-ops-x"
        f" --key releases/{SHA}.dev-data.dump"
    ]
    assert host.tree() == before


def test_check_falls_back_to_an_older_cached_dump(host):
    host.host_env("dev")
    host.release()
    host.marker()
    host.cached_dump(OLD_SHA)
    _executable(host.fakes / "aws", "exit 1\n")  # not in the bucket

    result = host.run(RESET, "--check")

    assert result.returncode == 0, result.stderr
    assert f"fallback: the newest cached dump {host.cache}/{OLD_SHA}" in result.stdout


def test_check_without_any_dump_exits_1(host):
    host.host_env("dev")
    host.release()
    host.marker()
    _executable(host.fakes / "aws", "exit 1\n")

    result = host.run(RESET, "--check")

    assert result.returncode == 1
    assert "dump:     NONE" in result.stdout


# --- manage.sh reset-dev-data ---------------------------------------------------------


@pytest.mark.parametrize("env_name", ["prod", "monitoring", None])
def test_manage_reset_dev_data_refuses_other_hosts(host, env_name):
    host.host_env(env_name)
    host.marker()

    result = host.run(MANAGE, "reset-dev-data")

    assert result.returncode == 2
    assert "runs on DEV only" in result.stderr
    assert host.called() == []


def test_manage_reset_dev_data_refuses_without_host_env(host):
    result = host.run(MANAGE, "reset-dev-data")

    assert result.returncode == 2
    assert "this host is 'unknown'" in result.stderr


def test_manage_reset_dev_data_needs_the_marker(host):
    host.host_env("dev")

    result = host.run(MANAGE, "reset-dev-data")

    assert result.returncode == 3
    assert "ENABLED is missing" in result.stderr
    assert host.called() == []


@pytest.mark.parametrize("unit_exit", ["0", "3"])
def test_manage_reset_dev_data_starts_the_unit_and_prints_its_line(host, unit_exit):
    host.host_env("dev")
    host.marker()
    line = '{"event": "dev-data-reset", "env": "dev", "status": "ok"}'
    _executable(
        host.fakes / "journalctl",
        f'echo "$(basename "$0") $*" >>{host.calls}\necho "noise"\necho \'{line}\'\n',
    )

    result = host.run(MANAGE, "reset-dev-data", FAKE_SYSTEMCTL_EXIT=unit_exit)

    assert result.returncode == int(unit_exit)
    assert result.stdout.strip() == line
    calls = host.called()
    assert calls[0] == "systemctl start --wait classora-dev-data-reset.service"
    assert calls[1].startswith("journalctl -u classora-dev-data-reset.service --since ")


def test_manage_reset_dev_data_takes_no_argument(host):
    host.host_env("dev")
    host.marker()

    result = host.run(MANAGE, "reset-dev-data", "now")

    assert result.returncode == 2
    assert "reset-dev-data" in result.stderr  # the usage line
    assert host.called() == []


# --- install-units.sh: the reset units on DEV only ------------------------------------


def _reset_unit_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if "dev-data-reset" in c]


def test_install_units_installs_and_enables_the_reset_timer_on_dev(host):
    host.host_env("dev")
    systemd = host.root / "opt" / "classora" / "systemd"
    systemd.mkdir(parents=True)
    for name in ("classora-dev-data-reset.service", "classora-dev-data-reset.timer"):
        (systemd / name).write_text("")

    result = host.run(INSTALL_UNITS)

    assert result.returncode == 0, result.stderr
    assert _reset_unit_calls(host.called()) == [
        f"install -m 0644 {systemd}/classora-dev-data-reset.service"
        f" {systemd}/classora-dev-data-reset.timer /etc/systemd/system/",
        "systemctl enable --now classora-dev-data-reset.timer",
    ]


@pytest.mark.parametrize("env_name", ["prod", "monitoring", None])
def test_install_units_never_installs_the_reset_units_elsewhere(host, env_name):
    host.host_env(env_name)
    # The release copies exist, so only the environment check keeps them out.
    systemd = host.root / "opt" / "classora" / "systemd"
    systemd.mkdir(parents=True)
    for name in ("classora-dev-data-reset.service", "classora-dev-data-reset.timer"):
        (systemd / name).write_text("")

    result = host.run(INSTALL_UNITS)

    assert result.returncode == 0, result.stderr
    assert _reset_unit_calls(host.called()) == []
    assert "systemctl enable --now classora-backup.timer" in host.called()


def test_install_units_on_dev_before_the_first_release_skips_the_reset(host):
    host.host_env("dev")  # /opt/classora/systemd has no reset units here

    result = host.run(INSTALL_UNITS)

    assert result.returncode == 0, result.stderr
    assert _reset_unit_calls(host.called()) == []


# --- the units, the workflows and the grants ------------------------------------------


def test_the_units_run_the_script_at_04_utc_behind_the_marker():
    systemd = ROOT / "infrastructure" / "host" / "systemd"
    service = (systemd / "classora-dev-data-reset.service").read_text()
    timer = (systemd / "classora-dev-data-reset.timer").read_text()
    assert "ConditionPathExists=/srv/classora-data/dev-data/ENABLED" in service
    assert "ExecStart=/opt/classora/bin/dev-data-reset.sh\n" in service
    assert "Type=oneshot" in service
    assert "OnCalendar=*-*-* 04:00:00 UTC" in timer
    assert "RandomizedDelaySec=300" in timer
    assert "Persistent=true" in timer


def test_release_prod_never_references_the_dev_data():
    workflow = (ROOT / ".github" / "workflows" / "release-prod.yml").read_text()
    assert "dev-data" not in workflow
    assert "visitor" not in workflow


def test_deploy_dev_builds_the_dump_before_the_deploy():
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()
    assert "\n  dev-data:\n" in workflow
    assert "needs: [probe, build, dev-data]" in workflow
    assert 'releases/$SHA.dev-data.dump"' in workflow
    assert 'releases/$SHA.dev-data.counts.json"' in workflow
    assert "--language en --seed 1" in workflow
    assert "python -m tests.factories.simulation.visitor" in workflow
    assert "APP_ENV: dev" in workflow


def _statements(sql: str) -> set[str]:
    return {" ".join(s.split()) for s in sql.split(";") if s.strip()}


def test_the_reset_grants_are_migration_0096s():
    script = RESET.read_text()
    block = script[script.index('<<SQL || fail "could not grant') :]
    block = block[block.index("\n") + 1 : block.index("\nSQL\n")]
    granted = _statements(block.replace("$database", "classora"))

    spec = importlib.util.spec_from_file_location(
        "migration_0096",
        ROOT / "apps" / "api" / "migrations" / "versions" / "0096_runtime_db_role.py",
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # The role creation and the database grants are DO blocks; the reset
    # writes the latter out for `classora` (the role exists cluster-wide).
    pinned = {
        " ".join(statement.split())
        for statement in migration.UPGRADE
        if not statement.lstrip().startswith("DO $$")
    }
    pinned |= {
        "GRANT CONNECT ON DATABASE classora TO classora_app",
        "REVOKE TEMPORARY ON DATABASE classora FROM PUBLIC",
        "ANALYZE",
    }
    assert "GRANT CONNECT ON DATABASE %I TO classora_app" in migration.DATABASE_GRANTS
    assert "REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC" in migration.DATABASE_GRANTS
    assert granted == pinned
