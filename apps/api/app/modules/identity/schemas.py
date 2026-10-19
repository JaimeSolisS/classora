"""Identity request and response bodies (the public schemas of the module)."""

import uuid

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Username and password. The bounds do not depend on any account."""

    username: str = Field(min_length=1, max_length=256)
    # repr=False: the password never appears in a repr or a log line.
    password: str = Field(min_length=1, max_length=1024, repr=False)


class SessionUser(BaseModel):
    """The signed-in user (``POST /auth/login`` and ``GET /auth/me``).

    Holds no password hash, session token or session id. ``csrf_token`` is
    the session's CSRF token: the SPA keeps it in memory and sends it as
    ``X-CSRF-Token`` on ``POST``, ``PUT``, ``PATCH`` and ``DELETE``.
    """

    id: uuid.UUID
    username: str
    display_name: str
    roles: list[str] = Field(description="Role codes, sorted.")
    permissions: list[str] = Field(description="Effective permission codes, sorted.")
    csrf_token: str = Field(
        description=(
            "The session's CSRF token: send it as X-CSRF-Token on POST, PUT,"
            " PATCH and DELETE. Keep it in memory only."
        ),
        min_length=43,
        max_length=43,
    )
