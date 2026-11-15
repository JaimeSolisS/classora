"""Session pay rules (#441 B, C, D, G3-G5) on real PostgreSQL through the real
app: the routes, their error codes, the readers, the audit masks, the write
lock and who may call what. The migration, the CHECKs and the guard by SQL are
``test_0075_*`` in ``test_migrations.py``; the route matrix is
``tests/authz/matrix.toml``.

The clock is frozen on 2051-03-10 (the center's zone). The guard reads the
database's date, so to it every rule dated 2051 is upcoming. Each test starts
from the three seeds.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import VersionConflictError
from app.core.money import Money, Rate
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    STUDENT,
    TUTOR,
)
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import PayRuleInEffectError
from app.modules.payroll.schemas import SessionPayRuleCreate
from app.workflows import session_pay_rules
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2051, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2051, 3, 10)
ROOT = "/api/v1/payroll/pay-rules"
EVENTS = ("late_cancellation", "no_show", "makeup")
SEEDED = date(2000, 1, 1)
ITEM_KEYS = {
    "id",
    "event",
    "effective_from",
    "mode",
    "percent",
    "amount",
    "created_by_user_id",
    "created_at",
}
DENIED = (403, "permission_denied")
HOLD_SECONDS = 0.4


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def finance_user(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


@pytest.fixture(scope="module")
def staff(client: TestClient, finance_user: CreatedUser) -> SetCookie:
    return sign_in(client, finance_user.username)


@pytest.fixture(autouse=True)
def seeds_only(module_seeded_url: str, frozen: FrozenClock) -> Iterator[None]:
    yield
    frozen.set(NOW)
    url = module_seeded_url
    guard = "ALTER TABLE session_pay_rules {} TRIGGER session_pay_rules_no_change"
    run_sql(url, guard.format("DISABLE"))
    run_sql(url, "DELETE FROM session_pay_rules WHERE effective_from <> '2000-01-01'")
    run_sql(url, guard.format("ENABLE"))


def overview(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, ROOT)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def latest_id(client: TestClient, cookie: SetCookie, event: str) -> str:
    rules = overview(client, cookie)[event]
    rule: dict[str, Any] = (rules["upcoming"] or [rules["current"]])[-1]
    return str(rule["id"])


def rule_body(client: TestClient, cookie: SetCookie, **changes: Any) -> dict[str, Any]:
    event = changes.get("event", "no_show")
    return {
        "based_on_id": latest_id(client, cookie, event),
        "event": event,
        "effective_from": "2051-04-01",
        "mode": "percent",
        "percent": "50.0000",
        "amount": None,
        **changes,
    }


def create(client: TestClient, cookie: SetCookie, **changes: Any) -> Response:
    return post(client, cookie, ROOT, rule_body(client, cookie, **changes))


def created(client: TestClient, cookie: SetCookie, **changes: Any) -> dict[str, Any]:
    response = create(client, cookie, **changes)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def add_row(url: str, event: str, on: date) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO session_pay_rules (event, effective_from, mode)"
        " VALUES (:event, :on, 'none') RETURNING id",
        {"event": event, "on": on},
    )
    return uuid.UUID(str(row.id))


def field_codes(response: Response) -> list[str]:
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    return [error["code"] for error in response.json()["errors"]]


# --- D1: the read --------------------------------------------------------------------


def test_the_overview_shows_the_seeds(client, staff):
    body = overview(client, staff)

    assert set(body) == {"today", *EVENTS}
    assert body["today"] == "2051-03-10"
    assert set(body["no_show"]) == {"current", "upcoming", "history"}
    assert set(body["no_show"]["current"]) == ITEM_KEYS
    assert {
        event: (
            body[event]["current"]["event"],
            body[event]["current"]["effective_from"],
            body[event]["current"]["mode"],
            body[event]["current"]["percent"],
            body[event]["current"]["amount"],
            body[event]["current"]["created_by_user_id"],
            body[event]["upcoming"],
            body[event]["history"],
        )
        for event in EVENTS
    } == {
        "late_cancellation": (
            "late_cancellation",
            "2000-01-01",
            "none",
            None,
            None,
            None,
            [],
            [],
        ),
        "no_show": ("no_show", "2000-01-01", "percent", "100.0000", None, None, [], []),
        "makeup": ("makeup", "2000-01-01", "percent", "100.0000", None, None, [], []),
    }


def test_the_lists_are_ordered_and_per_event(client, staff, module_seeded_url):
    url = module_seeded_url
    add_row(url, "makeup", date(2030, 1, 1))
    add_row(url, "makeup", date(2040, 1, 1))
    add_row(url, "makeup", TODAY)
    add_row(url, "makeup", date(2051, 5, 1))
    add_row(url, "makeup", date(2051, 4, 1))

    body = overview(client, staff)

    assert body["makeup"]["current"]["effective_from"] == "2051-03-10"
    assert [v["effective_from"] for v in body["makeup"]["upcoming"]] == [
        "2051-04-01",
        "2051-05-01",
    ]
    assert [v["effective_from"] for v in body["makeup"]["history"]] == [
        "2040-01-01",
        "2030-01-01",
        "2000-01-01",
    ]
    assert body["no_show"]["upcoming"] == []
    assert body["no_show"]["history"] == []


def test_the_history_stops_at_50_for_fixed_statements(client, staff, module_seeded_url):
    url = module_seeded_url
    run_sql(
        url,
        "INSERT INTO session_pay_rules (event, effective_from, mode)"
        " SELECT 'no_show', DATE '2001-01-01' + n, 'none'"
        " FROM generate_series(0, 59) AS n",
    )

    body = overview(client, staff)

    assert body["no_show"]["current"]["effective_from"] == "2001-03-01"
    assert len(body["no_show"]["history"]) == 50
    statements, _ = count_statements(
        url, lambda s: payroll.get_session_pay_rule_overview(s, today=TODAY)
    )
    assert statements == 6


# --- C2, D2: a new version -----------------------------------------------------------


def test_a_percent_version_is_created(client, staff, finance_user):
    item = created(client, staff, percent="12.3456")

    assert set(item) == ITEM_KEYS
    assert (
        item["event"],
        item["effective_from"],
        item["mode"],
        item["percent"],
        item["amount"],
        item["created_by_user_id"],
    ) == ("no_show", "2051-04-01", "percent", "12.3456", None, str(finance_user.id))
    assert overview(client, staff)["no_show"]["upcoming"] == [item]


def test_a_fixed_version_is_created(client, staff):
    item = created(client, staff, mode="fixed", percent=None, amount="150.50")

    assert (item["mode"], item["percent"], item["amount"]) == (
        "fixed",
        None,
        "150.50",
    )


def test_a_none_version_is_created(client, staff):
    item = created(client, staff, mode="none", percent=None)

    assert (item["mode"], item["percent"], item["amount"]) == ("none", None, None)


@pytest.mark.parametrize(
    "values",
    [
        {"percent": "0"},
        {"percent": "1000"},
        {"mode": "fixed", "percent": None, "amount": "0.00"},
    ],
)
def test_the_limits_are_accepted(client, staff, values):
    assert create(client, staff, **values).status_code == 201


def test_a_stale_base_is_a_version_conflict(client, staff):
    stale = latest_id(client, staff, "no_show")
    created(client, staff)

    response = create(client, staff, based_on_id=stale, effective_from="2051-05-01")

    assert code_of(response) == (409, "version_conflict")


def test_the_base_of_another_event_is_a_version_conflict(client, staff):
    other = latest_id(client, staff, "makeup")

    assert code_of(create(client, staff, based_on_id=other)) == (
        409,
        "version_conflict",
    )


def test_an_unknown_base_is_a_version_conflict(client, staff):
    assert code_of(create(client, staff, based_on_id=str(uuid.uuid4()))) == (
        409,
        "version_conflict",
    )


def test_the_conflict_comes_before_the_date(client, staff):
    other = latest_id(client, staff, "makeup")

    response = create(client, staff, based_on_id=other, effective_from="2051-03-09")

    assert code_of(response) == (409, "version_conflict")


def test_a_past_date_is_refused(client, staff):
    assert code_of(create(client, staff, effective_from="2051-03-09")) == (
        422,
        "invalid_effective_from",
    )


def test_a_rule_dated_today_is_in_force_at_once(client, staff):
    item = created(client, staff, effective_from="2051-03-10")

    body = overview(client, staff)["no_show"]
    assert body["current"] == item
    assert body["upcoming"] == []
    assert [v["effective_from"] for v in body["history"]] == ["2000-01-01"]


@pytest.mark.parametrize("on", ["2051-04-01", "2051-03-31"])
def test_a_second_upcoming_version_must_be_later(client, staff, on):
    created(client, staff)

    assert code_of(create(client, staff, effective_from=on)) == (
        422,
        "invalid_effective_from",
    )


def test_two_upcoming_versions_and_the_earlier_goes(client, staff):
    earlier = created(client, staff)
    later = created(client, staff, effective_from="2051-05-01")

    assert delete(client, staff, f"{ROOT}/{earlier['id']}").status_code == 204

    assert overview(client, staff)["no_show"]["upcoming"] == [later]


def test_dates_are_per_event(client, staff):
    created(client, staff, effective_from="2051-05-01")

    assert create(client, staff, event="makeup").status_code == 201


def test_today_is_the_centers_not_utc(client, staff, frozen, module_seeded_url):
    url = module_seeded_url
    body = SessionPayRuleCreate.model_validate(
        rule_body(client, staff, effective_from="2051-03-10")
    )
    frozen.set(datetime(2051, 3, 11, 3, 0, tzinfo=UTC))  # 21:00 on the 10th, center

    assert in_uow(url, session_pay_rules.get_overview).today == TODAY
    assert in_uow(url, lambda s: session_pay_rules.create_rule(s, body)).mode == (
        "percent"
    )


def test_a_field_left_out_or_extra_is_422(client, staff):
    body = rule_body(client, staff)
    for field in body:
        partial = {k: v for k, v in body.items() if k != field}
        assert post(client, staff, ROOT, partial).status_code == 422, field
    assert post(client, staff, ROOT, {**body, "note": "x"}).status_code == 422


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"percent": "1.23456"}, "percent", "rate_precision"),
        ({"percent": 50}, "percent", "rate_invalid"),
        ({"percent": 50.5}, "percent", "rate_invalid"),
        ({"percent": "abc"}, "percent", "rate_invalid"),
        ({"percent": "-0.0001"}, "percent", "percent_out_of_range"),
        ({"percent": "1000.0001"}, "percent", "percent_out_of_range"),
        (
            {"mode": "fixed", "percent": None, "amount": "1.234"},
            "amount",
            "money_precision",
        ),
        (
            {"mode": "fixed", "percent": None, "amount": 10},
            "amount",
            "money_invalid",
        ),
        (
            {"mode": "fixed", "percent": None, "amount": "-0.01"},
            "amount",
            "amount_negative",
        ),
        ({"percent": None}, "percent", "pay_rule_values_mismatch"),
        ({"amount": "1.00"}, "amount", "pay_rule_values_mismatch"),
        ({"mode": "fixed"}, "percent", "pay_rule_values_mismatch"),
        ({"mode": "fixed", "percent": None}, "amount", "pay_rule_values_mismatch"),
        ({"mode": "none"}, "percent", "pay_rule_values_mismatch"),
        (
            {"mode": "none", "percent": None, "amount": "0.00"},
            "amount",
            "pay_rule_values_mismatch",
        ),
    ],
)
def test_body_errors(client, staff, overrides, field, code):
    response = create(client, staff, **overrides)

    assert code in field_codes(response)
    assert any(
        error["code"] == code and error["path"][-1] == field
        for error in response.json()["errors"]
    )


@pytest.mark.parametrize(
    "overrides", [{"event": "bonus"}, {"mode": "half"}, {"effective_from": "soon"}]
)
def test_unknown_values_are_422(client, staff, overrides):
    body = {**rule_body(client, staff), **overrides}

    assert post(client, staff, ROOT, body).status_code == 422


# --- C3, D3: deleting ----------------------------------------------------------------


def test_an_upcoming_version_is_deleted(client, staff):
    item = created(client, staff)

    assert delete(client, staff, f"{ROOT}/{item['id']}").status_code == 204

    assert overview(client, staff)["no_show"]["upcoming"] == []


def test_an_unknown_id_is_404(client, staff):
    assert code_of(delete(client, staff, f"{ROOT}/{uuid.uuid4()}")) == (
        404,
        "not_found",
    )


def test_a_rule_in_force_or_past_is_not_deleted(client, staff, module_seeded_url):
    url = module_seeded_url
    past = add_row(url, "no_show", date(2030, 1, 1))
    today = add_row(url, "no_show", TODAY)
    seed = overview(client, staff)["makeup"]["current"]["id"]

    for rule in (past, today, seed):
        assert code_of(delete(client, staff, f"{ROOT}/{rule}")) == (
            409,
            "pay_rule_in_effect",
        )


def test_the_guards_refusal_is_the_same_409(module_seeded_url):
    """The application's clock says upcoming and the database says past."""
    rule = add_row(module_seeded_url, "no_show", date(2020, 1, 1))

    with pytest.raises(PayRuleInEffectError):
        in_uow(
            module_seeded_url,
            lambda s: payroll.delete_session_pay_rule(s, rule, today=date(2019, 1, 1)),
        )


# --- G5, B3: the readers -------------------------------------------------------------


def test_the_reader_boundaries(module_seeded_url):
    url = module_seeded_url
    on = date(2030, 6, 15)
    rule = add_row(url, "late_cancellation", on)

    async def read(session: AsyncSession) -> list[uuid.UUID]:
        return [
            (await payroll.session_pay_rule_in_effect(session, event, day)).id
            for event, day in (
                ("late_cancellation", date(2030, 6, 14)),
                ("late_cancellation", on),
                ("late_cancellation", date(2030, 6, 16)),
                ("makeup", on),
            )
        ]

    before, exactly, after, other = in_uow(url, read)

    assert before != rule
    assert (exactly, after) == (rule, rule)
    assert other not in (before, rule)


def test_the_reader_is_one_statement_and_typed(client, staff, module_seeded_url):
    url = module_seeded_url
    created(client, staff, percent="12.5")
    created(client, staff, event="makeup", mode="fixed", percent=None, amount="150.00")

    statements, version = count_statements(
        url,
        lambda s: payroll.session_pay_rule_in_effect(s, "no_show", date(2051, 4, 1)),
    )

    assert statements == 1
    assert (version.mode, version.percent, version.amount) == (
        "percent",
        Rate("12.5"),
        None,
    )
    _, fixed = count_statements(
        url, lambda s: payroll.session_pay_rule_in_effect(s, "makeup", date(2051, 4, 1))
    )
    assert (fixed.mode, fixed.percent, fixed.amount) == (
        "fixed",
        None,
        Money("150.00"),
    )


def test_the_batch_read_is_one_statement_for_every_event(
    client, staff, module_seeded_url
):
    created(client, staff)

    statements, versions = count_statements(
        module_seeded_url, payroll.session_pay_rule_versions
    )

    assert statements == 1
    assert [(v.event, v.effective_from) for v in versions] == [
        ("late_cancellation", SEEDED),
        ("makeup", SEEDED),
        ("no_show", date(2051, 4, 1)),
        ("no_show", SEEDED),
    ]


# --- C1, G3: two writers -------------------------------------------------------------


def _race(url: str, call: Callable[[AsyncSession], Awaitable[Any]]) -> list[Any]:
    """The second writer starts while the first holds the lock."""

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        inserted = asyncio.Event()

        async def first() -> Any:
            async with UnitOfWork(sessions) as uow:
                made = await call(uow.session)
                inserted.set()
                await asyncio.sleep(HOLD_SECONDS)
                return made

        async def second() -> Any:
            await inserted.wait()
            async with UnitOfWork(sessions) as uow:
                return await call(uow.session)

        try:
            return await asyncio.gather(first(), second(), return_exceptions=True)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_two_concurrent_creates_one_wins(client, staff, module_seeded_url):
    url = module_seeded_url
    body = SessionPayRuleCreate.model_validate(rule_body(client, staff))

    made, refused = _race(url, lambda s: session_pay_rules.create_rule(s, body))

    assert made.effective_from == date(2051, 4, 1)
    assert isinstance(refused, VersionConflictError)
    assert len(overview(client, staff)["no_show"]["upcoming"]) == 1


def test_writes_of_two_events_share_the_lock(client, staff, module_seeded_url):
    no_show = SessionPayRuleCreate.model_validate(rule_body(client, staff))
    makeup = SessionPayRuleCreate.model_validate(
        rule_body(client, staff, event="makeup")
    )
    bodies = iter((no_show, makeup))

    results = _race(
        module_seeded_url,
        lambda s: session_pay_rules.create_rule(s, next(bodies)),
    )

    assert {result.event for result in results} == {"no_show", "makeup"}


# --- G4: the audit log ---------------------------------------------------------------

PERCENT_CANARY = "731.9137"
AMOUNT_CANARY = "84621.37"


def _stored_audit(url: str) -> str:
    rows = run_sql(
        url,
        "SELECT CAST(to_jsonb(a) AS text) AS row FROM audit_events a"
        " WHERE a.record_type = 'session_pay_rules'",
    )
    return "\n".join(row.row for row in rows)


def test_the_audit_log_masks_the_values(
    client, staff, finance_user, module_seeded_url, caplog
):
    url = module_seeded_url
    caplog.set_level(logging.DEBUG)
    percent = created(client, staff, percent=PERCENT_CANARY)
    amount = created(
        client,
        staff,
        event="makeup",
        mode="fixed",
        percent=None,
        amount=AMOUNT_CANARY,
    )
    assert delete(client, staff, f"{ROOT}/{percent['id']}").status_code == 204

    stored = _stored_audit(url)
    assert "[masked]" in stored
    assert PERCENT_CANARY not in stored
    assert AMOUNT_CANARY not in stored
    assert AMOUNT_CANARY not in caplog.text
    assert PERCENT_CANARY not in caplog.text

    for role in (ADMINISTRATOR, OWNER):
        reader = sign_in(client, create_user(url, roles=(role,)).username)
        for rule, actions, masked in (
            (percent, 2, "percent"),
            (amount, 1, "amount"),
        ):
            response = get(
                client,
                reader,
                "/api/v1/audit-events",
                record_type="session_pay_rules",
                record_id=rule["id"],
            )
            assert response.status_code == 200, response.text
            assert PERCENT_CANARY not in response.text
            assert AMOUNT_CANARY not in response.text
            events = response.json()["items"]
            assert len(events) == actions
            for event in events:
                assert event["actor_id"] == str(finance_user.id)
                assert "[masked]" in str(event["changes"][masked])
                assert "mode" in event["changes"]


# --- D4: who may call ----------------------------------------------------------------


@pytest.fixture(scope="module")
def denied(client: TestClient, module_seeded_url: str) -> dict[str, SetCookie]:
    url = module_seeded_url
    return {
        name: sign_in(client, create_user(url, **kwargs).username)
        for name, kwargs in {
            "administrator": {"roles": (ADMINISTRATOR,)},
            "delegated": {
                "roles": (ADMINISTRATOR,),
                "delegations": [PAYROLL_FINALIZE],
            },
            "coordinator": {"roles": (ACADEMIC_COORDINATOR,)},
            "tutor": {"roles": (TUTOR,)},
            "student": {"roles": (STUDENT,)},
            "guardian": {"roles": (GUARDIAN,)},
        }.items()
    }


@pytest.mark.parametrize(
    "role",
    ["administrator", "delegated", "coordinator", "tutor", "student", "guardian"],
)
def test_everyone_else_is_refused(client, staff, denied, role):
    cookie = denied[role]
    item = created(client, staff)
    body = rule_body(client, staff, effective_from="2051-05-01")

    assert code_of(get(client, cookie, ROOT)) == DENIED
    assert code_of(post(client, cookie, ROOT, body)) == DENIED
    assert code_of(delete(client, cookie, f"{ROOT}/{item['id']}")) == DENIED


def test_the_owner_reads_and_writes(client, module_seeded_url):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(OWNER,)).username)

    item = created(client, cookie)

    assert overview(client, cookie)["no_show"]["upcoming"] == [item]
    assert delete(client, cookie, f"{ROOT}/{item['id']}").status_code == 204


def test_no_key_and_no_reauthentication(client, staff):
    """D4: a plain signed request is enough, and repeating a body is a domain
    answer."""
    body = rule_body(client, staff)

    assert post(client, staff, ROOT, body).status_code == 201
    assert code_of(post(client, staff, ROOT, body)) == (409, "version_conflict")
