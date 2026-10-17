"""Driver URL derivation and engine options (A2, A4, A7)."""

from pydantic import SecretStr

from app.core.config import Settings
from app.core.db import (
    async_database_url,
    create_engine,
    create_sessionmaker,
    engine_options,
)

DSN = "postgresql://classora:s3cret-pw@db.example:6543/classora?application_name=x"


def settings(dsn: str = DSN) -> Settings:
    return Settings(app_env="local", database_url=SecretStr(dsn))


def test_async_url_uses_asyncpg_and_keeps_the_dsn_parts():
    url = async_database_url(settings())

    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "classora"
    assert url.password == "s3cret-pw"
    assert url.host == "db.example"
    assert url.port == 6543
    assert url.database == "classora"
    assert url.query == {"application_name": "x"}


def test_async_url_hides_the_password_when_printed():
    url = async_database_url(settings())

    assert "s3cret-pw" not in str(url)
    assert "s3cret-pw" not in repr(url)


def test_engine_has_no_echo_and_pre_ping():
    options = engine_options()

    assert options["echo"] is False
    assert options["hide_parameters"] is True
    assert options["pool_pre_ping"] is True
    assert options["connect_args"]["timeout"] <= 2.0


def test_engine_and_sessions_are_created_without_connecting():
    # Port 1 is closed: creating these must not try to connect.
    engine = create_engine(settings("postgresql://u:p@127.0.0.1:1/db"))
    sessionmaker = create_sessionmaker(engine)

    assert engine.echo is False
    assert engine.pool.checkedout() == 0
    assert sessionmaker.kw["expire_on_commit"] is False
    # No isolation level is forced: PostgreSQL's default (READ COMMITTED).
    assert "isolation_level" not in engine_options()
