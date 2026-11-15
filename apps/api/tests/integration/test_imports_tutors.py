"""The ``tutors`` import kind end to end (#122 B6, D5, F3; E7)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import ADMINISTRATOR, OWNER, TUTOR
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.import_support import (
    action,
    clear_queue,
    codes,
    job_path,
    post_import,
    rows_of,
    run_job,
    tutors_csv,
)
from tests.integration.invitation_support import (
    INVITE_URL_PREFIX,
    reissue,
    sign_in,
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


def name(prefix: str = "tut") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}"


def user_by_name(url: str, username: str) -> object:
    rows = run_sql(url, "SELECT * FROM users WHERE username = :u", {"u": username})
    return rows[0] if rows else None


def roles_of(url: str, user_id: object) -> list[str]:
    rows = run_sql(
        url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :u ORDER BY r.code",
        {"u": user_id},
    )
    return [r.code for r in rows]


def test_d5_a_tutor_row_creates_a_pending_invitation(
    client, cookie, admin, module_seeded_url
):
    username = name()
    other = name()
    csv = tutors_csv(
        [
            {
                "username": username.upper(),
                "display_name": " Ana Tutora ",
                "email": "ana@example.com",
                "phone": "55 1234 5678",
            },
            {"username": other, "display_name": "Luis"},
        ]
    )
    job = post_import(client, cookie, "tutors", csv, file_name="tutores.csv").json()
    assert job["counts"]["ready"] == 2
    admin_cookie = sign_in(client, admin.username)
    action(client, admin_cookie, job["id"], "commit")

    run_job(module_seeded_url)

    user = user_by_name(module_seeded_url, username)
    assert user is not None
    assert (user.state, user.display_name, user.email, user.phone) == (
        "pending",
        "Ana Tutora",
        "ana@example.com",
        "55 1234 5678",
    )
    assert user.password_hash is None
    assert roles_of(module_seeded_url, user.id) == [TUTOR]
    [token] = run_sql(
        module_seeded_url,
        "SELECT kind, issued_by_user_id FROM account_tokens WHERE user_id = :u",
        {"u": user.id},
    )
    assert token.kind == "invitation"
    assert token.issued_by_user_id == admin.id
    [issued] = run_sql(
        module_seeded_url,
        "SELECT actor_user_id, details FROM audit_events"
        " WHERE action = 'identity.invitation_issued' AND record_id = :u",
        {"u": user.id},
    )
    assert issued.actor_user_id == admin.id
    assert issued.details["reissued"] is False
    assert issued.details["roles"] == [TUTOR]
    assert issued.details["import_job_id"] == job["id"]
    [created] = run_sql(
        module_seeded_url,
        "SELECT actor_user_id FROM audit_events"
        " WHERE record_type = 'users' AND record_id = :u AND action = 'create'",
        {"u": user.id},
    )
    assert created.actor_user_id == admin.id
    items = rows_of(client, cookie, job["id"])["items"]
    assert {r["record_id"] for r in items} == {
        str(user.id),
        str(user_by_name(module_seeded_url, other).id),  # type: ignore[union-attr]
    }
    # No link anywhere in the import; the admin reissues one (E7).
    assert "token" not in str(get(client, cookie, job_path(job["id"])).json())
    link = reissue(client, cookie, user.id)
    assert link.status_code == 200, link.text
    assert link.json()["invitation_url"].startswith(INVITE_URL_PREFIX)


def test_b6_username_rules(client, cookie, module_seeded_url):
    taken = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    tutor = create_user(module_seeded_url, roles=(TUTOR,))
    fresh = name()
    csv = tutors_csv(
        [
            {"username": "a!", "display_name": "Bad"},  # 2
            {"username": taken.username, "display_name": "Taken"},  # 3
            {"username": tutor.username, "display_name": "Tutor"},  # 4
            {"username": fresh, "display_name": "New"},  # 5
            {"username": fresh.upper(), "display_name": "Again"},  # 6
            {"username": name(), "display_name": "", "email": "x"},  # 7
        ]
    )

    job = post_import(client, cookie, "tutors", csv).json()
    items = {r["row_number"]: r for r in rows_of(client, cookie, job["id"])["items"]}

    assert {n: r["status"] for n, r in items.items()} == {
        2: "error",
        3: "error",
        4: "duplicate",
        5: "ready",
        6: "duplicate",
        7: "error",
    }
    assert codes(items[2]["errors"]) == {("username", "username_invalid")}
    assert codes(items[3]["errors"]) == {("username", "username_taken")}
    assert codes(items[4]["warnings"]) == {(None, "duplicate_existing")}
    assert codes(items[6]["warnings"]) == {(None, "duplicate_in_file")}
    assert codes(items[7]["errors"]) == {
        ("display_name", "required"),
        ("email", "invalid_email"),
    }


def test_d2_a_username_taken_meanwhile_fails_or_skips(
    client, cookie, module_seeded_url
):
    raced = name()
    tutor_meanwhile = name()
    csv = tutors_csv(
        [
            {"username": raced, "display_name": "A"},
            {"username": tutor_meanwhile, "display_name": "B"},
        ]
    )
    job = post_import(client, cookie, "tutors", csv).json()
    create_user(module_seeded_url, roles=(ADMINISTRATOR,), username=raced)
    create_user(module_seeded_url, roles=(TUTOR,), username=tutor_meanwhile)
    action(client, cookie, job["id"], "commit")

    run_job(module_seeded_url)

    items = rows_of(client, cookie, job["id"])["items"]
    assert [r["status"] for r in items] == ["failed", "skipped"]
    assert codes(items[0]["errors"]) == {("username", "username_taken")}
    assert codes(items[1]["warnings"]) == {(None, "duplicate_existing")}
