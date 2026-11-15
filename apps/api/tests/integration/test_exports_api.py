"""The export routes (#124 B1-B3, C1-C3, H1) over HTTP, with the task run
in-process.

The worker's own rules are ``test_exports_worker.py`` and the datasets'
rows ``test_exports_datasets.py``; the registry, parameters and rendering are
unit tests (``tests/unit/test_export_*.py``).
"""

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import run_sql
from tests.integration.export_support import (
    DATASETS,
    EXPORTS,
    FAMILY_AND_TUTOR,
    STAFF,
    actor,
    audit_of,
    clear_queue,
    csv_table,
    download,
    expenses_body,
    export_path,
    job_row,
    post_export,
    queued_runs,
    read,
    requested,
    run_job,
    sessions_body,
    set_language,
    set_roles,
    students_body,
)
from tests.integration.invitation_support import reauthenticate

GONE = "00000000-0000-7000-8000-000000000000"
ALLOWED = {
    "owner": {"students", "class_sessions", "expenses"},
    "administrator": {"students", "class_sessions"},
    "academic_coordinator": {"students", "class_sessions"},
    "finance_staff": {"students", "class_sessions", "expenses"},
}
BODIES = {
    "students": students_body(),
    "class_sessions": sessions_body("2026-01-01", "2026-01-31"),
    "expenses": expenses_body(),
}
CSV_TYPE = "text/csv; charset=utf-8"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


def code_of(response) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


# --- B1: the datasets -----------------------------------------------------------------


@pytest.mark.parametrize("role", STAFF)
def test_each_staff_role_lists_the_datasets_it_may_export(
    module_seeded_url, client, role
):
    who = actor(module_seeded_url, client, role)

    response = read(client, who, DATASETS)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    order = ["students", "class_sessions", "expenses"]
    assert [i["code"] for i in items] == [c for c in order if c in ALLOWED[role]]
    by_code = {i["code"]: i for i in items}
    assert by_code["students"]["personal"] is True
    assert by_code["students"]["date_filter"] == "none"
    assert by_code["class_sessions"]["date_filter"] == "required"
    assert by_code["students"]["columns"][0] == {
        "code": "family_names",
        "kind": "text",
        "groupable": False,
        "label": "Apellidos",
    }


@pytest.mark.parametrize("role", FAMILY_AND_TUTOR)
def test_tutors_and_families_may_not_export(module_seeded_url, client, role):
    who = actor(module_seeded_url, client, role)

    for path in (DATASETS, EXPORTS, export_path(GONE), export_path(GONE, "/download")):
        assert code_of(read(client, who, path)) == (403, "permission_denied"), path
    # The access check runs before the body is read.
    assert code_of(post_export(client, who, {})) == (403, "permission_denied")
    assert code_of(post_export(client, who, expenses_body())) == (
        403,
        "permission_denied",
    )


def test_the_labels_follow_the_callers_stored_language(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff")
    set_language(module_seeded_url, who.user, "en")

    items = read(client, who, DATASETS).json()["items"]

    assert items[2]["label"] == "Expenses"
    assert items[2]["columns"][0]["label"] == "Expense date"


# --- B2: the request ------------------------------------------------------------------


@pytest.mark.parametrize("role", STAFF)
def test_each_role_may_request_exactly_its_datasets(module_seeded_url, client, role):
    who = actor(module_seeded_url, client, role)

    for dataset, body in BODIES.items():
        response = post_export(client, who, body)
        if dataset not in ALLOWED[role]:
            assert code_of(response) == (403, "permission_denied"), dataset
        elif dataset == "expenses":
            assert code_of(response) == (202, None), response.text
        else:
            # Personal datasets need a recent reauthentication (#124 E5).
            assert code_of(response) == (403, "reauth_required"), dataset
    reauthenticate(client, who.cookie)
    for dataset in ALLOWED[role] - {"expenses"}:
        assert code_of(post_export(client, who, BODIES[dataset])) == (202, None)


def test_a_request_answers_202_with_the_queued_job(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")

    response = post_export(
        client,
        who,
        expenses_body("xlsx", columns=["amount", "vendor"], group_by="vendor"),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "dataset",
        "format",
        "status",
        "parameters",
        "language",
        "row_count",
        "byte_size",
        "file_name",
        "error_code",
        "created_at",
        "finished_at",
        "expires_at",
    }
    assert body["status"] == "queued"
    assert body["dataset"] == "expenses"
    assert body["format"] == "xlsx"
    assert body["language"] == "es"
    assert body["parameters"] == {
        "dataset": "expenses",
        "format": "xlsx",
        "columns": ["amount", "vendor"],
        "group_by": "vendor",
        "filters": {
            "status": None,
            "category_id": None,
            "vendor_id": None,
            "incurred_from": None,
            "incurred_to": None,
        },
    }
    assert re.fullmatch(r"classora-expenses-\d{8}-\d{4}\.xlsx", body["file_name"])
    assert body["row_count"] is body["finished_at"] is body["expires_at"] is None
    # The run is queued after the commit (#124 B2).
    assert queued_runs(module_seeded_url) == 1


def test_the_default_columns_are_stored_with_the_job(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff", reauth=True)

    body = requested(client, who, students_body())

    assert body["parameters"]["columns"] == [
        "family_names",
        "given_names",
        "is_active",
        "is_minor",
        "school_grade",
        "education_level",
    ]
    assert body["parameters"]["filters"] == {
        "status": "active",
        "education_level_id": None,
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"dataset": "payments", "format": "csv"},
        expenses_body("pdf"),
        expenses_body(columns=["salary"]),
        expenses_body(columns=["amount", "amount"]),
        expenses_body(group_by="amount"),
        expenses_body(columns=["amount"], group_by="vendor"),
        expenses_body(filters={"q": "renta"}),
        {"dataset": "class_sessions", "format": "csv"},
    ],
)
def test_a_bad_body_is_a_validation_error(module_seeded_url, client, body):
    who = actor(module_seeded_url, client, "owner")

    assert code_of(post_export(client, who, body)) == (422, "validation_error")


@pytest.mark.parametrize(
    ("role", "body", "field"),
    [
        (
            "owner",
            sessions_body("2026-02-01", "2026-01-31"),
            "to",
        ),
        (
            "owner",
            sessions_body("2026-01-01", "2027-01-02"),
            "from",
        ),
        (
            # The range is checked before the dataset's permission (B2 6, 7).
            "administrator",
            expenses_body(
                filters={"incurred_from": "2026-02-01", "incurred_to": "2026-01-01"}
            ),
            "incurred_to",
        ),
    ],
)
def test_a_bad_range_is_export_range_invalid(
    module_seeded_url, client, role, body, field
):
    who = actor(module_seeded_url, client, role)

    response = post_export(client, who, body)

    assert code_of(response) == (422, "export_range_invalid")
    assert [e["path"] for e in response.json()["errors"]] == [
        ["body", "filters", field]
    ]


def test_a_full_year_of_sessions_is_accepted(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner", reauth=True)

    response = post_export(client, who, sessions_body("2026-01-01", "2027-01-01"))

    assert code_of(response) == (202, None)


def test_three_queued_exports_are_the_limit(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    for _ in range(3):
        requested(client, who, expenses_body())

    response = post_export(client, who, expenses_body())

    assert code_of(response) == (409, "export_limit_reached")
    assert response.json()["details"] == {"max": 3}
    run_job(module_seeded_url)
    assert code_of(post_export(client, who, expenses_body())) == (202, None)


def test_the_request_is_audited_with_ids_and_codes(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")

    job = requested(client, who, expenses_body())

    [event] = audit_of(module_seeded_url, job["id"])
    assert event.action == "create"
    assert event.actor_user_id == who.user.id
    assert event.changes["dataset"] == {"old": None, "new": "expenses"}
    assert event.changes["parameters"]["new"] == job["parameters"]


# --- C1, C2: the caller's own exports -------------------------------------------------


def test_the_list_and_the_detail_show_only_the_callers_exports(
    module_seeded_url, client
):
    owner = actor(module_seeded_url, client, "owner")
    finance = actor(module_seeded_url, client, "finance_staff")
    mine = requested(client, owner, expenses_body())
    theirs = requested(client, finance, expenses_body())

    listed = read(client, owner, EXPORTS).json()
    assert [item["id"] for item in listed["items"]] == [mine["id"]]
    assert listed["total"] == 1
    assert read(client, owner, export_path(mine["id"])).json() == mine
    # Even the Owner never sees another user's export (C2).
    assert code_of(read(client, owner, export_path(theirs["id"]))) == (
        404,
        "not_found",
    )
    assert code_of(download(client, owner, theirs["id"])) == (404, "not_found")
    assert code_of(read(client, owner, export_path(GONE))) == (404, "not_found")


@pytest.mark.parametrize("role", STAFF)
def test_another_users_export_is_404_for_every_role(module_seeded_url, client, role):
    owner = actor(module_seeded_url, client, "owner")
    job = requested(client, owner, expenses_body())
    who = actor(module_seeded_url, client, role)

    assert code_of(read(client, who, export_path(job["id"]))) == (404, "not_found")
    assert code_of(download(client, who, job["id"])) == (404, "not_found")


def test_the_list_filters_by_status_newest_first(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    first = requested(client, who, expenses_body())
    run_job(module_seeded_url)
    second = requested(client, who, expenses_body())

    everything = read(client, who, EXPORTS).json()["items"]
    ready = read(client, who, EXPORTS, status="ready").json()["items"]

    assert [i["id"] for i in everything] == [second["id"], first["id"]]
    assert [i["id"] for i in ready] == [first["id"]]
    assert code_of(read(client, who, EXPORTS, status="running")) == (
        422,
        "validation_error",
    )


# --- C3: the download -----------------------------------------------------------------


def test_a_ready_export_downloads_with_no_store(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff")
    job = requested(client, who, expenses_body(columns=["description", "amount"]))
    assert code_of(download(client, who, job["id"])) == (409, "export_not_ready")

    run_job(module_seeded_url)
    response = download(client, who, job["id"])

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == CSV_TYPE
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{job["file_name"]}"'
    )
    assert csv_table(response.content)[0] == ["Descripción", "Monto"]
    # Repeatable until it expires, with no reauthentication (#124 E5).
    assert download(client, who, job["id"]).content == response.content
    detail = read(client, who, export_path(job["id"])).json()
    assert detail["status"] == "ready"
    assert detail["byte_size"] == len(response.content)


def test_an_xlsx_export_has_the_spreadsheet_type(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body("xlsx"))
    run_job(module_seeded_url)

    response = download(client, who, job["id"])

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_TYPE
    assert response.content.startswith(b"PK")


def test_a_failed_export_is_not_ready(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff")
    job = requested(client, who, expenses_body())
    set_roles(module_seeded_url, who.user, "administrator")
    run_job(module_seeded_url)
    set_roles(module_seeded_url, who.user, "finance_staff")

    assert job_row(module_seeded_url, job["id"]).error_code == "permission_lost"
    assert code_of(download(client, who, job["id"])) == (409, "export_not_ready")


def test_losing_the_permission_after_ready_denies_the_download(
    module_seeded_url, client
):
    who = actor(module_seeded_url, client, "finance_staff")
    job = requested(client, who, expenses_body())
    run_job(module_seeded_url)
    # Administrators may export students, but not expenses (ADR-0017).
    set_roles(module_seeded_url, who.user, "administrator")

    assert code_of(download(client, who, job["id"])) == (403, "permission_denied")
    assert code_of(read(client, who, export_path(job["id"])))[0] == 200


def test_an_expired_export_is_gone(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body())
    run_job(module_seeded_url)

    def expire_in(seconds: int) -> None:
        run_sql(
            module_seeded_url,
            "UPDATE export_jobs SET expires_at = now() + make_interval(secs => :s)"
            " WHERE id = :i",
            {"s": seconds, "i": job["id"]},
        )

    expire_in(60)
    assert download(client, who, job["id"]).status_code == 200
    expire_in(-1)
    # Past expires_at before the purge ran: 410 already (C3 6).
    assert job_row(module_seeded_url, job["id"]).status == "ready"
    assert code_of(download(client, who, job["id"])) == (410, "export_expired")

    run_job(module_seeded_url)

    assert job_row(module_seeded_url, job["id"]).status == "expired"
    assert code_of(download(client, who, job["id"])) == (410, "export_expired")


def test_an_unknown_export_id_is_not_found(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")

    assert code_of(download(client, who, uuid.uuid4())) == (404, "not_found")
    assert code_of(read(client, who, export_path("nope"))) == (
        422,
        "validation_error",
    )


def test_personal_exports_need_no_password_to_download(module_seeded_url, client):
    who = actor(module_seeded_url, client, "administrator", reauth=True)
    job = requested(client, who, students_body())
    run_job(module_seeded_url)
    # The session's reauthentication is gone (as after 5 minutes).
    run_sql(
        module_seeded_url,
        "UPDATE auth_sessions SET reauthenticated_at = NULL WHERE user_id = :u",
        {"u": who.user.id},
    )

    assert download(client, who, job["id"]).status_code == 200
