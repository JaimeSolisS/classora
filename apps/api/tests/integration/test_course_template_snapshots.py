"""Course template snapshots (#64 E, owner decision E1 (a)) and concurrent
template writes (D7).

A cohort (#65) is created from an immutable snapshot of its template:
editing the template afterwards never changes the snapshot, and a snapshot
of the new version shows the edits.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.courses import service
from app.modules.courses.errors import CourseTemplateInactive, CourseTemplateNotFound
from app.modules.courses.schemas import CourseTemplateReplace, CourseTemplateSnapshot
from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get, patch, subject_path
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.courses_support import (
    create_template,
    put,
    subjects,
    template_events,
    template_path,
    write_body,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture
def template(client, cookie) -> dict[str, Any]:
    return create_template(client, cookie, subjects(client, cookie, 2))


def snapshot(url: str, template_id: Any) -> CourseTemplateSnapshot:
    return in_uow(
        url,
        lambda session: service.snapshot_course_template(
            session, uuid.UUID(str(template_id))
        ),
    )


def stored_text(url: str, snapshot_id: uuid.UUID) -> str:
    [row] = run_sql(
        url,
        "SELECT content::text AS content FROM course_template_snapshots WHERE id = :id",
        {"id": snapshot_id},
    )
    text: str = row.content
    return text


def snapshot_rows(url: str, template_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, template_version, schema_version FROM course_template_snapshots"
        " WHERE template_id = :id ORDER BY template_version",
        {"id": uuid.UUID(str(template_id))},
    )


def ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- E1, E2 -----------------------------------------------------------------------


def test_a_snapshot_holds_the_template_content(module_seeded_url, template):
    taken = snapshot(module_seeded_url, template["id"])

    assert taken.template_id == uuid.UUID(template["id"])
    assert taken.template_version == 1
    content = taken.content
    assert content["name"] == template["name"]
    assert [m["id"] for m in content["modules"]] == [
        m["id"] for m in template["modules"]
    ]
    assert [s["id"] for s in content["subjects"]] == [
        s["id"] for s in template["subjects"]
    ]
    assert content["completion"] == {
        "min_attendance_percent": 80,
        "required_module_ids": template["completion"]["required_module_ids"],
        "required_assessment_ids": template["completion"]["required_assessment_ids"],
    }
    [row] = snapshot_rows(module_seeded_url, template["id"])
    assert (row.id, row.template_version, row.schema_version) == (taken.id, 1, 1)


def test_later_edits_never_change_a_snapshot(
    module_seeded_url, client, cookie, template
):
    first = snapshot(module_seeded_url, template["id"])
    before = stored_text(module_seeded_url, first.id)
    path = template_path(template["id"])

    # Rename.
    body = write_body(template)
    body["name"] = template["name"] + " (2027)"
    current = ok(put(client, cookie, path, body))
    # Edit a module, reorder, delete a module.
    body = write_body(current)
    body["modules"][0]["title"] = "Bienvenida"
    body["modules"] = [body["modules"][1], body["modules"][0]]
    body["assessments"][0]["module_index"] = 0
    current = ok(put(client, cookie, path, body))
    # Change the attendance percentage and a required flag.
    body = write_body(current)
    body["min_attendance_percent"] = 90
    body["modules"][1]["is_required"] = False
    current = ok(put(client, cookie, path, body))
    # Deactivate a linked subject in the catalog.
    ok(
        patch(
            client,
            cookie,
            subject_path(template["subjects"][1]["id"]),
            {"version": 1, "is_active": False},
        )
    )

    assert stored_text(module_seeded_url, first.id) == before
    reread = in_uow(
        module_seeded_url,
        lambda session: service.get_course_template_snapshot(session, first.id),
    )
    assert reread == first
    later = snapshot(module_seeded_url, template["id"])
    assert later.id != first.id
    assert later.template_version == current["version"] == 4
    assert later.content["name"] == template["name"] + " (2027)"
    assert [m["title"] for m in later.content["modules"]] == [
        "Desarrollo",
        "Bienvenida",
    ]
    assert later.content["completion"]["min_attendance_percent"] == 90
    assert later.content["completion"]["required_module_ids"] == [
        current["modules"][0]["id"]
    ]
    assert later.content["assessments"][0]["module_id"] == current["modules"][0]["id"]
    assert first.content["name"] == template["name"]
    assert len(first.content["modules"]) == 3
    assert [
        r.template_version for r in snapshot_rows(module_seeded_url, template["id"])
    ] == [
        1,
        4,
    ]


def test_two_snapshots_of_one_version_are_one_row(module_seeded_url, template):
    first = snapshot(module_seeded_url, template["id"])
    second = snapshot(module_seeded_url, template["id"])

    assert second == first
    assert len(snapshot_rows(module_seeded_url, template["id"])) == 1
    # One create event, without the content (the row is the record).
    events = [
        (action, changes)
        for table, action, changes in template_events(module_seeded_url, template["id"])
        if table == "course_template_snapshots"
    ]
    assert events == [
        (
            "create",
            {
                "template_id": {"old": None, "new": template["id"]},
                "template_version": {"old": None, "new": 1},
                "schema_version": {"old": None, "new": 1},
            },
        )
    ]


def test_an_inactive_template_has_no_snapshot(
    module_seeded_url, client, cookie, template
):
    ok(
        patch(
            client,
            cookie,
            template_path(template["id"]),
            {"version": 1, "is_active": False},
        )
    )

    with pytest.raises(CourseTemplateInactive):
        snapshot(module_seeded_url, template["id"])

    assert snapshot_rows(module_seeded_url, template["id"]) == []


def test_an_unknown_template_has_no_snapshot(module_seeded_url):
    with pytest.raises(CourseTemplateNotFound):
        snapshot(module_seeded_url, uuid.uuid4())

    assert (
        in_uow(
            module_seeded_url,
            lambda session: service.get_course_template_snapshot(session, uuid.uuid4()),
        )
        is None
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE course_template_snapshots SET content = '{}'::jsonb WHERE id = :id",
        "UPDATE course_template_snapshots SET template_version = 99 WHERE id = :id",
        "DELETE FROM course_template_snapshots WHERE id = :id",
    ],
)
def test_a_raw_update_or_delete_of_a_snapshot_fails(
    module_seeded_url, template, statement
):
    taken = snapshot(module_seeded_url, template["id"])
    before = stored_text(module_seeded_url, taken.id)

    with pytest.raises(DBAPIError, match="course_template_snapshots rows never change"):
        run_sql(module_seeded_url, statement, {"id": taken.id})

    assert stored_text(module_seeded_url, taken.id) == before


def test_no_route_exposes_snapshots(client, cookie, template):
    response = get(client, cookie, template_path(template["id"]) + "/snapshots")

    assert response.status_code in {404, 405}


# --- Concurrency (E2, D7) ---------------------------------------------------------

Change = Callable[[AsyncSession], Awaitable[Any]]
HOLD_SECONDS = 0.5


async def _run(
    engine: Any, change: Change, after_change: Any = None
) -> tuple[str, Any]:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            result = await change(uow.session)
            if after_change is not None:
                await after_change()
    except StaleVersionError:
        return "version_conflict", None
    except AppError as exc:
        return exc.code, None
    return "ok", result


async def race(url: str, first: Change, second: Change) -> list[tuple[str, Any, float]]:
    """``(outcome, result, finished_at)`` of ``first`` (transaction A, which
    holds its transaction open for a moment after its change) and
    ``second`` (transaction B, started once A's change is flushed)."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    changed = asyncio.Event()

    async def hold() -> None:
        changed.set()
        await asyncio.sleep(HOLD_SECONDS)

    async def run_a() -> tuple[str, Any, float]:
        try:
            outcome, result = await _run(engine_a, first, hold)
            return outcome, result, time.monotonic()
        finally:
            changed.set()

    async def run_b() -> tuple[str, Any, float]:
        await changed.wait()
        outcome, result = await _run(engine_b, second)
        return outcome, result, time.monotonic()

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


def _replace(template: dict[str, Any], **changes: Any) -> Change:
    body = write_body(template)
    body.update(changes)
    return lambda session: service.replace_course_template(
        session, uuid.UUID(template["id"]), CourseTemplateReplace.model_validate(body)
    )


def _snapshot_change(template: dict[str, Any]) -> Change:
    return lambda session: service.snapshot_course_template(
        session, uuid.UUID(template["id"])
    )


def test_a_put_waits_for_a_snapshot_transaction(module_seeded_url, template):
    started = time.monotonic()

    [(a, taken, a_done), (b, replaced, b_done)] = asyncio.run(
        race(
            module_seeded_url,
            _snapshot_change(template),
            _replace(template, name=template["name"] + " nuevo"),
        )
    )

    assert (a, b) == ("ok", "ok")
    # B's FOR UPDATE waited for A's FOR SHARE until A committed.
    assert b_done >= a_done
    assert b_done - started >= HOLD_SECONDS
    assert taken.template_version == 1
    assert taken.content["name"] == template["name"]
    assert replaced.version == 2
    [row] = snapshot_rows(module_seeded_url, template["id"])
    assert row.template_version == 1


def test_two_concurrent_snapshots_of_one_version_give_one_row(
    module_seeded_url, template
):
    [(a, first, _), (b, second, _)] = asyncio.run(
        race(module_seeded_url, _snapshot_change(template), _snapshot_change(template))
    )

    assert (a, b) == ("ok", "ok")
    assert first == second
    assert len(snapshot_rows(module_seeded_url, template["id"])) == 1


def test_two_puts_from_one_version_give_one_200_and_one_409(
    module_seeded_url, template
):
    [(a, _, _), (b, _, _)] = asyncio.run(
        race(
            module_seeded_url,
            _replace(template, name=template["name"] + " A"),
            _replace(template, session_count=12),
        )
    )

    assert (a, b) == ("ok", "version_conflict")
    [row] = run_sql(
        module_seeded_url,
        "SELECT name, session_count, version FROM course_templates WHERE id = :id",
        {"id": uuid.UUID(template["id"])},
    )
    assert (row.name, row.session_count, row.version) == (
        template["name"] + " A",
        10,
        2,
    )
