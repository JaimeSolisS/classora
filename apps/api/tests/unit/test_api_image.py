"""The API image command (#27 C4): uvicorn must not rewrite the client address
from ``X-Forwarded-For``; only ``app.core.client_ip`` reads it."""

import json
import re
from pathlib import Path

DOCKERFILE = (
    Path(__file__).resolve().parents[4] / "infrastructure/docker/api.Dockerfile"
)


def image_command() -> list[str]:
    [line] = [
        line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD ")
    ]
    command: list[str] = json.loads(line.removeprefix("CMD "))
    return command


def test_the_api_command_disables_uvicorn_proxy_headers():
    command = image_command()

    assert command[:3] == ["uvicorn", "--factory", "app.main:create_app"]
    assert "--no-proxy-headers" in command
    assert not any(
        re.match(r"--(proxy-headers|forwarded-allow-ips)", a) for a in command
    )
