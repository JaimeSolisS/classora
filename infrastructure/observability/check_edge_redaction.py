"""Edge access-log redaction check (`make check-observability` step 7; #617 QA D1).

  check_edge_redaction.py fixture <containers dir>   write a Docker json-file log
  check_edge_redaction.py verify <logs.json>         check the collector's output

check.sh runs the agent's real logs pipeline (collector/agent.yaml plus
collector/check/edge-redaction.yaml) between the two. The edge's lines must
keep their status, method and duration but lose the URI (query and path),
every header, the client IPs and the response headers; other containers'
lines pass unchanged.
"""

import json
import sys
from pathlib import Path

CONTAINER_ID = "e" * 64
EMAIL = "zz-probe@example.com"
IP = "203.0.113.7"
FORBIDDEN = (
    EMAIL,
    "example.com",
    "EDGEMARK",
    IP,
    "headers",
    "uri",
    "remote_ip",
    "client_ip",
)


def caddy_line(logger: str, msg: str, status: int) -> dict:
    return {
        "level": "info" if status < 500 else "error",
        "ts": 1794462983.123,
        "logger": logger,
        "msg": msg,
        "request": {
            "remote_ip": IP,
            "remote_port": "51234",
            "client_ip": IP,
            "proto": "HTTP/2.0",
            "method": "GET",
            "host": "dev.classora.dev",
            "uri": f"/api/v1/users/{EMAIL}/EDGEMARK?q=EDGEMARK&email={EMAIL}",
            "headers": {"User-Agent": ["EDGEMARK-UA"], "X-Note": [EMAIL]},
            "tls": {"server_name": "dev.classora.dev"},
        },
        "bytes_read": 0,
        "user_id": EMAIL,
        "duration": 0.0123,
        "size": 12,
        "status": status,
        "resp_headers": {"Location": [f"/login?email={EMAIL}"]},
    }


def docker_line(service: str, log: str) -> str:
    return json.dumps(
        {
            "log": log + "\n",
            "stream": "stderr",
            "attrs": {"com.docker.compose.service": service},
            "time": "2026-11-12T06:00:00.000000000Z",
        }
    )


def fixture(containers: Path) -> None:
    directory = containers / CONTAINER_ID
    directory.mkdir(parents=True)
    lines = [
        docker_line(
            "edge",
            json.dumps(caddy_line("http.log.access.log0", "handled request", 404)),
        ),
        docker_line(
            "edge", json.dumps(caddy_line("http.log.error", "dial tcp: refused", 502))
        ),
        docker_line("edge", f"GET uri=/login?email={EMAIL} remote_ip={IP} EDGEMARK"),
        docker_line("db", "LOG:  checkpoint complete DBMARK"),
    ]
    path = directory / f"{CONTAINER_ID}-json.log"
    path.write_text("\n".join(lines) + "\n")
    directory.chmod(0o755)
    path.chmod(0o644)


def bodies(output: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for raw in output.read_text().splitlines():
        for resource_logs in json.loads(raw).get("resourceLogs", []):
            attributes = {
                a["key"]: a["value"].get("stringValue")
                for a in resource_logs["resource"].get("attributes", [])
            }
            name = attributes.get("container.name") or "?"
            for scope_logs in resource_logs.get("scopeLogs", []):
                for record in scope_logs.get("logRecords", []):
                    found.setdefault(name, []).append(
                        json.dumps(record.get("body", {}))
                    )
    return found


def verify(output: Path) -> int:
    found = bodies(output)
    edge, db = found.get("edge", []), found.get("db", [])
    errors = []
    if len(edge) != 3:
        errors.append(f"expected 3 edge lines, got {len(edge)}")
    if len(db) != 1 or "DBMARK" not in db[0]:
        errors.append(f"the db line must pass unchanged: {db}")
    for body in edge:
        for marker in FORBIDDEN:
            if marker in body:
                errors.append(f"edge line still contains {marker!r}: {body}")
    structured = [b for b in edge if "kvlistValue" in b]
    if len(structured) != 2 or not all(
        '"GET"' in b and "duration" in b for b in structured
    ):
        errors.append(
            f"the two request lines must keep method, status and duration: {edge}"
        )
    if not any("not parseable" in b for b in edge):
        errors.append(f"the unparsed request line must be replaced: {edge}")
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("edge: 3 lines without URI, headers or IPs; db line unchanged")
    return 1 if errors else 0


def main() -> int:
    command, path = sys.argv[1], Path(sys.argv[2])
    if command == "fixture":
        fixture(path)
        return 0
    return verify(path)


if __name__ == "__main__":
    sys.exit(main())
