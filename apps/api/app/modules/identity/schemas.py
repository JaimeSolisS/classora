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

    Holds no secret: no password hash, session token or session id.
    """

    id: uuid.UUID
    username: str
    display_name: str
    roles: list[str] = Field(description="Role codes, sorted.")
    permissions: list[str] = Field(description="Effective permission codes, sorted.")
