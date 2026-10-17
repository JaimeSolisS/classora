"""Offset and cursor pagination helpers (C1-C6)."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import (
    InvalidCursorError,
    build_cursor_page,
    decode_cursor,
    encode_cursor,
)
from tests.unit.conventions_app import ROWS, build_test_app

# --- C1 / C2 offset -----------------------------------------------------------


def test_offset_defaults(client: TestClient):
    body = client.get("/offset").json()

    assert body["limit"] == 25
    assert body["offset"] == 0
    assert body["total"] == len(ROWS)
    assert len(body["items"]) == 25


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("limit=0", "limit"),
        ("limit=101", "limit"),
        ("limit=abc", "limit"),
        ("offset=-1", "offset"),
    ],
)
def test_offset_bounds_rejected(client: TestClient, query: str, field: str):
    response = client.get(f"/offset?{query}")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert [e["path"] for e in body["errors"]] == [["query", field]]


@pytest.mark.parametrize("limit", [1, 100])
def test_offset_limit_bounds_accepted(client: TestClient, limit: int):
    body = client.get(f"/offset?limit={limit}").json()

    assert body["limit"] == limit
    assert len(body["items"]) == min(limit, len(ROWS))


@pytest.mark.parametrize("offset", [len(ROWS), len(ROWS) + 50])
def test_offset_at_or_beyond_total_is_empty_page(client: TestClient, offset: int):
    response = client.get(f"/offset?offset={offset}")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": len(ROWS),
        "limit": 25,
        "offset": offset,
    }


# --- C3 / C4 cursor (in-memory, no database) ----------------------------------


def key(row: tuple[int, int]) -> tuple[int, int]:
    return row


def fetch(rows: list[tuple[int, int]], cursor: str | None, limit: int):
    """Keyset query over an ordered list: rows after the cursor, limit + 1."""
    after = rows
    if cursor is not None:
        position = decode_cursor(cursor, length=2)
        after = [r for r in rows if r > position]
    return build_cursor_page(after[: limit + 1], limit=limit, key=key)


def walk(rows: list[tuple[int, int]], limit: int) -> list[list[tuple[int, int]]]:
    pages = []
    cursor = None
    while True:
        page = fetch(rows, cursor, limit)
        pages.append(page.items)
        if page.next_cursor is None:
            return pages
        cursor = page.next_cursor


# (sort value, unique id); sort values repeat across page boundaries.
TIED = sorted((rank, i) for i, rank in enumerate([1, 1, 1, 2, 2, 2, 2, 3, 3, 4]))


def test_first_middle_and_last_pages():
    first = fetch(TIED, None, 4)
    assert first.items == TIED[:4]
    assert first.next_cursor is not None

    middle = fetch(TIED, first.next_cursor, 4)
    assert middle.items == TIED[4:8]
    assert middle.next_cursor is not None

    last = fetch(TIED, middle.next_cursor, 4)
    assert last.items == TIED[8:]
    assert last.next_cursor is None


def test_empty_result():
    page = fetch([], None, 5)

    assert page.items == []
    assert page.next_cursor is None


def test_exact_multiple_of_limit_has_no_extra_empty_page():
    pages = walk(TIED, 5)

    assert [len(p) for p in pages] == [5, 5]


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 7, 10, 11])
def test_walk_returns_every_row_once_with_ties_across_pages(limit: int):
    pages = walk(TIED, limit)

    flat = [row for page in pages for row in page]
    assert flat == TIED
    assert all(pages)  # no empty page (except for an empty result)


def test_cursor_is_opaque_url_safe_and_round_trips():
    cursor = encode_cursor(["2026-10-17T10:00:00+00:00", 42])

    assert set(cursor) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert decode_cursor(cursor, length=2) == ("2026-10-17T10:00:00+00:00", 42)


def test_cursor_endpoint_walks_all_rows(client: TestClient):
    seen = []
    cursor = None
    while True:
        params = {"limit": 7} | ({"cursor": cursor} if cursor else {})
        body = client.get("/cursor", params=params).json()
        seen.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [row.id for row in ROWS]


# --- C5 invalid cursor --------------------------------------------------------


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


INVALID_CURSORS = [
    "not base64!",
    "%%%%",
    "abc",  # bad padding length
    b64(b"not json"),
    b64(b"\xff\xfe"),
    b64(b'{"rank": 1, "id": 2}'),
    b64(b"[1]"),
    b64(b"[1, 2, 3]"),
    b64(b"[[1], 2]"),
    b64(b"[NaN, 2]"),
    "w6k",  # non-ASCII after decoding is fine, but this is not JSON
    "café",
]


@pytest.mark.parametrize("cursor", INVALID_CURSORS)
def test_decode_rejects_malformed_cursor(cursor: str):
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, length=2)


@pytest.mark.parametrize("cursor", INVALID_CURSORS)
def test_malformed_cursor_is_400_without_echo(client: TestClient, cursor: str):
    response = client.get("/cursor", params={"cursor": cursor})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_cursor"
    assert body["request_id"] == response.headers["x-request-id"]
    assert cursor not in response.text


def test_cursor_limit_bounds(client: TestClient):
    assert client.get("/cursor?limit=0").status_code == 422
    assert client.get("/cursor?limit=101").status_code == 422
    assert client.get("/cursor?limit=100").status_code == 200


# --- C6 OpenAPI -----------------------------------------------------------------


def test_openapi_builds_with_distinct_page_schema_names():
    schema = build_test_app().openapi()

    names = set(schema["components"]["schemas"])
    offset_names = {n for n in names if n.startswith("OffsetPage")}
    cursor_names = {n for n in names if n.startswith("CursorPage")}
    assert len(offset_names) == 1
    assert len(cursor_names) == 1
    assert offset_names != cursor_names
    # Stable: building again gives the same names.
    assert set(build_test_app().openapi()["components"]["schemas"]) == names
