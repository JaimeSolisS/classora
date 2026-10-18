"""Export the API's OpenAPI document to a file (spec §10 API contracts).

Run from ``apps/api``::

    uv run python -m app.core.openapi ../../packages/api-client/openapi.json

``make api-client`` runs this and then regenerates the TypeScript client.
The document comes from :func:`app.main.build_app`, the builder that
``create_app()`` uses, so it has the same routes and conventions as the running
app. The export needs no configuration: it never reads settings, starts no
server, runs no lifespan and opens no database connection. The local/DEV demo
job route is not part of the contract and is left out.

Output is deterministic: two-space indented UTF-8 JSON with a trailing newline,
and nothing taken from the environment, git or the clock.
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.main import build_app


def build_document() -> dict[str, Any]:
    """The OpenAPI document of the contract (no dev-only routes)."""
    return build_app().openapi()


def render_document(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def export(path: Path) -> None:
    path.write_text(render_document(build_document()), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.core.openapi <output path>", file=sys.stderr)
        return 2
    export(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
