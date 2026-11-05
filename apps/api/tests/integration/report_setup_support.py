"""Helpers for the saved export setup tests (#125: ``test_report_setups_api.py``,
``test_report_setups_replay.py``)."""

from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import signed_headers
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.export_support import Actor

SETUPS: Final = "/api/v1/report-setups"


def setup_path(setup_id: Any) -> str:
    return f"{SETUPS}/{setup_id}"


def list_setups(client: TestClient, who: Actor) -> Response:
    return get(client, who.cookie, SETUPS)


def listed(client: TestClient, who: Actor) -> list[dict[str, Any]]:
    response = list_setups(client, who)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def post_setup(client: TestClient, who: Actor, body: Any) -> Response:
    client.cookies.clear()
    return client.post(SETUPS, json=body, headers=signed_headers(who.cookie))


def saved(
    client: TestClient,
    who: Actor,
    name: str,
    parameters: dict[str, Any],
    shared_role: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "parameters": parameters}
    if shared_role is not None:
        body["shared_role"] = shared_role
    response = post_setup(client, who, body)
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()
    return item


def patch_setup(client: TestClient, who: Actor, setup_id: Any, body: Any) -> Response:
    client.cookies.clear()
    return client.patch(
        setup_path(setup_id), json=body, headers=signed_headers(who.cookie)
    )


def delete_setup(client: TestClient, who: Actor, setup_id: Any) -> Response:
    client.cookies.clear()
    return client.delete(setup_path(setup_id), headers=signed_headers(who.cookie))


def stored_row(url: str, setup_id: Any) -> Any:
    rows = run_sql(
        url, "SELECT * FROM report_setups WHERE id = :i", {"i": str(setup_id)}
    )
    return rows[0] if rows else None


def setup_audit(url: str, setup_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT action, actor_user_id, changes FROM audit_events"
        " WHERE record_type = 'report_setups' AND record_id = :i ORDER BY id",
        {"i": str(setup_id)},
    )
