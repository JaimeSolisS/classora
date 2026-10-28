"""Idempotency keys through HTTP on real PostgreSQL (#88 B, C; ADR-0006 E6).

The routes are the test-only router of ``tests/integration/idempotency_app.py``
on the real app (``build_app()``), so the Origin, session, CSRF, privacy
notice and permission checks run for real. Each run of a route writes one
``test.idempotent_effect`` audit event (the **effect**); the tests count
them by label.

The concurrency tests (C7-C9) send two HTTP requests at once (two pooled
connections). Request A claims its key, writes its effect and keeps its
transaction open; request B starts once A is holding, and must still be
pending while A holds. Each case runs 5 times with new keys.
"""

import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.idempotency import (
    IDEMPOTENCY_HEADER,
    REPLAYED_HEADER,
    RETENTION,
    request_hash,
)
from app.core.models import StaleVersionError
from tests.authz.csrf import ORIGIN_HEADER
from tests.conftest import CLOCK_START
from tests.integration.auth_support import (
    auth_client,
    create_user,
    login,
    only_set_cookie,
    signed_headers,
)
from tests.integration.conftest import run_sql
from tests.integration.idempotency_app import (
    EFFECT_ACTION,
    PREFIX,
    Controls,
    SampleConflictError,
    build_idempotency_router,
)

REPEATS = 5
PENDING_CHECK_SECONDS = 0.5
WAIT_SECONDS = 10.0
ITEM = uuid.UUID("0199a000-0000-7000-8000-0000000000a1")
OTHER_ITEM = uuid.UUID("0199a000-0000-7000-8000-0000000000a2")
MICROSECOND = timedelta(microseconds=1)
CONTROLS = Controls()


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(
        module_seeded_url, routers=[build_idempotency_router(CONTROLS)]
    ) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_controls() -> Iterator[None]:
    CONTROLS.reset()
    yield
    if CONTROLS.hold is not None:
        CONTROLS.hold.set()
    CONTROLS.reset()


def new_key() -> str:
    return str(uuid.uuid4())


def new_label() -> str:
    return f"effect-{uuid.uuid4().hex}"


def body(label: str, **extra: Any) -> dict[str, Any]:
    return {"label": label, "amount": "1250.50", "lines": ["a", "b"], **extra}


class User:
    """A committed user and a way to sign in as them."""

    def __init__(self, url: str, roles: tuple[str, ...] = ("owner",)) -> None:
        created = create_user(url, roles=roles)
        self.id = created.id
        self.username = created.username

    def headers(self, client: TestClient) -> dict[str, str]:
        """The headers of a fresh session (each call logs in again)."""
        response = login(client, self.username)
        assert response.status_code == 200, response.text
        cookie = only_set_cookie(response)
        client.cookies.clear()
        return signed_headers(cookie)


@pytest.fixture
def owner(module_seeded_url: str) -> User:
    return User(module_seeded_url)


def post(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    json: Any,
    key: str | None,
) -> Response:
    sent = dict(headers)
    if key is not None:
        sent[IDEMPOTENCY_HEADER] = key
    return client.post(f"{PREFIX}{path}", json=json, headers=sent)


def effects(url: str, label: str) -> int:
    rows = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE action = :action AND details->>'label' = :label",
        {"action": EFFECT_ACTION, "label": label},
    )
    return int(rows[0].n)


def key_rows(url: str, key: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT user_id, endpoint, request_hash, response_status, response_body,"
        " expires_at, created_at FROM idempotency_keys WHERE key = :key"
        " ORDER BY endpoint, user_id",
        {"key": key},
    )


def assert_problem(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code
    assert REPLAYED_HEADER not in response.headers


# --- C2: replay -----------------------------------------------------------------------


def test_a_retry_returns_the_first_response_without_running_again(
    client, module_seeded_url, owner
):
    headers, key, label = owner.headers(client), new_key(), new_label()

    first = post(client, f"/items/{ITEM}", headers, body(label), key)
    # The same body with its keys in another order.
    reordered = {"lines": ["a", "b"], "amount": "1250.50", "label": label}
    second = post(client, f"/items/{ITEM}", headers, reordered, key)

    assert first.status_code == 201, first.text
    assert REPLAYED_HEADER not in first.headers
    assert first.json()["amount"] == "1250.50"
    assert second.status_code == 201
    assert second.headers[REPLAYED_HEADER] == "true"
    assert second.headers["content-type"] == "application/json"
    assert second.json() == first.json()
    assert second.headers["x-request-id"] != first.headers["x-request-id"]
    assert effects(module_seeded_url, label) == 1
    [row] = key_rows(module_seeded_url, key)
    assert row.user_id == owner.id
    assert row.endpoint == f"POST {PREFIX}/items/{{item_id}}"
    assert bytes(row.request_hash) == request_hash(
        "POST",
        f"{PREFIX}/items/{ITEM}",
        b'{"label":"' + label.encode() + b'","amount":"1250.50","lines":["a","b"]}',
    )
    assert row.response_status == 201
    assert row.response_body == first.json()


def test_a_replay_from_another_session_of_the_same_user(
    client, module_seeded_url, owner
):
    key, label = new_key(), new_label()

    first = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)
    second = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    assert first.status_code == 201
    assert second.headers[REPLAYED_HEADER] == "true"
    assert effects(module_seeded_url, label) == 1


def test_a_204_replays_as_204_without_a_body(client, module_seeded_url, owner):
    headers, key, label = owner.headers(client), new_key(), new_label()

    first = post(client, f"/items/{ITEM}/archive", headers, body(label), key)
    second = post(client, f"/items/{ITEM}/archive", headers, body(label), key)

    assert first.status_code == 204
    assert first.content == b""
    assert second.status_code == 204
    assert second.content == b""
    assert second.headers[REPLAYED_HEADER] == "true"
    assert effects(module_seeded_url, label) == 1
    [row] = key_rows(module_seeded_url, key)
    assert (row.response_status, row.response_body) == (204, None)


# --- C3: mismatch ---------------------------------------------------------------------


def test_the_same_key_with_another_body_or_path_is_refused(
    client, module_seeded_url, owner, caplog
):
    headers, key, label = owner.headers(client), new_key(), new_label()
    first = post(client, f"/items/{ITEM}", headers, body(label), key)
    [before] = key_rows(module_seeded_url, key)
    caplog.set_level("INFO", logger="app.security")

    other_body = post(client, f"/items/{ITEM}", headers, body(label, lines=[]), key)
    other_path = post(client, f"/items/{OTHER_ITEM}", headers, body(label), key)

    assert first.status_code == 201
    assert_problem(other_body, 422, "idempotency_key_reused")
    assert_problem(other_path, 422, "idempotency_key_reused")
    assert effects(module_seeded_url, label) == 1
    assert key_rows(module_seeded_url, key) == [before]
    warnings = [
        r
        for r in caplog.records
        if r.name == "app.security" and r.getMessage().startswith("Idempotency")
    ]
    assert [r.levelname for r in warnings] == ["WARNING", "WARNING"]
    for record in warnings:
        assert record.endpoint == f"POST {PREFIX}/items/{{item_id}}"  # type: ignore[attr-defined]
        assert key not in f"{record.getMessage()} {record.__dict__}"
        assert label not in f"{record.getMessage()} {record.__dict__}"


def test_a_replay_logs_one_info_line_without_the_key_or_the_body(
    client, module_seeded_url, owner, caplog
):
    headers, key, label = owner.headers(client), new_key(), new_label()
    post(client, f"/items/{ITEM}", headers, body(label), key)
    caplog.set_level("INFO", logger="app.security")

    post(client, f"/items/{ITEM}", headers, body(label), key)

    [record] = [
        r
        for r in caplog.records
        if r.name == "app.security" and "replayed" in r.getMessage()
    ]
    assert record.levelname == "INFO"
    assert record.method == "POST"  # type: ignore[attr-defined]
    assert record.path == f"{PREFIX}/items/{ITEM}"  # type: ignore[attr-defined]
    text = f"{record.getMessage()} {record.__dict__}"
    for secret in (key, label, "1250.50"):
        assert secret not in text
    [row] = key_rows(module_seeded_url, key)
    assert bytes(row.request_hash).hex() not in text


# --- C4: missing or bad key -----------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "code"),
    [
        (None, "idempotency_key_required"),
        ("", "idempotency_key_required"),
        ("a" * 15, "idempotency_key_invalid"),
        ("a" * 129, "idempotency_key_invalid"),
        ("a" * 15 + "!", "idempotency_key_invalid"),
        ("a" * 15 + ".", "idempotency_key_invalid"),
    ],
)
def test_a_missing_or_malformed_key_is_400(client, module_seeded_url, owner, key, code):
    label = new_label()

    response = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    assert_problem(response, 400, code)
    assert effects(module_seeded_url, label) == 0
    assert (
        run_sql(
            module_seeded_url,
            "SELECT count(*) FROM idempotency_keys WHERE user_id = :u",
            {"u": owner.id},
        )[0][0]
        == 0
    )


def test_the_bounds_of_the_key_format_are_accepted(client, module_seeded_url, owner):
    headers = owner.headers(client)
    for key in ("a" * 16, "Z9_-" * 32):
        label = new_label()
        response = post(client, f"/items/{ITEM}", headers, body(label), key)
        assert response.status_code == 201, response.text


def test_the_header_twice_is_invalid(client, module_seeded_url, owner):
    label, key = new_label(), new_key()
    headers = owner.headers(client)
    request = client.build_request(
        "POST",
        f"{PREFIX}/items/{ITEM}",
        json=body(label),
        headers=[
            *headers.items(),
            (IDEMPOTENCY_HEADER, key),
            (IDEMPOTENCY_HEADER, key),
        ],
    )

    response = client.send(request)

    assert_problem(response, 400, "idempotency_key_invalid")
    assert effects(module_seeded_url, label) == 0
    assert key_rows(module_seeded_url, key) == []


def test_a_missing_key_is_400_even_when_the_body_is_invalid(client, owner):
    response = post(client, f"/items/{ITEM}", owner.headers(client), {"x": 1}, None)

    assert_problem(response, 400, "idempotency_key_required")


def test_an_invalid_body_with_a_key_is_422_and_leaves_no_key(
    client, module_seeded_url, owner
):
    key = new_key()

    response = post(client, f"/items/{ITEM}", owner.headers(client), {"x": 1}, key)

    assert_problem(response, 422, "validation_error")
    assert key_rows(module_seeded_url, key) == []


# --- C5: order of checks --------------------------------------------------------------


def test_anonymous_gets_401_before_the_key_check(client):
    client.cookies.clear()

    response = post(client, f"/items/{ITEM}", dict(ORIGIN_HEADER), body("x"), None)

    assert_problem(response, 401, "authentication_required")


def test_a_user_without_the_permission_gets_403_even_on_a_replay(
    client, module_seeded_url, owner
):
    tutor = User(module_seeded_url, roles=("tutor",))
    key, label = new_key(), new_label()
    first = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)
    tutor_headers = tutor.headers(client)

    without_key = post(client, f"/items/{ITEM}", tutor_headers, body(label), None)
    replay = post(client, f"/items/{ITEM}", tutor_headers, body(label), key)

    assert first.status_code == 201
    assert_problem(without_key, 403, "permission_denied")
    assert_problem(replay, 403, "permission_denied")
    assert effects(module_seeded_url, label) == 1


def test_no_origin_is_403_csrf_failed_before_400(client, owner):
    headers = owner.headers(client)
    request = client.build_request(
        "POST", f"{PREFIX}/items/{ITEM}", json=body("x"), headers=headers
    )
    del request.headers["origin"]

    response = client.send(request)

    assert_problem(response, 403, "csrf_failed")


def test_no_csrf_token_is_403_csrf_failed_before_400(client, owner):
    headers = owner.headers(client)
    headers.pop("X-CSRF-Token")

    response = post(client, f"/items/{ITEM}", headers, body("x"), None)

    assert_problem(response, 403, "csrf_failed")


# --- C6, B8: a failure releases the key -----------------------------------------------


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (None, 409, "sample_conflict"),
        (StaleVersionError, 409, "version_conflict"),
        (lambda: RuntimeError("boom"), 500, "internal_error"),
    ],
    ids=["app-error", "stale-version", "crash"],
)
def test_a_failure_leaves_no_key_and_a_retry_runs(
    client, module_seeded_url, owner, failure, status, code
):
    headers, key, label = owner.headers(client), new_key(), new_label()
    CONTROLS.failure = failure or SampleConflictError

    failed = post(client, f"/conflicts/{ITEM}", headers, body(label), key)

    assert_problem(failed, status, code)
    assert effects(module_seeded_url, label) == 0
    assert key_rows(module_seeded_url, key) == []

    CONTROLS.failure = None
    retried = post(client, f"/conflicts/{ITEM}", headers, body(label), key)

    assert retried.status_code == 201, retried.text
    assert REPLAYED_HEADER not in retried.headers
    assert effects(module_seeded_url, label) == 1
    [row] = key_rows(module_seeded_url, key)
    assert row.response_status == 201


# --- C7-C9: concurrent duplicates -----------------------------------------------------


def race(
    client: TestClient,
    path: str,
    first: tuple[dict[str, str], Any],
    second: tuple[dict[str, str], Any],
    key: str,
) -> tuple[Response, Response]:
    """A holds its transaction after writing its effect; B starts then, and
    must still be pending while A holds. Returns both responses."""
    hold = threading.Event()
    CONTROLS.entered = threading.Event()
    CONTROLS.hold = hold
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a: Future[Response] = pool.submit(post, client, path, *first, key)
        assert CONTROLS.entered.wait(WAIT_SECONDS), "A never reached its body"
        future_b: Future[Response] = pool.submit(post, client, path, *second, key)
        time.sleep(PENDING_CHECK_SECONDS)
        b_was_pending = not future_b.done()
        hold.set()
        response_a = future_a.result(WAIT_SECONDS)
        response_b = future_b.result(WAIT_SECONDS)
    assert b_was_pending, f"B did not wait for A: {response_b.status_code}"
    return response_a, response_b


def test_two_concurrent_copies_run_exactly_once(client, module_seeded_url, owner):
    for _ in range(REPEATS):
        key, label = new_key(), new_label()
        a, b = race(
            client,
            f"/items/{ITEM}",
            (owner.headers(client), body(label)),
            (owner.headers(client), body(label)),
            key,
        )

        assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
        assert a.json() == b.json()
        assert REPLAYED_HEADER not in a.headers
        assert b.headers[REPLAYED_HEADER] == "true"
        assert effects(module_seeded_url, label) == 1


def test_when_the_first_copy_fails_the_waiting_copy_runs(
    client, module_seeded_url, owner
):
    for _ in range(REPEATS):
        key, label = new_key(), new_label()
        CONTROLS.failure = SampleConflictError
        CONTROLS.fail_once = True

        a, b = race(
            client,
            f"/conflicts/{ITEM}",
            (owner.headers(client), body(label)),
            (owner.headers(client), body(label)),
            key,
        )

        assert_problem(a, 409, "sample_conflict")
        assert b.status_code == 201, b.text
        assert REPLAYED_HEADER not in b.headers
        assert effects(module_seeded_url, label) == 1
        [row] = key_rows(module_seeded_url, key)
        assert row.response_body == b.json()


def test_a_concurrent_copy_with_another_body_is_refused(
    client, module_seeded_url, owner
):
    for _ in range(REPEATS):
        key, label = new_key(), new_label()
        a, b = race(
            client,
            f"/items/{ITEM}",
            (owner.headers(client), body(label)),
            (owner.headers(client), body(label, lines=["c"])),
            key,
        )

        assert a.status_code == 201, a.text
        assert_problem(b, 422, "idempotency_key_reused")
        assert effects(module_seeded_url, label) == 1


# --- C10: expiry ----------------------------------------------------------------------


def test_an_expired_key_counts_as_new(client, module_seeded_url, owner, clock):
    key, label = new_key(), new_label()
    first = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)
    [claimed] = key_rows(module_seeded_url, key)
    assert claimed.expires_at == CLOCK_START + RETENTION

    clock.set(CLOCK_START + RETENTION - MICROSECOND)
    still_live = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    assert first.status_code == 201
    assert still_live.headers[REPLAYED_HEADER] == "true"
    assert effects(module_seeded_url, label) == 1

    clock.set(CLOCK_START + RETENTION)
    renewed = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    assert renewed.status_code == 201, renewed.text
    assert REPLAYED_HEADER not in renewed.headers
    assert renewed.json()["effect_id"] != first.json()["effect_id"]
    assert effects(module_seeded_url, label) == 2
    [replaced] = key_rows(module_seeded_url, key)
    assert replaced.expires_at == CLOCK_START + 2 * RETENTION
    assert replaced.response_body == renewed.json()


def test_an_expired_key_with_another_body_runs_too(
    client, module_seeded_url, owner, clock
):
    key, label = new_key(), new_label()
    post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    clock.set(CLOCK_START + RETENTION)
    other = post(
        client, f"/items/{ITEM}", owner.headers(client), body(label, lines=[]), key
    )

    assert other.status_code == 201, other.text
    assert effects(module_seeded_url, label) == 2


# --- C11: scope of a key --------------------------------------------------------------


def test_the_same_key_is_independent_per_user_and_per_endpoint(
    client, module_seeded_url, owner
):
    admin = User(module_seeded_url, roles=("administrator",))
    key, label = new_key(), new_label()

    mine = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)
    theirs = post(client, f"/items/{ITEM}", admin.headers(client), body(label), key)
    elsewhere = post(client, f"/others/{ITEM}", owner.headers(client), body(label), key)

    for response in (mine, theirs, elsewhere):
        assert response.status_code == 201, response.text
        assert REPLAYED_HEADER not in response.headers
    assert effects(module_seeded_url, label) == 3
    assert len(key_rows(module_seeded_url, key)) == 3


# --- C12: guards ----------------------------------------------------------------------


def test_a_route_that_forgets_respond_is_500_and_leaves_no_key(
    client, module_seeded_url, owner
):
    key, label = new_key(), new_label()

    response = post(client, "/forgetful", owner.headers(client), body(label), key)

    assert_problem(response, 500, "internal_error")
    assert effects(module_seeded_url, label) == 0
    assert key_rows(module_seeded_url, key) == []


def test_a_decorated_route_without_an_access_rule_is_500(
    client, module_seeded_url, owner
):
    key, label = new_key(), new_label()

    response = post(client, "/unguarded", owner.headers(client), body(label), key)

    assert_problem(response, 500, "internal_error")
    assert effects(module_seeded_url, label) == 0
    assert key_rows(module_seeded_url, key) == []


# --- B6: the stored body and the first response ---------------------------------------


def test_the_first_response_body_is_the_stored_body(client, module_seeded_url, owner):
    key, label = new_key(), new_label()

    response = post(client, f"/items/{ITEM}", owner.headers(client), body(label), key)

    [row] = key_rows(module_seeded_url, key)
    assert response.json() == row.response_body
    assert response.headers["content-type"] == "application/json"
