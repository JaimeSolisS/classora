"""Typed runtime configuration (spec §10 Configuration).

This module is the only place in ``app/`` that reads environment variables.
``Settings`` reads the **process environment only**: it never loads a ``.env``
file itself. Locally, Compose passes ``.env`` values to the ``api`` container;
deployed environments render an env file for Compose (architecture ISSUE-I1).

Application code gets configuration only through :func:`get_settings`, which
tests can replace with ``app.dependency_overrides[get_settings]``.
"""

import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

AppEnv = Literal["local", "dev", "prod"]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class ConfigurationError(Exception):
    """Invalid or missing configuration.

    The message lists one line per environment variable (name and reason) and
    never contains a secret or a submitted DSN, so it is safe to print.
    """


class Settings(BaseSettings):
    """All runtime configuration of the API, validated at startup."""

    model_config = SettingsConfigDict(
        # Empty values (``DATABASE_URL=``) count as missing or unset.
        env_ignore_empty=True,
        # Unrelated variables (POSTGRES_USER, PATH, ...) are ignored.
        extra="ignore",
    )

    app_env: AppEnv
    # Pooled URL (through PgBouncer): the API's request engine (ADR-0003).
    database_url: SecretStr
    # Direct URL to PostgreSQL: Alembic now, and later the LISTEN connection and
    # the worker (ADR-0003). Required, never derived from DATABASE_URL, so a
    # missing value can never silently send them through PgBouncer.
    database_direct_url: SecretStr
    # The origin the SPA is served from (ADR-0007 D9). Unsafe API requests
    # (POST, PUT, PATCH, DELETE) whose ``Origin`` differs get 403 (#24).
    # Required, no default; normalized as browsers serialize ``Origin``.
    app_origin: str
    session_cookie_secure: bool = True
    storage_bucket: NonEmptyStr | None = None
    storage_region: NonEmptyStr | None = None
    storage_endpoint_url: HttpUrl | None = None
    storage_access_key_id: NonEmptyStr | None = None
    storage_secret_access_key: SecretStr | None = Field(
        default=None, validate_default=True
    )
    # Worker (M1-16): seconds a stopping worker waits for running jobs before
    # aborting them. Capped at 30 so Compose's stop_grace_period (40 s) never
    # kills the worker before Procrastinate's own timeout ends.
    worker_shutdown_timeout_seconds: int = Field(default=20, ge=1, le=30)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Process environment only: no .env file and no secrets directory.
        return (init_settings, env_settings)

    @field_validator("database_url", "database_direct_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        # Messages never include the value: it contains the password.
        try:
            parts = urlsplit(value.get_secret_value())
            host = parts.hostname
        except ValueError:
            raise ValueError("is not a valid URL") from None
        if parts.scheme != "postgresql":
            raise ValueError("scheme must be 'postgresql'")
        if not host:
            raise ValueError("must include a host")
        if not parts.path.lstrip("/"):
            raise ValueError("must include a database name")
        return value

    @field_validator("app_origin")
    @classmethod
    def _check_app_origin(cls, value: str, info: ValidationInfo) -> str:
        # Messages never include the value.
        origin = normalize_origin(value)
        app_env = info.data.get("app_env")
        if origin.startswith("http://") and app_env is not None and app_env != "local":
            raise ValueError(
                f"may use http only when APP_ENV=local (APP_ENV is {app_env!r})"
            )
        return origin

    @field_validator("session_cookie_secure")
    @classmethod
    def _check_cookie_secure(cls, value: bool, info: ValidationInfo) -> bool:
        app_env = info.data.get("app_env")
        if not value and app_env is not None and app_env != "local":
            raise ValueError(
                f"may be false only when APP_ENV=local (APP_ENV is {app_env!r})"
            )
        return value

    @field_validator("storage_secret_access_key")
    @classmethod
    def _check_storage_key_pair(
        cls, value: SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        key_id_set = info.data.get("storage_access_key_id") is not None
        if key_id_set != (value is not None):
            raise ValueError(
                "STORAGE_ACCESS_KEY_ID and STORAGE_SECRET_ACCESS_KEY "
                "must be both set or both unset"
            )
        return value


_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST = re.compile(r"[a-z0-9.-]+|\[[0-9a-f:.]+\]")


def normalize_origin(value: str) -> str:
    """``value`` as a browser serializes ``Origin``, or :class:`ValueError`.

    Accepts only a bare ``http``/``https`` origin (scheme, host, optional
    port): no userinfo, path (not even ``/``), query or fragment, and not
    ``null``. Lower-cases the scheme and host and drops a default port. The
    error messages never contain the value.
    """
    if value == "null":
        raise ValueError("must be an origin, not 'null'")
    if not value.isascii() or not value.isprintable() or " " in value:
        raise ValueError("must be an origin without spaces or non-ASCII characters")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("is not a valid origin") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError("scheme must be 'http' or 'https'")
    if "@" in parts.netloc:
        raise ValueError("must not contain userinfo")
    if "?" in value or "#" in value:
        raise ValueError("must not contain a query or a fragment")
    if parts.path:
        raise ValueError("must not contain a path (not even a trailing '/')")
    host = parts.netloc.rsplit(":", 1)[0] if port is not None else parts.netloc
    host = host.lower()
    if not host or _HOST.fullmatch(host) is None:
        raise ValueError("must include a valid host")
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def env_var_names() -> set[str]:
    """Environment variable names that ``Settings`` reads."""
    return {name.upper() for name in Settings.model_fields}


def _reason(error_type: str, message: str) -> str:
    if error_type == "missing":
        return "is required but missing or empty"
    return message.removeprefix("Value error, ")


def format_validation_error(exc: ValidationError) -> str:
    """Render one line per variable, without input values or doc links."""
    lines = ["Configuration error: the API cannot start."]
    for error in exc.errors(include_url=False, include_input=False):
        loc = error["loc"]
        name = str(loc[0]).upper() if loc else "SETTINGS"
        lines.append(f"  {name}: {_reason(error['type'], error['msg'])}")
    return "\n".join(lines)


def load_settings() -> Settings:
    """Validate the process environment. Raises :class:`ConfigurationError`."""
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as exc:
        raise ConfigurationError(format_validation_error(exc)) from None


@lru_cache
def get_settings() -> Settings:
    """The validated settings, loaded once per process."""
    return load_settings()
