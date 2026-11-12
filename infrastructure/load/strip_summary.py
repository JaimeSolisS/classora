"""Remove k6's ``setup_data`` from a summary export, in place (#152).

    python3 infrastructure/load/strip_summary.py <k6 summary json>

``setup()`` returns the shared sessions (cookie, CSRF token, and the staff
passwords kept for reauthentication), and ``--summary-export`` writes the
setup data next to the metrics. ``make load-test`` runs this after every run,
so no credential stays on disk. Standard library only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def strip(summary: dict) -> dict:
    """The summary without ``setup_data`` (metrics and groups kept)."""
    return {key: value for key, value in summary.items() if key != "setup_data"}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: strip_summary.py <k6 summary json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    path.write_text(json.dumps(strip(json.loads(path.read_text()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
