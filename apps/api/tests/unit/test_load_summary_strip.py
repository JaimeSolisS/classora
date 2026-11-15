"""The load test's k6 summary keeps no credential (#152, M10-04).

``infrastructure/load/strip_summary.py`` runs after every ``make
load-test``: k6 writes ``setup()``'s return value (session cookies, CSRF
tokens, staff passwords) into ``--summary-export``.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infrastructure" / "load" / "strip_summary.py"


def _module():
    spec = importlib.util.spec_from_file_location("strip_summary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary() -> dict:
    session = {
        "username": "finanzas",
        "password": "secret-password-value",
        "csrf": "c" * 43,
        "cookieName": "__Host-classora_session",
        "cookie": "cookie-token-value",
    }
    return {
        "root_group": {"name": "", "checks": []},
        "metrics": {"http_reqs": {"count": 10}},
        "setup_data": {"sessions": {"staff": [session]}},
    }


def test_strip_drops_the_setup_data_and_keeps_the_metrics() -> None:
    stripped = _module().strip(_summary())

    assert set(stripped) == {"root_group", "metrics"}
    assert stripped["metrics"] == {"http_reqs": {"count": 10}}


def test_the_command_rewrites_the_file_without_any_credential(tmp_path: Path) -> None:
    path = tmp_path / "k6-summary.json"
    path.write_text(json.dumps(_summary()))

    assert _module().main(["strip_summary.py", str(path)]) == 0

    text = path.read_text()
    for secret in ("secret-password-value", "cookie-token-value", "c" * 43):
        assert secret not in text
    for key in ("setup_data", "password", "csrf", "cookie"):
        assert key not in text


def test_make_load_test_runs_the_strip_after_k6() -> None:
    makefile = (ROOT / "Makefile").read_text()
    target = makefile[makefile.index("\nload-test:") :].split("\n\n", 1)[0]

    assert "infrastructure/load/strip_summary.py" in target
    assert target.index("--summary-export") < target.index("strip_summary.py")
