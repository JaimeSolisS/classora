"""Driver URLs and engine options (M1-07 A2, A4, A7; M1-08 D1, D3, D4)."""

from pydantic import SecretStr

from app.core.config import Settings
from app.core.db import (
    async_database_url,
    async_direct_database_url,
    create_direct_engine,
    create_engine,
    create_sessionmaker,
    engine_options,
)
from tests.authz.csrf import TEST_APP_ORIGIN

DSN = "postgresql://classora:s3cret-pw@db.example:6543/classora?application_name=x"
POOLED_DSN = "postgresql://classora:pooled-pw@pgbouncer:6432/classora"
DIRECT_DSN = "postgresql://classora:direct-pw@db:5432/classora"
PGBOUNCER_CONNECT_ARGS = (
    "prepared_statement_cache_size",
    "statement_cache_size",
    "prepared_statement_name_func",
)


def settings(dsn: str = DSN, direct_dsn: str = DIRECT_DSN) -> Settings:
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url=SecretStr(dsn),
        database_direct_url=SecretStr(direct_dsn),
    )


def test_async_url_uses_asyncpg_and_keeps_the_dsn_parts():
    url = async_database_url(settings())

    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "classora"
    assert url.password == "s3cret-pw"
    assert url.host == "db.example"
    assert url.port == 6543
    assert url.database == "classora"
    assert url.query == {"application_name": "x"}


def test_pooled_and_direct_urls_come_from_their_own_settings():
    s = settings(POOLED_DSN, DIRECT_DSN)

    pooled = async_database_url(s)
    direct = async_direct_database_url(s)

    assert (pooled.drivername, pooled.host, pooled.port) == (
        "postgresql+asyncpg",
        "pgbouncer",
        6432,
    )
    assert (direct.drivername, direct.host, direct.port) == (
        "postgresql+asyncpg",
        "db",
        5432,
    )


def test_derived_urls_hide_the_password_when_printed():
    s = settings(POOLED_DSN, DIRECT_DSN)

    for url, password in (
        (async_database_url(s), "pooled-pw"),
        (async_direct_database_url(s), "direct-pw"),
    ):
        assert password not in str(url)
        assert password not in repr(url)


def test_engine_has_no_echo_and_pre_ping():
    for options in (engine_options(), engine_options(pgbouncer=False)):
        assert options["echo"] is False
        assert options["hide_parameters"] is True
        assert options["pool_pre_ping"] is True
        assert options["connect_args"]["timeout"] <= 2.0


def test_request_engine_options_disable_statement_caches():
    connect_args = engine_options()["connect_args"]

    assert connect_args["prepared_statement_cache_size"] == 0
    assert connect_args["statement_cache_size"] == 0


def test_prepared_statement_names_are_unique():
    name_func = engine_options()["connect_args"]["prepared_statement_name_func"]

    first, second = name_func(), name_func()

    assert first != second
    assert first.startswith("__asyncpg_")
    assert first.endswith("__")


def test_request_engine_uses_the_pooled_url_and_pgbouncer_settings(monkeypatch):
    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return "engine"

    monkeypatch.setattr("app.core.db.create_async_engine", fake_create_async_engine)

    assert create_engine(settings(POOLED_DSN, DIRECT_DSN)) == "engine"

    assert captured["url"].host == "pgbouncer"
    assert captured["url"].port == 6432
    for key in PGBOUNCER_CONNECT_ARGS:
        assert key in captured["connect_args"]
    assert captured["connect_args"]["statement_cache_size"] == 0
    assert captured["connect_args"]["prepared_statement_cache_size"] == 0


def test_direct_engine_uses_the_given_url_without_pgbouncer_settings(monkeypatch):
    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return "engine"

    monkeypatch.setattr("app.core.db.create_async_engine", fake_create_async_engine)
    url = async_direct_database_url(settings(POOLED_DSN, DIRECT_DSN))

    create_direct_engine(url, pool_size=1)

    assert captured["url"] is url
    assert captured["pool_size"] == 1
    assert captured["hide_parameters"] is True
    for key in PGBOUNCER_CONNECT_ARGS:
        assert key not in captured["connect_args"]


def test_engine_and_sessions_are_created_without_connecting():
    # Port 1 is closed: creating these must not try to connect.
    engine = create_engine(settings("postgresql://u:p@127.0.0.1:1/db"))
    direct = create_direct_engine(
        async_direct_database_url(settings(direct_dsn="postgresql://u:p@127.0.0.1:1/x"))
    )
    sessionmaker = create_sessionmaker(engine)

    assert engine.echo is False
    assert engine.pool.checkedout() == 0
    assert direct.pool.checkedout() == 0
    assert sessionmaker.kw["expire_on_commit"] is False
    # No isolation level is forced: PostgreSQL's default (READ COMMITTED).
    assert "isolation_level" not in engine_options()
