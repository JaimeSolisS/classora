"""Reusable authorization assertions (M2-03).

IDOR tests (#33-#35 and later) compare the response for a record the caller
may not see with the response for a record that does not exist.
"""

from httpx import Response

from app.core.errors import PROBLEM_JSON


def assert_same_not_found(
    hidden_response: Response, missing_response: Response
) -> None:
    """A hidden record is indistinguishable from a missing one.

    Both are ``404`` Problem Details with code ``not_found``, their bodies are
    identical apart from ``request_id``, and they carry the same header names
    with the same ``Content-Type``.
    """
    for name, response in (("hidden", hidden_response), ("missing", missing_response)):
        assert response.status_code == 404, f"{name}: {response.status_code}"
        assert response.headers.get("content-type") == PROBLEM_JSON, name
        assert response.json().get("code") == "not_found", f"{name}: {response.text}"
    hidden_body = {k: v for k, v in hidden_response.json().items() if k != "request_id"}
    missing_body = {
        k: v for k, v in missing_response.json().items() if k != "request_id"
    }
    assert hidden_body == missing_body
    assert {h.lower() for h in hidden_response.headers} == {
        h.lower() for h in missing_response.headers
    }
    assert (
        hidden_response.headers["content-type"]
        == missing_response.headers["content-type"]
    )
