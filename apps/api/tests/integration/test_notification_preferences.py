"""Notification preferences through the real app (#119 C1-C4, D1, D2).

The catalogue comes from the real wiring (``build_app``); the enforcement
tests register two test types of their own (one optional, one mandatory)
for the ``tutor`` role and remove them afterwards.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.administration.notification_preferences import catalogue_types
from app.modules.administration.notifications import NOTIFICATION_CHANNEL
from app.modules.administration.notifications_dev import (
    DEV_DEMO_TYPE,
    register_dev_notification_type,
)
from app.modules.administration.schemas import (
    NotificationPreferenceChange,
    NotificationPreferencesBody,
)
from app.modules.identity.permissions import FINANCE_STAFF, GUARDIAN, OWNER, STUDENT
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql, run_sql_async, short_lived_engine
from tests.integration.lifecycle_support import code_of, in_uow, signed_in

PREFERENCES = "/api/v1/notifications/preferences"
OK = (200, None)

OPTIONAL = "test.muteable_notice"
REQUIRED = "test.required_notice"
TUTOR = frozenset({"tutor"})


class LabelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as c:
        yield c


@pytest.fixture
def test_types() -> Iterator[None]:
    administration.register_notification_type(
        OPTIONAL, LabelParams, recipient_roles=TUTOR
    )
    administration.register_notification_type(
        REQUIRED, LabelParams, mandatory=True, recipient_roles=TUTOR
    )
    yield
    administration.unregister_notification_type(OPTIONAL)
    administration.unregister_notification_type(REQUIRED)


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=("tutor",))


def get(client: TestClient, cookie: SetCookie) -> Any:
    client.cookies.clear()
    return client.get(PREFERENCES, headers=cookie_header(cookie))


def patch(client: TestClient, cookie: SetCookie, body: Any) -> Any:
    client.cookies.clear()
    return client.patch(PREFERENCES, headers=signed_headers(cookie), json=body)


def change(notification_type: str, enabled: bool) -> dict[str, Any]:
    return {"notification_type": notification_type, "enabled": enabled}


def items_of(response: Any) -> dict[str, tuple[bool, bool]]:
    assert code_of(response) == OK, response.text
    items = response.json()["items"]
    codes = [item["notification_type"] for item in items]
    assert codes == sorted(codes)
    return {i["notification_type"]: (i["mandatory"], i["enabled"]) for i in items}


def opt_outs(url: str, user: CreatedUser) -> set[str]:
    rows = run_sql(
        url,
        "SELECT notification_type FROM notification_opt_outs WHERE user_id = :u",
        {"u": user.id},
    )
    return {row.notification_type for row in rows}


def notifications_of(url: str, user: CreatedUser, notification_type: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM notifications"
        " WHERE recipient_user_id = :u AND notification_type = :t",
        {"u": user.id, "t": notification_type},
    )
    return int(row.n)


def notify(url: str, user: CreatedUser, notification_type: str) -> uuid.UUID | None:
    return in_uow(
        url,
        lambda session: administration.create_notification(
            session,
            recipient_user_id=user.id,
            notification_type=notification_type,
            params={"label": "x"},
        ),
    )


def principal(user: CreatedUser, roles: frozenset[str] = TUTOR) -> Principal:
    return Principal(user_id=user.id, roles=roles, permissions=frozenset())


# --- C1: the catalogue per role ------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "present", "absent"),
    [
        (
            GUARDIAN,
            {"billing.payment_reminder", "academics.assignment_set"},
            {"payroll.period_finalized", "administration.approval_assigned"},
        ),
        (
            STUDENT,
            {"academics.assignment_set", "scheduling.session_cancelled"},
            {"billing.payment_reminder", "scheduling.offer_received"},
        ),
        (
            "tutor",
            {"payroll.period_finalized", "scheduling.tutor_substituted"},
            {"academics.assignment_set", "scheduling.session_tutor_changed"},
        ),
        (
            FINANCE_STAFF,
            {"administration.approval_assigned"},
            {"scheduling.offer_declined", "scheduling.session_cancelled"},
        ),
        (
            OWNER,
            {"administration.approval_assigned", "scheduling.offer_declined"},
            {"billing.payment_reminder", "payroll.period_finalized"},
        ),
    ],
)
def test_each_role_sees_the_types_of_its_roles_all_on(
    module_seeded_url, client, role, present, absent
):
    member = create_user(module_seeded_url, roles=(role,))

    items = items_of(get(client, signed_in(client, member.username)))

    assert set(items) == set(catalogue_types(frozenset({role})))
    assert present <= set(items)
    assert not absent & set(items)
    assert DEV_DEMO_TYPE not in items
    assert all(enabled for _, enabled in items.values())
    for code, (mandatory, _) in items.items():
        info = administration.notification_type_info(code)
        assert info is not None
        assert mandatory is info.mandatory


def test_a_user_with_several_roles_sees_the_union(module_seeded_url, client):
    member = create_user(module_seeded_url, roles=("tutor", OWNER))

    items = items_of(get(client, signed_in(client, member.username)))

    assert set(items) == set(catalogue_types(frozenset({"tutor"}))) | set(
        catalogue_types(frozenset({OWNER}))
    )


def test_the_catalogue_is_one_statement(module_seeded_url, tutor):
    statements, result = count_statements(
        module_seeded_url,
        lambda session: administration.list_notification_preferences(
            session, principal=principal(tutor)
        ),
    )

    assert statements == 1
    assert result.items


# --- C2: errors -----------------------------------------------------------------------


def assert_refused(
    response: Any, code: str, indexes: list[int], url: str, user: CreatedUser
) -> None:
    assert code_of(response) == (422, code), response.text
    assert [e["path"] for e in response.json()["errors"]] == [
        ["body", "items", index, "notification_type"] for index in indexes
    ]
    assert {e["code"] for e in response.json()["errors"]} == {code}
    assert opt_outs(url, user) == set()


@pytest.mark.parametrize(
    ("items", "code", "indexes"),
    [
        (
            [
                change("payroll.period_finalized", False),
                change("staff.qualification_approved", False),
                change("payroll.period_finalized", True),
            ],
            "notification_type_duplicate",
            [2],
        ),
        # The duplicate check comes before the unknown one.
        (
            [change("test.never_registered", False)] * 2,
            "notification_type_duplicate",
            [1],
        ),
        (
            [
                change("payroll.period_finalized", False),
                change("test.never_registered", False),
                change(DEV_DEMO_TYPE, False),
                change("billing.payment_reminder", False),  # a guardian's type
            ],
            "notification_type_unknown",
            [1, 2, 3],
        ),
        # The unknown check comes before the mandatory one.
        (
            [
                change("scheduling.session_cancelled", False),
                change("billing.payment_reminder", True),
            ],
            "notification_type_unknown",
            [1],
        ),
        (
            [
                change("payroll.period_finalized", False),
                change("scheduling.session_cancelled", False),
                change("scheduling.tutor_substituted", False),
            ],
            "notification_type_mandatory",
            [1, 2],
        ),
    ],
)
def test_refused_changes_name_each_item_and_write_nothing(
    module_seeded_url, client, tutor, items, code, indexes
):
    register_dev_notification_type()
    cookie = signed_in(client, tutor.username)

    assert_refused(
        patch(client, cookie, {"items": items}), code, indexes, module_seeded_url, tutor
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": []},
        {"items": [change("payroll.period_finalized", False)] * 51},
        {"items": [{"notification_type": "payroll.period_finalized"}]},
        {"items": [{**change("payroll.period_finalized", False), "user_id": "x"}]},
        {"items": [change("payroll.period_finalized", False)], "user_id": "x"},
        {"items": [{"notification_type": 1, "enabled": False}]},
    ],
)
def test_a_badly_shaped_body_is_a_validation_error(
    module_seeded_url, client, tutor, body
):
    cookie = signed_in(client, tutor.username)

    assert code_of(patch(client, cookie, body)) == (422, "validation_error")
    assert opt_outs(module_seeded_url, tutor) == set()


def test_turning_a_mandatory_type_on_is_a_no_op(module_seeded_url, client, tutor):
    cookie = signed_in(client, tutor.username)

    response = patch(
        client, cookie, {"items": [change("scheduling.session_cancelled", True)]}
    )

    assert items_of(response)["scheduling.session_cancelled"] == (True, True)
    assert opt_outs(module_seeded_url, tutor) == set()


# --- C3: changes ---------------------------------------------------------------------


def test_off_and_on_round_trip_and_a_resend_changes_nothing(
    module_seeded_url, client, tutor
):
    cookie = signed_in(client, tutor.username)
    off = {
        "items": [
            change("payroll.period_finalized", False),
            change("staff.qualification_approved", False),
        ]
    }

    first = items_of(patch(client, cookie, off))
    again = items_of(patch(client, cookie, off))

    assert first == again == items_of(get(client, cookie))
    assert first["payroll.period_finalized"] == (False, False)
    assert first["staff.qualification_approved"] == (False, False)
    assert first["staff.qualification_rejected"] == (False, True)
    assert opt_outs(module_seeded_url, tutor) == {
        "payroll.period_finalized",
        "staff.qualification_approved",
    }

    mixed = {
        "items": [
            change("payroll.period_finalized", True),
            change("staff.qualification_rejected", False),
        ]
    }
    after = items_of(patch(client, cookie, mixed))

    assert after["payroll.period_finalized"] == (False, True)
    assert after["staff.qualification_rejected"] == (False, False)
    assert opt_outs(module_seeded_url, tutor) == {
        "staff.qualification_approved",
        "staff.qualification_rejected",
    }
    # Turning on a type that is already on changes nothing.
    assert items_of(
        patch(client, cookie, {"items": [change("payroll.period_finalized", True)]})
    ) == items_of(get(client, cookie))


def test_concurrent_changes_of_one_user_never_fail(module_seeded_url, tutor):
    codes = sorted(
        code for code, info in catalogue_types(TUTOR).items() if not info.mandatory
    )
    first, second = codes[:3], codes[3:6]

    def body(off: list[str], on: list[str]) -> NotificationPreferencesBody:
        return NotificationPreferencesBody(
            items=[
                NotificationPreferenceChange(notification_type=c, enabled=False)
                for c in off
            ]
            + [
                NotificationPreferenceChange(notification_type=c, enabled=True)
                for c in on
            ]
        )

    async def update(engine: Any, change_body: NotificationPreferencesBody) -> None:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await administration.update_notification_preferences(
                uow.session, principal=principal(tutor), body=change_body
            )

    async def race() -> None:
        engines = [short_lived_engine(module_seeded_url) for _ in range(2)]
        try:
            for _ in range(5):
                await asyncio.gather(
                    update(engines[0], body(first, second)),
                    update(engines[1], body(second, first)),
                )
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(race())

    # The two run one after the other: the last one's state wins whole.
    assert opt_outs(module_seeded_url, tutor) in (set(first), set(second))


# --- C4: own rows only -------------------------------------------------------------


def test_a_user_changes_only_their_own_preferences(module_seeded_url, client, tutor):
    other = create_user(module_seeded_url, roles=("tutor",))
    cookie = signed_in(client, tutor.username)

    patch(client, cookie, {"items": [change("payroll.period_finalized", False)]})

    assert opt_outs(module_seeded_url, tutor) == {"payroll.period_finalized"}
    assert opt_outs(module_seeded_url, other) == set()
    other_items = items_of(get(client, signed_in(client, other.username)))
    assert other_items["payroll.period_finalized"] == (False, True)


# --- D1, D2: enforcement in create_notification --------------------------------------


def test_a_muted_optional_type_is_not_created_and_a_mandatory_one_is(
    module_seeded_url, client, tutor, test_types
):
    cookie = signed_in(client, tutor.username)
    response = patch(client, cookie, {"items": [change(OPTIONAL, False)]})
    assert items_of(response)[OPTIONAL] == (False, False)
    # D2: a forged opt-out row for a mandatory type is ignored.
    run_sql(
        module_seeded_url,
        "INSERT INTO notification_opt_outs (user_id, notification_type)"
        " VALUES (:u, :t)",
        {"u": tutor.id, "t": REQUIRED},
    )

    assert notify(module_seeded_url, tutor, OPTIONAL) is None
    assert notify(module_seeded_url, tutor, REQUIRED) is not None
    assert notifications_of(module_seeded_url, tutor, OPTIONAL) == 0
    assert notifications_of(module_seeded_url, tutor, REQUIRED) == 1
    assert items_of(get(client, cookie))[REQUIRED] == (True, True)

    # Bad params still raise for a muted type.
    with pytest.raises(ValueError):
        in_uow(
            module_seeded_url,
            lambda session: administration.create_notification(
                session,
                recipient_user_id=tutor.id,
                notification_type=OPTIONAL,
                params={"wrong": 1},
            ),
        )

    # Turned back on: only later notifications arrive (E6, nothing backfilled).
    patch(client, cookie, {"items": [change(OPTIONAL, True)]})
    assert notify(module_seeded_url, tutor, OPTIONAL) is not None
    assert notifications_of(module_seeded_url, tutor, OPTIONAL) == 1


def test_the_lookup_is_one_statement_and_mandatory_types_skip_it(
    module_seeded_url, tutor, test_types
):
    def create(notification_type: str) -> Any:
        return lambda session: administration.create_notification(
            session,
            recipient_user_id=tutor.id,
            notification_type=notification_type,
            params={"label": "x"},
        )

    optional, _ = count_statements(module_seeded_url, create(OPTIONAL))
    mandatory, _ = count_statements(module_seeded_url, create(REQUIRED))
    run_sql(
        module_seeded_url,
        "INSERT INTO notification_opt_outs (user_id, notification_type)"
        " VALUES (:u, :t)",
        {"u": tutor.id, "t": OPTIONAL},
    )
    muted, result = count_statements(module_seeded_url, create(OPTIONAL))

    assert (optional, mandatory, muted, result) == (mandatory + 1, mandatory, 1, None)


@pytest.mark.anyio
async def test_a_muted_notification_sends_no_notify(
    module_seeded_url: str, tutor: CreatedUser, test_types: None
) -> None:
    await run_sql_async(
        module_seeded_url,
        "INSERT INTO notification_opt_outs (user_id, notification_type)"
        " VALUES (:u, :t)",
        {"u": tutor.id, "t": OPTIONAL},
    )
    listen_engine = short_lived_engine(module_seeded_url)
    work_engine = short_lived_engine(module_seeded_url)
    payloads: asyncio.Queue[str] = asyncio.Queue()

    async def create(notification_type: str) -> uuid.UUID | None:
        async with UnitOfWork(create_sessionmaker(work_engine)) as uow:
            return await administration.create_notification(
                uow.session,
                recipient_user_id=tutor.id,
                notification_type=notification_type,
                params={"label": "x"},
            )

    try:
        async with listen_engine.connect() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            assert raw is not None
            await raw.add_listener(
                NOTIFICATION_CHANNEL, lambda *args: payloads.put_nowait(args[3])
            )
            muted = await create(OPTIONAL)
            sentinel = await create(REQUIRED)
            first = await asyncio.wait_for(payloads.get(), 5)
            await asyncio.sleep(0.2)
    finally:
        await listen_engine.dispose()
        await work_engine.dispose()

    assert muted is None
    # The first (and only) payload is the sentinel's: the muted one sent none.
    assert json.loads(first) == {
        "user_id": str(tutor.id),
        "notification_id": str(sentinel),
    }
    assert payloads.empty()


# --- Access ---------------------------------------------------------------------------


def test_both_routes_need_the_privacy_notice_first(module_seeded_url, client):
    pending = create_user(
        module_seeded_url, roles=("tutor",), privacy_notice_accepted=False
    )
    cookie = signed_in(client, pending.username)

    assert code_of(get(client, cookie)) == (403, "privacy_notice_required")
    response = patch(
        client, cookie, {"items": [change("payroll.period_finalized", False)]}
    )
    assert code_of(response) == (403, "privacy_notice_required")
    assert opt_outs(module_seeded_url, pending) == set()


def test_signed_out_is_401_and_a_patch_without_the_csrf_token_fails(
    module_seeded_url, client, tutor
):
    client.cookies.clear()
    assert client.get(PREFERENCES).status_code == 401
    assert client.patch(PREFERENCES, json={"items": []}).status_code == 401

    cookie = signed_in(client, tutor.username)
    client.cookies.clear()
    response = client.patch(
        PREFERENCES,
        headers=cookie_header(cookie),
        json={"items": [change("payroll.period_finalized", False)]},
    )

    assert code_of(response) == (403, "csrf_failed")
    assert opt_outs(module_seeded_url, tutor) == set()
