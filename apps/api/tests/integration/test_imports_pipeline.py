"""The import pipeline over HTTP and the task (#122 B1-B3, B7, B8, C1, C2,
D1-D4, E1, E2, F2, F3, G2).

Kind-specific rules are ``test_imports_students.py`` and
``test_imports_tutors.py``; the registry, parser and row codes are unit tests
(``tests/unit/test_import_*.py``).
"""

import logging
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock as app_clock
from app.core.authz import Principal
from app.modules.administration.imports import service
from app.modules.administration.imports.errors import MAX_BODY_BYTES
from app.modules.administration.imports.registry import (
    CommitOutcome,
    ImportRowRef,
    RowResult,
    register_import_kind,
    unregister_import_kind,
)
from app.modules.families.service import STUDENTS_IMPORT_KIND
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, SetCookie, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql
from tests.integration.import_support import (
    IMPORTS,
    action,
    clear_queue,
    job_path,
    job_row,
    post_import,
    post_raw,
    principal_of,
    queued_runs,
    rows_of,
    run_job,
    stored_rows,
    student,
    students_csv,
    students_named,
    tutors_csv,
    unique,
)
from tests.integration.invitation_support import sign_in

SENTINEL = "Zyxwsentinelvq"
TASK_LOGGER = "app.modules.administration.imports.tasks"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass(frozen=True)
class Actor:
    user: CreatedUser
    cookie: SetCookie


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    return {
        role: create_user(module_seeded_url, roles=(role,))
        for role in (
            OWNER,
            ADMINISTRATOR,
            ACADEMIC_COORDINATOR,
            FINANCE_STAFF,
            TUTOR,
            STUDENT,
            GUARDIAN,
        )
    }


def actor(client: TestClient, users: dict[str, CreatedUser], role: str) -> Actor:
    user = users[role]
    return Actor(user, sign_in(client, user.username))


@pytest.fixture
def owner(client, users) -> Actor:
    return actor(client, users, OWNER)


@pytest.fixture
def coordinator(client, users) -> Actor:
    return actor(client, users, ACADEMIC_COORDINATOR)


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


def stage(client: TestClient, who: Actor, rows: Sequence[Mapping[str, str]]) -> dict:
    response = post_import(client, who.cookie, "students", students_csv(rows))
    assert response.status_code == 201, response.text
    return response.json()


# --- G2: who may do what ----------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "students", "tutors"),
    [
        (OWNER, 201, 201),
        (ADMINISTRATOR, 201, 201),
        (ACADEMIC_COORDINATOR, 201, 403),
        (FINANCE_STAFF, 403, 403),
        (TUTOR, 403, 403),
        (STUDENT, 403, 403),
        (GUARDIAN, 403, 403),
    ],
)
def test_g2_who_may_import_each_kind(client, users, role, students, tutors):
    who = actor(client, users, role)
    tutor_name = f"t{uuid.uuid4().hex[:10]}"

    s = post_import(client, who.cookie, "students", students_csv([student(unique())]))
    t = post_import(
        client,
        who.cookie,
        "tutors",
        tutors_csv([{"username": tutor_name, "display_name": "Tutor"}]),
    )
    listed = get(client, who.cookie, IMPORTS)

    assert (s.status_code, t.status_code) == (students, tutors)
    for response in (s, t):
        if response.status_code == 403:
            assert response.json()["code"] == "permission_denied"
    # #123 E1: Finance may import opening balances, so it may list (its kinds).
    may_list = students == 201 or role == FINANCE_STAFF
    assert listed.status_code == (200 if may_list else 403)


def test_g2_anonymous_gets_401(client):
    client.cookies.clear()
    for response in (
        client.get(IMPORTS),
        client.post(IMPORTS, json={"kind": "students", "csv": ""}),
        client.get(job_path(uuid.uuid4())),
    ):
        assert response.status_code == 401


def test_c1_c2_the_coordinator_never_sees_tutor_jobs(client, owner, coordinator):
    tutors = post_import(
        client,
        owner.cookie,
        "tutors",
        tutors_csv([{"username": f"t{uuid.uuid4().hex[:8]}", "display_name": "T"}]),
    ).json()
    mine = stage(client, coordinator, [student(unique())])

    listed = get(client, coordinator.cookie, IMPORTS, limit=100).json()
    owner_list = get(client, owner.cookie, IMPORTS, limit=100).json()
    hidden = [
        get(client, coordinator.cookie, job_path(tutors["id"])),
        get(client, coordinator.cookie, job_path(tutors["id"], "/rows")),
        action(client, coordinator.cookie, tutors["id"], "commit", version=1),
        action(client, coordinator.cookie, tutors["id"], "discard", version=1),
    ]
    filtered = get(client, coordinator.cookie, IMPORTS, kind="tutors").json()

    ids = {item["id"] for item in listed["items"]}
    assert mine["id"] in ids and tutors["id"] not in ids
    assert {item["kind"] for item in listed["items"]} == {"students"}
    assert tutors["id"] in {item["id"] for item in owner_list["items"]}
    for response in hidden:
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    assert filtered["items"] == [] and filtered["total"] == 0


# --- B1, B3: the create order and graceful failures -------------------------------


def test_b1_a_body_over_2_mib_is_413_by_length_and_while_streaming(client, owner):
    big = b'{"kind":"students","csv":"' + b"a" * MAX_BODY_BYTES + b'"}'

    by_length = post_raw(client, owner.cookie, big)
    streamed = post_raw(
        client,
        owner.cookie,
        iter([big[: MAX_BODY_BYTES // 2], big[MAX_BODY_BYTES // 2 :]]),
    )
    at_limit = post_raw(
        client,
        owner.cookie,
        b'{"kind":"students","csv":"' + b"a" * (MAX_BODY_BYTES - 28) + b'"}',
    )

    for response in (by_length, streamed):
        assert response.status_code == 413, response.text
        assert response.json()["code"] == "import_too_large"
        assert response.json()["details"] == {"max_bytes": MAX_BODY_BYTES}
    assert "content-length" not in {k.lower() for k in streamed.request.headers}
    # Exactly 2 MiB passes the cap and fails later (one long header line,
    # no data row).
    assert at_limit.status_code == 422
    assert at_limit.json()["code"] == "import_file_invalid"


def test_b1_order_permission_before_size_and_validation(client, users):
    # A tutor may import no kind (#123: Finance now may import balances).
    nobody = actor(client, users, TUTOR)
    coordinator = actor(client, users, ACADEMIC_COORDINATOR)
    big = b"x" * (MAX_BODY_BYTES + 1)

    denied_big = post_raw(client, nobody.cookie, big)
    no_csrf = client.post(IMPORTS, content=big, headers={"Cookie": "x=y"})
    invalid_json = post_raw(client, coordinator.cookie, b"{nope")
    unknown_kind = post_import(client, coordinator.cookie, "payments", "a\nb\n")
    extra = post_raw(
        client, coordinator.cookie, b'{"kind":"tutors","csv":"","other":1}'
    )
    kind_denied = post_import(client, coordinator.cookie, "tutors", "")

    assert denied_big.status_code == 403
    assert denied_big.json()["code"] == "permission_denied"
    assert no_csrf.status_code == 401
    assert invalid_json.status_code == 422
    assert invalid_json.json()["code"] == "validation_error"
    assert unknown_kind.status_code == 422
    assert unknown_kind.json()["errors"][0]["path"] == ["body", "kind"]
    assert extra.status_code == 422
    assert extra.json()["errors"][0]["path"] == ["body", "other"]
    assert kind_denied.status_code == 403
    assert kind_denied.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("csv", "details"),
    [
        ("given_names,family_names\n\xe9�,x\n", {"reason": "encoding"}),
        ("", {"reason": "empty"}),
        (students_csv([]), {"reason": "empty"}),
        (
            "given_names,family_names,birthdate,contact_name,contact_relationship,"
            'contact_phone\n"Ana,Pérez,2012-01-01,M,Madre,5512345678\n',
            {"reason": "malformed", "line": 2},
        ),
        ("given_names,given_names\nA,B\n", {"reason": "duplicate_column"}),
        (
            "given_names,family_names,contact_name\nA,B,C\n",
            {
                "reason": "missing_columns",
                "columns": [
                    "birthdate",
                    "contact_relationship",
                    "contact_phone",
                ],
            },
        ),
    ],
    ids=["latin1", "blank", "header_only", "quote", "duplicate", "missing"],
)
def test_b3_a_bad_file_fails_visibly_and_writes_nothing(
    client, owner, module_seeded_url, csv, details
):
    before = run_sql(module_seeded_url, "SELECT count(*) AS n FROM import_jobs")[0].n

    response = post_import(client, owner.cookie, "students", csv)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "import_file_invalid"
    assert body["details"] == details
    assert "Ana" not in response.text and "Pérez" not in response.text
    after = run_sql(module_seeded_url, "SELECT count(*) AS n FROM import_jobs")[0].n
    assert after == before


def test_b2_1001_rows_is_too_many(client, owner):
    family = unique()
    rows = [student(family, f"N{i:04d}") for i in range(1001)]

    response = post_import(client, owner.cookie, "students", students_csv(rows))

    assert response.status_code == 422
    assert response.json()["code"] == "import_too_many_rows"
    assert response.json()["details"] == {"max": 1000}


# --- B7: bounded work ---------------------------------------------------------------


def test_b7_staging_runs_a_fixed_number_of_statements(module_seeded_url, users):
    owner = principal_of(users[OWNER], OWNER)
    level = unique("Nivel")
    run_sql(
        module_seeded_url,
        "INSERT INTO education_levels (name, sort_order, is_active)"
        " VALUES (:n, 900, true)",
        {"n": level},
    )

    def staged(count: int) -> int:
        family = unique()
        csv = students_csv(
            [
                student(family, f"N{i:04d}", education_level=level.upper())
                for i in range(count)
            ]
        )

        async def call(session: AsyncSession) -> object:
            return await service.create_import_job(
                session,
                principal=owner,
                kind_code="students",
                file_name=None,
                csv_text=csv,
            )

        statements, detail = count_statements(module_seeded_url, call)
        assert detail.counts.ready == count  # type: ignore[attr-defined]
        return statements

    assert staged(3) == staged(300)


# --- B8, C2: the detail and the rows -----------------------------------------------


def test_b8_c2_the_detail_and_rows(client, owner, users):
    family = unique()
    job = stage(
        client,
        owner,
        [student(family, "Ana"), student(family, "Luis", birthdate="x")],
    )

    assert job["kind"] == "students"
    assert job["status"] == "staged"
    assert job["file_name"] == "alumnos.csv"
    assert job["ignored_columns"] == []
    assert job["counts"] == {
        "total": 2,
        "ready": 1,
        "duplicate": 0,
        "error": 1,
        "created": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert job["created_by"] == {
        "id": str(users[OWNER].id),
        "display_name": job["created_by"]["display_name"],
    }
    assert job["committed_by"] is None and job["committed_at"] is None
    assert job["version"] == 1
    assert job["expires_at"] is not None
    rows = rows_of(client, owner.cookie, job["id"])
    assert rows["total"] == 2
    assert [r["row_number"] for r in rows["items"]] == [2, 3]
    assert rows["items"][0]["values"]["given_names"] == "Ana"
    assert rows["items"][1]["errors"] == [
        {"column": "birthdate", "code": "invalid_date"}
    ]
    only_errors = rows_of(client, owner.cookie, job["id"], status="error")
    assert [r["row_number"] for r in only_errors["items"]] == [3]


def test_b3_unknown_columns_are_ignored_and_listed(client, owner):
    csv = (
        students_csv([student(unique())])
        .replace("given_names,", "Notas , given_names,", 1)
        .replace("\nAna,", "\nhola,Ana,", 1)
    )

    job = post_import(client, owner.cookie, "students", csv).json()

    assert job["ignored_columns"] == ["notas"]
    assert job["counts"]["ready"] == 1


# --- D1-D4: commit ----------------------------------------------------------------


def test_d1_commit_order_and_202(client, owner, module_seeded_url):
    family = unique()
    job = stage(client, owner, [student(family)])
    errors_only = stage(client, owner, [student(family, birthdate="x")])

    unknown = action(client, owner.cookie, uuid.uuid4(), "commit", version=1)
    bad_body = action(client, owner.cookie, job["id"], "commit", version=0)
    stale = action(client, owner.cookie, job["id"], "commit", version=7)
    nothing = action(client, owner.cookie, errors_only["id"], "commit")
    accepted = action(client, owner.cookie, job["id"], "commit")
    twice = action(client, owner.cookie, job["id"], "commit")

    assert unknown.status_code == 404
    assert bad_body.status_code == 422
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    assert nothing.status_code == 422
    assert nothing.json()["code"] == "import_nothing_to_commit"
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["status"] == "committing"
    assert accepted.json()["committed_by"]["id"] == str(owner.user.id)
    assert accepted.json()["expires_at"] is None
    assert twice.status_code == 409
    assert twice.json()["code"] == "import_not_committable"
    # Queued after the commit (the background task ran).
    assert queued_runs(module_seeded_url) == 1
    assert students_named(module_seeded_url, family) == []

    run_job(module_seeded_url)

    done = get(client, owner.cookie, job_path(job["id"])).json()
    assert done["status"] == "committed"
    assert done["committed_at"] is not None
    assert done["counts"]["created"] == 1 and done["counts"]["ready"] == 0
    again = action(client, owner.cookie, job["id"], "commit")
    assert again.status_code == 409


def test_d3_values_are_cleared_on_commit(client, owner, module_seeded_url):
    family = unique()
    job = stage(client, owner, [student(family), student(family, "Luis", birthdate="")])
    action(client, owner.cookie, job["id"], "commit")

    run_job(module_seeded_url)

    stored = stored_rows(module_seeded_url, job["id"])
    assert [row.values for row in stored] == [None, None]
    assert [row.status for row in stored] == ["created", "error"]
    rows = rows_of(client, owner.cookie, job["id"])
    assert [row["values"] for row in rows["items"]] == [None, None]
    assert rows["items"][0]["record_id"] == str(
        students_named(module_seeded_url, family)[0].id
    )
    assert rows["items"][1]["errors"] == [{"column": "birthdate", "code": "required"}]


def test_d4_the_same_file_again_creates_nothing(client, owner, module_seeded_url):
    family = unique()
    rows = [student(family, "Ana"), student(family, "Luis")]
    first = stage(client, owner, rows)
    action(client, owner.cookie, first["id"], "commit")
    run_job(module_seeded_url)

    second = stage(client, owner, rows)
    commit = action(client, owner.cookie, second["id"], "commit")
    before = job_row(module_seeded_url, first["id"])
    run_job(module_seeded_url)

    assert second["counts"]["duplicate"] == 2 and second["counts"]["ready"] == 0
    assert commit.status_code == 422
    assert commit.json()["code"] == "import_nothing_to_commit"
    assert len(students_named(module_seeded_url, family)) == 2
    # Re-running the task on a committed job changes nothing.
    after = job_row(module_seeded_url, first["id"])
    assert (after.version, after.updated_at) == (before.version, before.updated_at)


def test_d2_two_jobs_of_one_file_commit_once(client, owner, module_seeded_url):
    # The per-kind lock and the per-batch re-check: the later job skips.
    family = unique()
    rows = [student(family, "Ana"), student(family, "Luis")]
    first = stage(client, owner, rows)
    second = stage(client, owner, rows)
    action(client, owner.cookie, first["id"], "commit")
    action(client, owner.cookie, second["id"], "commit")

    result = run_job(module_seeded_url)

    assert result.committed >= 2
    assert len(students_named(module_seeded_url, family)) == 2
    later = get(client, owner.cookie, job_path(second["id"])).json()
    assert later["status"] == "committed"
    assert later["counts"]["skipped"] == 2 and later["counts"]["created"] == 0
    skipped = rows_of(client, owner.cookie, second["id"])["items"]
    assert {(None, "duplicate_existing")} == {
        (w["column"], w["code"]) for r in skipped for w in r["warnings"]
    }


@dataclass(frozen=True)
class FlakyStudents:
    """The students kind, with a ``commit_row`` that raises on one row."""

    code: str = "flaky_students"
    permission: str = "student.manage"
    columns: Sequence = STUDENTS_IMPORT_KIND.columns
    batch_size: int = 100
    fail_on: list[int] = None  # type: ignore[assignment]

    def may_import(self, principal: Principal) -> bool:
        return STUDENTS_IMPORT_KIND.may_import(principal)

    async def prepare(self, session, rows, principal) -> list[RowResult]:  # type: ignore[no-untyped-def]
        return await STUDENTS_IMPORT_KIND.prepare(session, rows, principal)

    async def commit_row(  # type: ignore[no-untyped-def]
        self, session, row_values, ref: ImportRowRef, actor_user_id
    ) -> CommitOutcome:
        if ref.row_number in self.fail_on:
            raise RuntimeError("boom")
        return await STUDENTS_IMPORT_KIND.commit_row(
            session, row_values, ref, actor_user_id
        )


@pytest.fixture
def flaky() -> Iterator[FlakyStudents]:
    kind = FlakyStudents(fail_on=[])
    register_import_kind(kind)
    yield kind
    unregister_import_kind(kind.code)


def test_d3_a_failing_batch_rolls_back_and_a_retry_resumes(
    client, owner, module_seeded_url, flaky, caplog
):
    family = unique()
    rows = [student(family, f"N{i:04d}") for i in range(200)]
    response = post_import(client, owner.cookie, "flaky_students", students_csv(rows))
    job = response.json()
    flaky.fail_on.append(151)  # the 150th data row (line 151)
    action(client, owner.cookie, job["id"], "commit")

    with caplog.at_level(logging.ERROR, logger=TASK_LOGGER):
        result = run_job(module_seeded_url)

    failed = get(client, owner.cookie, job_path(job["id"])).json()
    assert result.failed == 1
    assert failed["status"] == "failed"
    assert failed["counts"]["created"] == 100 and failed["counts"]["ready"] == 100
    assert failed["expires_at"] is not None
    assert len(students_named(module_seeded_url, family)) == 100
    [record] = [
        r for r in caplog.records if r.message == "Committing an import batch failed"
    ]
    assert record.error == "RuntimeError"
    assert record.import_job_id == job["id"]

    flaky.fail_on.clear()
    retry = action(client, owner.cookie, job["id"], "commit")
    run_job(module_seeded_url)

    done = get(client, owner.cookie, job_path(job["id"])).json()
    assert retry.status_code == 202
    assert done["status"] == "committed"
    assert done["counts"]["created"] == 200
    created = students_named(module_seeded_url, family)
    assert len(created) == 200
    assert len({row.given_names for row in created}) == 200


def test_d2_a_committing_job_with_nothing_queued_is_committed_by_the_tick(
    client, owner, module_seeded_url
):
    family = unique()
    job = stage(client, owner, [student(family)])
    action(client, owner.cookie, job["id"], "commit")
    clear_queue(module_seeded_url)

    run_job(module_seeded_url)

    assert (
        get(client, owner.cookie, job_path(job["id"])).json()["status"] == "committed"
    )


# --- E1, E2: discard and expiry -----------------------------------------------------


def test_e1_discard_deletes_the_rows(client, owner, module_seeded_url):
    job = stage(client, owner, [student(unique())])

    stale = action(client, owner.cookie, job["id"], "discard", version=9)
    discarded = action(client, owner.cookie, job["id"], "discard")
    again = action(client, owner.cookie, job["id"], "discard")
    commit = action(client, owner.cookie, job["id"], "commit")

    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"
    assert discarded.json()["counts"]["total"] == 1
    assert stored_rows(module_seeded_url, job["id"]) == []
    assert again.status_code == 409
    assert again.json()["code"] == "import_not_discardable"
    assert commit.status_code == 409


def test_e2_stale_staged_and_failed_jobs_expire(client, owner, module_seeded_url):
    staged = stage(client, owner, [student(unique())])
    failed = stage(client, owner, [student(unique())])
    run_sql(
        module_seeded_url,
        "UPDATE import_jobs SET status = 'failed', committed_by_user_id = :u"
        " WHERE id = :i",
        {"i": failed["id"], "u": str(owner.user.id)},
    )

    with frozen_clock(app_clock.now() + timedelta(days=7, minutes=1)):
        result = run_job(module_seeded_url)

    assert result.expired >= 2
    for job in (staged, failed):
        later = get(client, owner.cookie, job_path(job["id"])).json()
        assert later["status"] == "expired"
        assert later["expires_at"] is None
        assert stored_rows(module_seeded_url, job["id"]) == []


def test_e2_a_job_inside_its_7_days_stays(client, owner, module_seeded_url):
    staged = stage(client, owner, [student(unique())])

    with frozen_clock(app_clock.now() + timedelta(days=6, hours=23)):
        run_job(module_seeded_url)

    assert (
        get(client, owner.cookie, job_path(staged["id"])).json()["status"] == "staged"
    )
    assert len(stored_rows(module_seeded_url, staged["id"])) == 1


# --- F2, F3: audit and personal data ------------------------------------------------


def test_f2_f3_actors_and_no_staged_value_anywhere(
    client, users, owner, module_seeded_url, caplog
):
    admin = actor(client, users, ADMINISTRATOR)
    family = unique()
    job = stage(
        client,
        owner,
        [student(family, school_name=SENTINEL, contact_name=f"{SENTINEL} Madre")],
    )
    action(client, admin.cookie, job["id"], "commit")
    with caplog.at_level(logging.DEBUG):
        run_job(module_seeded_url)
    [created] = students_named(module_seeded_url, family)

    events = run_sql(
        module_seeded_url,
        "SELECT action, actor_user_id, changes FROM audit_events"
        " WHERE record_type = 'import_jobs' AND record_id = :i"
        " ORDER BY occurred_at, id",
        {"i": job["id"]},
    )
    assert (events[0].action, events[0].actor_user_id) == ("create", owner.user.id)
    assert {(e.action, e.actor_user_id) for e in events[1:]} == {
        ("update", admin.user.id)
    }
    assert [e.changes["status"]["new"] for e in events if "status" in e.changes][
        1:
    ] == ["committing", "committed"]
    [student_event] = run_sql(
        module_seeded_url,
        "SELECT actor_user_id FROM audit_events WHERE record_type = 'students'"
        " AND record_id = :i AND action = 'create'",
        {"i": created.id},
    )
    assert student_event.actor_user_id == admin.user.id
    # The created record holds the value; the import's own trail never does.
    leaked = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type NOT IN ('students', 'student_emergency_contacts')"
        " AND (changes::text LIKE :s OR coalesce(details::text, '') LIKE :s)",
        {"s": f"%{SENTINEL}%"},
    )
    assert leaked[0].n == 0
    assert not [
        r for r in caplog.records if SENTINEL in r.getMessage() + str(r.__dict__)
    ]


def test_f4_a_formula_is_stored_as_text(client, owner, module_seeded_url):
    family = unique()
    job = stage(client, owner, [student(family, school_name='=HYPERLINK("http://x")')])
    action(client, owner.cookie, job["id"], "commit")

    run_job(module_seeded_url)

    [created] = students_named(module_seeded_url, family)
    assert created.school_name == '=HYPERLINK("http://x")'


def test_c1_list_filters_and_pages(client, owner):
    job = stage(client, owner, [student(unique())])
    action(client, owner.cookie, job["id"], "discard")

    page = get(
        client, owner.cookie, IMPORTS, status="discarded", kind="students", limit=1
    )
    bad = get(client, owner.cookie, IMPORTS, status="nope")

    assert page.status_code == 200
    body = page.json()
    assert body["limit"] == 1 and len(body["items"]) == 1
    assert body["items"][0]["status"] == "discarded"
    assert bad.status_code == 422
