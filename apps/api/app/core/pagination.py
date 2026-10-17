"""Offset and cursor pagination helpers (architecture §4.4).

Offset pagination is for ordinary tables::

    @router.get("/things")
    async def list_things(page: OffsetPagination) -> OffsetPage[Thing]:
        ...
        return OffsetPage(
            items=items, total=total, limit=page.limit, offset=page.offset
        )

Cursor (keyset) pagination is for large histories. The cursor is an opaque
base64url string of a JSON array: the sort key values **plus a unique
tiebreaker** (for example ``[created_at, id]``). Fetch up to ``limit + 1`` rows
ordered by that key, strictly after the decoded position, then call
:func:`build_cursor_page`. These helpers are pure Python; applying them to
SQLAlchemy queries comes with the first consumer.
"""

import base64
import binascii
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel

from app.core.errors import AppError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

CursorValue = str | int | float | bool | None
# Longer cursors are rejected before decoding (a real one is far shorter).
MAX_CURSOR_LENGTH = 1024


class InvalidCursorError(AppError):
    status = 400
    code = "invalid_cursor"
    detail = "The pagination cursor is not valid."


@dataclass(frozen=True)
class OffsetParams:
    limit: int
    offset: int


@dataclass(frozen=True)
class CursorParams:
    limit: int
    cursor: str | None


Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def offset_params(
    limit: Limit = DEFAULT_LIMIT, offset: Annotated[int, Query(ge=0)] = 0
) -> OffsetParams:
    return OffsetParams(limit=limit, offset=offset)


def cursor_params(
    limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
) -> CursorParams:
    return CursorParams(limit=limit, cursor=cursor)


OffsetPagination = Annotated[OffsetParams, Depends(offset_params)]
CursorPagination = Annotated[CursorParams, Depends(cursor_params)]


class OffsetPage[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def _reject_constant(value: str) -> Any:
    raise ValueError(value)


def encode_cursor(position: Sequence[CursorValue]) -> str:
    """Opaque, URL-safe cursor for a keyset position (sort values + tiebreaker)."""
    raw = json.dumps(list(position), separators=(",", ":"), allow_nan=False)
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode("ascii")


def decode_cursor(
    cursor: str, *, types: Sequence[type | tuple[type, ...]]
) -> tuple[CursorValue, ...]:
    """Decode a cursor made by :func:`encode_cursor`.

    ``types`` gives the expected type of each position element, for example
    ``(str, int)`` for ``[created_at, id]``; add ``type(None)`` for a nullable
    sort key. Types match exactly, so ``true`` is not accepted as an ``int``.

    Raises :class:`InvalidCursorError` (400) for anything else: longer than
    :data:`MAX_CURSOR_LENGTH`, not base64url, not JSON, or not an array of
    ``len(types)`` values of those types. The cursor value is never echoed.
    """
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError()
    try:
        data = cursor.encode("ascii")
        raw = base64.b64decode(
            data + b"=" * (-len(data) % 4), altchars=b"-_", validate=True
        )
        position = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except UnicodeError, binascii.Error, ValueError, RecursionError:
        raise InvalidCursorError() from None
    if (
        not isinstance(position, list)
        or len(position) != len(types)
        or not all(
            _has_type(value, expected)
            for value, expected in zip(position, types, strict=True)
        )
    ):
        raise InvalidCursorError()
    return tuple(position)


def _has_type(value: object, expected: type | tuple[type, ...]) -> bool:
    allowed = expected if isinstance(expected, tuple) else (expected,)
    if type(value) not in allowed:
        return False
    return not isinstance(value, float) or math.isfinite(value)


def build_cursor_page[T](
    rows: Sequence[T],
    *,
    limit: int,
    key: Callable[[T], Sequence[CursorValue]],
) -> CursorPage[T]:
    """Page from up to ``limit + 1`` fetched rows; ``key`` gives a row's position."""
    items = list(rows[:limit])
    has_more = len(rows) > limit
    next_cursor = encode_cursor(key(items[-1])) if has_more and items else None
    return CursorPage[T](items=items, next_cursor=next_cursor)
