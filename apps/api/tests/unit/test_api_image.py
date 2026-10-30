"""The API image command (#27 C4): uvicorn must not rewrite the client address
from ``X-Forwarded-For``; only ``app.core.client_ip`` reads it. It also bounds
the graceful shutdown, so open notification streams can't hang a stop (#42
C5)."""

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


def test_the_api_command_bounds_the_graceful_shutdown():
    command = image_command()

    position = command.index("--timeout-graceful-shutdown")
    # Below Docker's default 10 s stop timeout, so the lifespan shutdown runs.
    assert 0 < int(command[position + 1]) < 10


PDF_PACKAGES = {
    "libpango-1.0-0",
    "libpangoft2-1.0-0",
    "libharfbuzz-subset0",
    "fonts-dejavu-core",
}
CI_WORKFLOW = DOCKERFILE.parents[2] / ".github/workflows/ci.yml"


def apt_packages(text: str) -> list[set[str]]:
    """The package names of each ``apt-get install`` command in ``text``."""
    return [
        {word for word in match.split() if not word.startswith("-")}
        for match in re.findall(r"apt-get install ([^\\\n&]+)", text)
    ]


def test_the_image_installs_the_pdf_libraries():
    # ADR-0015 (#98a B1): WeasyPrint's Pango, HarfBuzz subsetting and fonts.
    [packages] = apt_packages(DOCKERFILE.read_text())
    assert packages == {"libpq5", *PDF_PACKAGES}


def test_ci_installs_the_same_pdf_libraries():
    # #98a B2: api-unit and the api-integration matrix (one step each).
    installs = apt_packages(CI_WORKFLOW.read_text())
    assert installs.count(PDF_PACKAGES) == 2
