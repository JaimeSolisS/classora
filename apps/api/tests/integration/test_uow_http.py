"""Unit of Work over HTTP (B1-B6) with a test-only app and router."""

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.conventions import install_conventions
from app.core.models import ensure_version
from app.core.uow import UnitOfWork, UnitOfWorkDep, get_uow
from app.main import lifespan
from tests.integration.conftest import run_sql, settings_for
from tests.integration.models import DeferredCode, VersionedWidget, Widget


class WidgetService:
    """Stands in for a module service: it uses the session, never commits."""

    def __init__(self, uow: UnitOfWorkDep) -> None:
        self.uow = uow

    def add(self, name: str) -> Widget:
        widget = Widget(name=name)
        self.uow.session.add(widget)
        return widget


class AuditService:
    """A second service in the same request."""

    def __init__(self, uow: UnitOfWorkDep) -> None:
        self.uow = uow


Widgets = Annotated[WidgetService, Depends()]
Audit = Annotated[AuditService, Depends()]

router = APIRouter()


@router.post("/widgets")
async def create_widget(name: str, widgets: Widgets) -> dict[str, str]:
    widget = widgets.add(name)
    await widgets.uow.session.flush()
    return {"id": str(widget.id)}


@router.post("/widgets/http-error")
async def create_then_http_error(name: str, widgets: Widgets) -> None:
    widgets.add(name)
    await widgets.uow.session.flush()
    raise HTTPException(status_code=409)


@router.post("/widgets/crash")
async def create_then_crash(name: str, widgets: Widgets) -> None:
    widgets.add(name)
    await widgets.uow.session.flush()
    raise RuntimeError("boom")


@router.post("/widgets/two-services")
async def two_services(
    name: str, widgets: Widgets, audit: Audit, uow: UnitOfWorkDep, fail: bool = False
) -> dict[str, bool]:
    widgets.add(name)
    audit.uow.session.add(Widget(name=f"{name}-audit"))
    await uow.session.flush()
    if fail:
        raise HTTPException(status_code=422)
    return {
        "same_uow": widgets.uow is audit.uow is uow,
        "same_session": widgets.uow.session is audit.uow.session,
    }


@router.get("/widgets/count")
async def count_widgets(uow: UnitOfWorkDep) -> dict[str, int]:
    rows = (await uow.session.scalars(select(Widget))).all()
    return {"count": len(rows)}


@router.post("/deferred")
async def duplicate_deferred_codes(uow: UnitOfWorkDep) -> dict[str, str]:
    # Both inserts succeed; the deferred unique constraint fails at COMMIT.
    uow.session.add_all([DeferredCode(code="dup"), DeferredCode(code="dup")])
    await uow.session.flush()
    return {"status": "should never be sent"}


@router.post("/versioned/{name}")
async def rename_versioned(
    name: str, new_name: str, expected: int, uow: UnitOfWorkDep
) -> dict[str, int]:
    widget = (
        await uow.session.scalars(
            select(VersionedWidget).where(VersionedWidget.name == name)
        )
    ).one()
    ensure_version(widget, expected)
    widget.name = new_name
    await uow.session.flush()
    return {"version": widget.version}


@router.post("/misused")
async def misused(
    uow: UnitOfWorkDep, other: Annotated[UnitOfWork, Depends(get_uow)]
) -> None:
    pass  # pragma: no cover - the second Unit of Work is refused first


def build_app(dsn: str) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    install_conventions(app)
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings_for(dsn)
    return app


@pytest.fixture
def app(tables_url: str) -> FastAPI:
    return build_app(tables_url)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
        # B6: every connection went back to the pool.
        assert app.state.engine.pool.checkedout() == 0


def names(dsn: str) -> list[str]:
    """Committed widget names, read from a new connection."""
    return sorted(row.name for row in run_sql(dsn, "SELECT name FROM widgets"))


def test_success_commits_before_the_response(client, tables_url):
    response = client.post("/widgets", params={"name": "committed"})

    assert response.status_code == 200
    assert names(tables_url) == ["committed"]


def test_http_exception_rolls_back(client, tables_url):
    response = client.post("/widgets/http-error", params={"name": "rolled-back"})

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert names(tables_url) == []


def test_unexpected_exception_rolls_back(client, tables_url):
    response = client.post("/widgets/crash", params={"name": "rolled-back"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert names(tables_url) == []


def test_failed_commit_returns_5xx_and_persists_nothing(client, tables_url):
    response = client.post("/deferred")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "should never be sent" not in response.text
    assert "dup" not in response.text
    assert run_sql(tables_url, "SELECT count(*) FROM deferred_codes") == [(0,)]


def test_dependencies_share_one_unit_of_work(client, tables_url):
    response = client.post("/widgets/two-services", params={"name": "shared"})

    assert response.status_code == 200
    assert response.json() == {"same_uow": True, "same_session": True}
    assert names(tables_url) == ["shared", "shared-audit"]


def test_dependencies_roll_back_together(client, tables_url):
    response = client.post(
        "/widgets/two-services", params={"name": "shared", "fail": True}
    )

    assert response.status_code == 422
    assert names(tables_url) == []


def test_read_only_request_works(client, tables_url):
    client.post("/widgets", params={"name": "one"})

    response = client.get("/widgets/count")

    assert response.status_code == 200
    assert response.json() == {"count": 1}


def test_each_request_gets_its_own_unit_of_work(client, tables_url):
    client.post("/widgets", params={"name": "first"})
    client.post("/widgets/crash", params={"name": "second"})
    client.post("/widgets", params={"name": "third"})

    assert names(tables_url) == ["first", "third"]


def test_stale_client_version_returns_409_and_changes_nothing(client, tables_url):
    run_sql(tables_url, "INSERT INTO versioned_widgets (name) VALUES ('w')")

    stale = client.post("/versioned/w", params={"new_name": "x", "expected": 2})
    current = client.post("/versioned/w", params={"new_name": "y", "expected": 1})

    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"
    assert current.status_code == 200
    assert current.json() == {"version": 2}
    assert [
        tuple(r)
        for r in run_sql(tables_url, "SELECT name, version FROM versioned_widgets")
    ] == [("y", 2)]


def test_a_second_unit_of_work_in_one_request_is_refused(client, tables_url):
    response = client.post("/misused")

    assert response.status_code == 500


def test_no_connection_leak_after_mixed_requests(app, tables_url):
    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(3):
            client.post("/widgets", params={"name": "ok"})
            client.post("/widgets/http-error", params={"name": "e"})
            client.post("/widgets/crash", params={"name": "e"})
            client.post("/deferred")
            client.get("/widgets/count")

        pool = app.state.engine.pool
        assert pool.checkedout() == 0
        assert pool.checkedin() >= 1
    assert names(tables_url) == ["ok", "ok", "ok"]
