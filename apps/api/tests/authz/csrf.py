"""CSRF helpers shared by the API tests (#24).

Test clients send :data:`ORIGIN_HEADER` by default (``TestClient(...,
headers=ORIGIN_HEADER)``), so the ``Origin`` check never hides what a test is
about. A CSRF test removes the header with :func:`send_without_origin` or
replaces it per request.
"""

from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Headers, Response

TEST_APP_ORIGIN: Final = "http://127.0.0.1:5173"
"""``Settings.app_origin`` in the tests (the local default of ``.env.example``)."""

PROD_APP_ORIGIN: Final = "https://app.classora.example"
"""An ``https`` app origin for ``APP_ENV`` ``dev``/``prod`` (``http`` is local only)."""

ORIGIN_HEADER: Final = {"Origin": TEST_APP_ORIGIN}
CSRF_HEADER: Final = "X-CSRF-Token"


def origin_for(app_env: str) -> str:
    """A valid ``APP_ORIGIN`` for ``app_env``."""
    return TEST_APP_ORIGIN if app_env == "local" else PROD_APP_ORIGIN


def csrf_header(token: str) -> dict[str, str]:
    return {CSRF_HEADER: token}


def send_without_origin(
    client: TestClient, method: str, url: str, **kwargs: Any
) -> Response:
    """Send a request with no ``Origin`` header, even if the client's default
    headers carry one."""
    request = client.build_request(method, url, **kwargs)
    del request.headers["origin"]
    return client.send(request)


def send_with_headers(
    client: TestClient,
    method: str,
    url: str,
    headers: list[tuple[str, str]],
    **kwargs: Any,
) -> Response:
    """Send a request carrying exactly ``headers`` for the names it lists
    (a name given twice is sent twice); the client's other headers stay."""
    request = client.build_request(method, url, **kwargs)
    names = {name.lower() for name, _ in headers}
    kept = [
        (name, value)
        for name, value in request.headers.multi_items()
        if name.lower() not in names
    ]
    request.headers = Headers([*kept, *headers])
    return client.send(request)
