"""The saved export setup routes (#125 B1-B5, C2, C3) over HTTP.

Running a setup is ``POST /api/v1/exports`` with its stored ``parameters``
(#125 E4); the byte-identical output is ``test_report_setups_replay.py``.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.setups import errors as setup_errors
from app.modules.reporting.setups import service
from tests.integration.auth_support import signed_headers
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.export_support import (
    FAMILY_AND_TUTOR,
    Actor,
    actor,
    clear_queue,
    expenses_body,
    export_path,
    post_export,
    read,
    sessions_body,
    set_roles,
    students_body,
    unique,
)
from tests.integration.invitation_support import reauthenticate
from tests.integration.lifecycle_support import in_uow
from tests.integration.report_setup_support import (
    delete_setup,
    list_setups,
    listed,
    patch_setup,
    post_setup,
    saved,
    setup_audit,
    setup_path,
    stored_row,
)

GONE = "00000000-0000-7000-8000-000000000000"
PARAMETERS = TypeAdapter(ExportParameters)


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


def post_setup_path(client: TestClient, who: Actor, path: str) -> Any:
    client.cookies.clear()
    return client.post(path, headers=signed_headers(who.cookie))


def ids_listed(client: TestClient, who: Actor) -> set[str]:
    return {item["id"] for item in listed(client, who)}


def custom_expenses() -> dict[str, Any]:
    """Non-default columns, a grouping and filters."""
    return expenses_body(
        "xlsx",
        columns=["vendor", "amount", "incurred_on", "status"],
        group_by="vendor",
        filters={"status": ["unpaid", "paid"], "incurred_from": "2026-01-01"},
    )


# --- Access ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", FAMILY_AND_TUTOR)
def test_tutors_and_families_get_403_on_every_route(module_seeded_url, client, role):
    who = actor(module_seeded_url, client, role)

    assert code_of(list_setups(client, who)) == (403, "permission_denied")
    assert code_of(post_setup(client, who, {})) == (403, "permission_denied")
    body = {"name": "x", "parameters": expenses_body()}
    assert code_of(post_setup(client, who, body)) == (403, "permission_denied")
    assert code_of(patch_setup(client, who, GONE, {"version": 1})) == (
        403,
        "permission_denied",
    )
    assert code_of(delete_setup(client, who, GONE)) == (403, "permission_denied")


# --- B3, C2: save, store and list -----------------------------------------------------


def test_a_setup_round_trips_exactly_as_stored(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    parameters = custom_expenses()
    name = unique("Gastos")

    created = saved(client, finance, f"  {name}  ", parameters, "owner")

    assert created["name"] == name
    assert created["dataset"] == "expenses"
    assert created["parameters"] == parameters
    assert created["shared_role"] == "owner"
    assert created["mine"] is True
    assert created["can_run"] is True
    assert created["version"] == 1
    [user] = run_sql(
        url, "SELECT display_name FROM users WHERE id = :u", {"u": finance.user.id}
    )
    assert created["created_by"] == {
        "id": str(finance.user.id),
        "display_name": user.display_name,
    }
    row = stored_row(url, created["id"])
    assert row.parameters == parameters
    assert row.created_by_user_id == finance.user.id
    [item] = [i for i in listed(client, finance) if i["id"] == created["id"]]
    assert item == created
    # C2: the stored JSON validates to the model of the original body.
    assert PARAMETERS.validate_python(item["parameters"]) == (
        PARAMETERS.validate_python(parameters)
    )
    [event] = setup_audit(url, created["id"])
    assert event.action == "create"
    assert event.actor_user_id == finance.user.id


def test_omitted_optional_fields_stay_omitted(module_seeded_url, client):
    url = module_seeded_url
    coordinator = actor(url, client, "academic_coordinator")
    parameters = sessions_body("2026-02-01", "2026-02-28")

    created = saved(client, coordinator, unique("Ses"), parameters)

    assert created["parameters"] == parameters
    assert "columns" not in stored_row(url, created["id"]).parameters
    assert created["shared_role"] is None


def test_the_list_is_by_name_and_only_the_visible_setups(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    other = actor(url, client, "finance_staff")
    prefix = unique("Ord")
    b = saved(client, finance, f"{prefix} b", expenses_body())
    a = saved(client, finance, f"{prefix} A", expenses_body())
    c = saved(client, finance, f"{prefix} c", students_body())
    hidden = saved(client, other, f"{prefix} private", expenses_body())

    names = [i["name"] for i in listed(client, finance) if i["name"].startswith(prefix)]

    assert names == [a["name"], b["name"], c["name"]]
    assert hidden["id"] not in ids_listed(client, finance)


def test_the_list_takes_at_most_three_statements(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    saved(client, finance, unique("Stmt"), expenses_body())

    async def principal(session: AsyncSession) -> Any:
        return await identity.load_principal(session, finance.user.id)

    who = in_uow(url, principal)
    count, result = count_statements(
        url, lambda s: service.list_setups(s, principal=who)
    )

    assert count <= 3
    assert result.items


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"name": ""}, ["body", "name"]),
        ({"name": "   "}, ["body", "name"]),
        ({"name": "x" * 81}, ["body", "name"]),
        ({"shared_role": "tutor"}, ["body", "shared_role"]),
        ({"shared_role": "guardian"}, ["body", "shared_role"]),
        ({"extra": 1}, ["body", "extra"]),
        ({"parameters": {"dataset": "expenses"}}, None),
        ({"parameters": expenses_body(columns=["amount", "amount"])}, None),
        ({"parameters": expenses_body(columns=["amount"], group_by="vendor")}, None),
        ({"parameters": {"dataset": "payroll", "format": "csv"}}, None),
    ],
)
def test_invalid_bodies_are_422(module_seeded_url, client, change, path):
    finance = actor(module_seeded_url, client, "finance_staff")
    body = {"name": unique("Inv"), "parameters": expenses_body(), **change}

    response = post_setup(client, finance, body)

    assert code_of(response) == (422, "validation_error")
    if path is not None:
        assert path in [e["path"] for e in response.json()["errors"]]


def test_an_80_character_name_is_accepted(module_seeded_url, client):
    finance = actor(module_seeded_url, client, "finance_staff")

    created = saved(client, finance, unique("N").ljust(80, "x"), expenses_body())

    assert len(created["name"]) == 80


def test_the_range_check_then_the_dataset_permission(module_seeded_url, client):
    admin = actor(module_seeded_url, client, "administrator")
    reversed_range = expenses_body(
        filters={"incurred_from": "2026-03-01", "incurred_to": "2026-02-01"}
    )

    # Step 6 (the #124 range check) before step 7 (may_export).
    response = post_setup(
        client, admin, {"name": unique("R"), "parameters": reversed_range}
    )
    assert code_of(response) == (422, "export_range_invalid")
    assert response.json()["errors"][0]["path"] == [
        "body",
        "parameters",
        "filters",
        "incurred_to",
    ]

    too_long = sessions_body("2026-01-01", "2027-01-02")
    assert code_of(
        post_setup(client, admin, {"name": unique("R"), "parameters": too_long})
    ) == (422, "export_range_invalid")

    # The Administrator may not export expenses (no expense.view).
    assert code_of(
        post_setup(client, admin, {"name": unique("R"), "parameters": expenses_body()})
    ) == (403, "permission_denied")


def test_names_are_unique_per_creator_case_insensitively(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    other = actor(url, client, "finance_staff")
    name = unique("Dup")
    saved(client, finance, name, expenses_body())

    again = post_setup(
        client, finance, {"name": name.upper(), "parameters": students_body()}
    )

    assert code_of(again) == (409, "report_setup_name_taken")
    # Another creator may use the same name.
    saved(client, other, name, expenses_body())


def test_the_limit_per_creator(module_seeded_url, client, monkeypatch):
    finance = actor(module_seeded_url, client, "finance_staff")
    monkeypatch.setattr(setup_errors, "MAX_SETUPS_PER_USER", 2)
    first = saved(client, finance, unique("L"), expenses_body())
    saved(client, finance, unique("L"), expenses_body())

    response = post_setup(
        client, finance, {"name": unique("L"), "parameters": expenses_body()}
    )

    assert code_of(response) == (409, "report_setup_limit_reached")
    assert response.json()["details"] == {"max": 2}
    # The limit is checked before the name.
    response = post_setup(
        client, finance, {"name": first["name"], "parameters": expenses_body()}
    )
    assert code_of(response) == (409, "report_setup_limit_reached")
    # Deleting one frees a place.
    assert delete_setup(client, finance, first["id"]).status_code == 204
    saved(client, finance, unique("L"), expenses_body())


# --- C3: sharing and re-checked authorization -----------------------------------------


def test_finance_shares_expenses_with_the_owner(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    owner = actor(url, client, "owner")
    admin = actor(url, client, "administrator")
    setup = saved(client, finance, unique("Share"), custom_expenses(), "owner")
    to_admins = saved(
        client, finance, unique("Share"), expenses_body(), "administrator"
    )

    [item] = [i for i in listed(client, owner) if i["id"] == setup["id"]]
    assert item["mine"] is False
    assert item["can_run"] is True
    assert item["created_by"]["id"] == str(finance.user.id)
    assert to_admins["id"] not in ids_listed(client, owner)

    # The Owner runs it: the job is the Owner's alone.
    response = post_export(client, owner, item["parameters"])
    assert response.status_code == 202, response.text
    job = response.json()
    [row] = run_sql(
        url,
        "SELECT requested_by_user_id FROM export_jobs WHERE id = :i",
        {"i": job["id"]},
    )
    assert row.requested_by_user_id == owner.user.id
    assert code_of(read(client, finance, export_path(job["id"]))) == (
        404,
        "not_found",
    )

    # The Administrator lacks expense.view: not listed even when shared with
    # administrator, and the parameters are refused by the export.
    admin_ids = ids_listed(client, admin)
    assert setup["id"] not in admin_ids
    assert to_admins["id"] not in admin_ids
    assert code_of(post_export(client, admin, item["parameters"])) == (
        403,
        "permission_denied",
    )


def test_a_shared_personal_dataset_needs_the_runners_reauthentication(
    module_seeded_url, client
):
    url = module_seeded_url
    coordinator = actor(url, client, "academic_coordinator")
    finance = actor(url, client, "finance_staff")
    setup = saved(
        client, coordinator, unique("Alum"), students_body("xlsx"), "finance_staff"
    )

    [item] = [i for i in listed(client, finance) if i["id"] == setup["id"]]
    assert item["mine"] is False

    assert code_of(post_export(client, finance, item["parameters"])) == (
        403,
        "reauth_required",
    )
    reauthenticate(client, finance.cookie)
    assert post_export(client, finance, item["parameters"]).status_code == 202


def test_a_private_setup_is_invisible_to_every_other_role(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    setup = saved(client, finance, unique("Priv"), students_body())

    for role in ("owner", "administrator", "academic_coordinator", "finance_staff"):
        other = actor(url, client, role)
        assert setup["id"] not in ids_listed(client, other), role
        body = {"version": 1, "name": unique("Hack")}
        assert code_of(patch_setup(client, other, setup["id"], body)) == (
            404,
            "not_found",
        ), role
        assert code_of(delete_setup(client, other, setup["id"])) == (
            404,
            "not_found",
        ), role
    assert stored_row(url, setup["id"]).name == setup["name"]


def test_a_sharee_may_not_change_or_delete(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    owner = actor(url, client, "owner")
    setup = saved(client, finance, unique("Ro"), expenses_body(), "owner")

    body = {"version": 1, "name": unique("Mine")}
    assert code_of(patch_setup(client, owner, setup["id"], body)) == (
        403,
        "permission_denied",
    )
    assert code_of(delete_setup(client, owner, setup["id"])) == (
        403,
        "permission_denied",
    )
    assert stored_row(url, setup["id"]).version == 1


def test_losing_the_role_or_the_permission_hides_a_shared_setup(
    module_seeded_url, client
):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    sharee = actor(url, client, "finance_staff")
    setup = saved(client, finance, unique("Lose"), expenses_body(), "finance_staff")
    assert setup["id"] in ids_listed(client, sharee)

    # The Coordinator role may export students and sessions, not expenses.
    set_roles(url, sharee.user, "finance_staff", "academic_coordinator")
    assert setup["id"] in ids_listed(client, sharee)
    set_roles(url, sharee.user, "academic_coordinator")
    assert setup["id"] not in ids_listed(client, sharee)
    assert code_of(patch_setup(client, sharee, setup["id"], {"version": 1})) == (
        404,
        "not_found",
    )

    # Shared with the coordinator role, the dataset is still not theirs.
    patched = patch_setup(
        client,
        finance,
        setup["id"],
        {"version": 1, "shared_role": "academic_coordinator"},
    )
    assert patched.status_code == 200, patched.text
    assert setup["id"] not in ids_listed(client, sharee)


def test_a_creator_who_loses_the_dataset_keeps_it_unrunnable(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    owner = actor(url, client, "owner")
    setup = saved(client, finance, unique("Kept"), expenses_body(), "owner")
    set_roles(url, finance.user, "administrator")

    [item] = [i for i in listed(client, finance) if i["id"] == setup["id"]]
    assert item["mine"] is True
    assert item["can_run"] is False
    # Not editable (the creator may not export the dataset)…
    assert code_of(
        patch_setup(client, finance, setup["id"], {"version": 1, "name": "Otro"})
    ) == (403, "permission_denied")
    # …the sharee still runs it…
    assert setup["id"] in ids_listed(client, owner)
    # …and the creator may delete it.
    assert delete_setup(client, finance, setup["id"]).status_code == 204
    assert stored_row(url, setup["id"]) is None


def test_an_inactive_creators_shared_setup_stays_listed(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    owner = actor(url, client, "owner")
    setup = saved(client, finance, unique("Inact"), expenses_body(), "owner")

    run_sql(
        url,
        "UPDATE users SET state = 'suspended' WHERE id = :u",
        {"u": finance.user.id},
    )

    [item] = [i for i in listed(client, owner) if i["id"] == setup["id"]]
    assert item["can_run"] is True
    assert item["created_by"]["id"] == str(finance.user.id)


# --- B4: update -----------------------------------------------------------------------


def test_an_update_renames_reshares_and_replaces_the_parameters(
    module_seeded_url, client
):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    owner = actor(url, client, "owner")
    setup = saved(client, finance, unique("Upd"), expenses_body(), "owner")
    new_name = unique("Ren")
    parameters = students_body("xlsx", columns=["given_names", "family_names"])

    response = patch_setup(
        client,
        finance,
        setup["id"],
        {"version": 1, "name": new_name, "parameters": parameters},
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == new_name
    assert updated["dataset"] == "students"
    assert updated["parameters"] == parameters
    assert updated["shared_role"] == "owner"
    assert updated["version"] == 2
    row = stored_row(url, setup["id"])
    assert (row.dataset, row.parameters) == ("students", parameters)

    # shared_role: null makes it private.
    response = patch_setup(
        client, finance, setup["id"], {"version": 2, "shared_role": None}
    )
    assert response.json()["shared_role"] is None
    assert setup["id"] not in ids_listed(client, owner)
    actions = [e.action for e in setup_audit(url, setup["id"])]
    assert actions == ["create", "update", "update"]
    changes = setup_audit(url, setup["id"])[1].changes
    changes = json.loads(changes) if isinstance(changes, str) else changes
    assert set(changes) >= {"name", "dataset", "parameters"}


def test_an_unchanged_body_is_a_no_op(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    parameters = custom_expenses()
    setup = saved(client, finance, unique("Same"), parameters, "owner")

    response = patch_setup(
        client,
        finance,
        setup["id"],
        {
            "version": 1,
            "name": setup["name"],
            "parameters": parameters,
            "shared_role": "owner",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == setup
    assert [e.action for e in setup_audit(url, setup["id"])] == ["create"]


def test_update_conflicts_and_checks(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    setup = saved(client, finance, unique("Chk"), expenses_body())
    taken = saved(client, finance, unique("Taken"), expenses_body())
    path_of = setup["id"]

    assert code_of(
        patch_setup(client, finance, path_of, {"version": 1, "name": None})
    ) == (422, "validation_error")
    assert code_of(patch_setup(client, finance, path_of, {"name": "x"})) == (
        422,
        "validation_error",
    )
    reversed_range = sessions_body("2026-03-01", "2026-02-01")
    assert code_of(
        patch_setup(
            client, finance, path_of, {"version": 1, "parameters": reversed_range}
        )
    ) == (422, "export_range_invalid")
    # An unknown setup is 404 before the range check.
    assert code_of(
        patch_setup(client, finance, GONE, {"version": 1, "parameters": reversed_range})
    ) == (404, "not_found")
    assert code_of(
        patch_setup(client, finance, path_of, {"version": 1, "name": taken["name"]})
    ) == (409, "report_setup_name_taken")
    # A case change of its own name is not "taken".
    response = patch_setup(
        client, finance, path_of, {"version": 1, "name": setup["name"].upper()}
    )
    assert response.status_code == 200, response.text
    # The second tab's PATCH: version 1 is stale now.
    assert code_of(
        patch_setup(client, finance, path_of, {"version": 1, "name": unique("Two")})
    ) == (409, "version_conflict")


def test_an_update_to_a_dataset_the_creator_may_not_export(module_seeded_url, client):
    coordinator = actor(module_seeded_url, client, "academic_coordinator")
    setup = saved(client, coordinator, unique("Ds"), students_body())

    response = patch_setup(
        client,
        coordinator,
        setup["id"],
        {"version": 1, "parameters": expenses_body()},
    )

    assert code_of(response) == (403, "permission_denied")


# --- B5: delete -----------------------------------------------------------------------


def test_delete_is_a_hard_delete_audited(module_seeded_url, client):
    url = module_seeded_url
    finance = actor(url, client, "finance_staff")
    setup = saved(client, finance, unique("Del"), expenses_body())

    response = delete_setup(client, finance, setup["id"])

    assert response.status_code == 204
    assert response.content == b""
    assert stored_row(url, setup["id"]) is None
    events = setup_audit(url, setup["id"])
    assert [e.action for e in events] == ["create", "delete"]
    assert events[-1].actor_user_id == finance.user.id
    assert code_of(delete_setup(client, finance, setup["id"])) == (404, "not_found")
    assert code_of(delete_setup(client, finance, str(uuid.uuid4()))) == (
        404,
        "not_found",
    )


def test_there_is_no_run_route(module_seeded_url, client):
    # E4: running is POST /exports with the stored parameters.
    finance = actor(module_seeded_url, client, "finance_staff")
    setup = saved(client, finance, unique("Run"), expenses_body())

    response = post_setup_path(client, finance, f"{setup_path(setup['id'])}/run")

    assert response.status_code in (404, 405)
