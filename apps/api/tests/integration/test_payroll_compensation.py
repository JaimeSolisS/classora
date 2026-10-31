"""Tutor compensation (#105 B, C, F, G, H) on real PostgreSQL through the real
app: the routes, their error codes, the lookups, the five pay models, the
audit masks, the keys and CHECKs, the guards and the insert race. Who may call
what is in ``test_payroll_compensation_access.py`` and
``tests/authz/matrix.toml``.

The clock is frozen on 2051-03-10 (the center's zone), a decade of its own.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money, Rate
from app.core.pagination import OffsetParams
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import ADMINISTRATOR, FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    CompensationRateExistsError,
    PayTermsExistsError,
)
from app.workflows import payroll_compensation
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2051, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2051, 3, 10)
ROOT = "/api/v1/payroll/tutors"
NOT_FOUND = (404, "not_found")
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
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


@pytest.fixture
def tutor(module_seeded_url: str) -> uuid.UUID:
    return create_user(module_seeded_url, roles=(TUTOR,)).id


def terms_body(on: str = "2051-01-01", **overrides: Any) -> dict[str, Any]:
    return {
        "effective_from": on,
        "pay_frequency": "monthly",
        "salary_amount": None,
        **overrides,
    }


def rate_body(on: str = "2051-01-01", **overrides: Any) -> dict[str, Any]:
    return {
        "class_kind": "individual",
        "effective_from": on,
        "rate_model": "per_hour",
        "rate": "250.0000",
        **overrides,
    }


def add_terms(
    client: TestClient, staff: SetCookie, tutor: uuid.UUID, **body: Any
) -> Response:
    return post(client, staff, f"{ROOT}/{tutor}/pay-terms", terms_body(**body))


def add_rate(
    client: TestClient, staff: SetCookie, tutor: uuid.UUID, **body: Any
) -> Response:
    return post(client, staff, f"{ROOT}/{tutor}/rates", rate_body(**body))


def compensation(
    client: TestClient, staff: SetCookie, tutor: uuid.UUID
) -> dict[str, Any]:
    response = get(client, staff, f"{ROOT}/{tutor}/compensation")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[str]:
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    return [error["code"] for error in response.json()["errors"]]


# --- G4, G5, G2, G3: writes and the read ---------------------------------------------


def test_a_terms_version_is_created_and_read(client, staff, tutor):
    response = add_terms(
        client, staff, tutor, pay_frequency="biweekly", salary_amount="12000.50"
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "effective_from",
        "pay_frequency",
        "salary_amount",
        "status",
    }
    assert body["pay_frequency"] == "biweekly"
    assert body["salary_amount"] == "12000.50"
    assert body["status"] == "current"
    read = compensation(client, staff, tutor)
    assert read["today"] == "2051-03-10"
    assert read["terms"] == [body]
    assert read["rates"] == []
    assert set(read["tutor"]) == {"user_id", "display_name", "state"}


def test_a_rate_version_is_created_with_four_places(client, staff, tutor):
    response = add_rate(client, staff, tutor, rate="250.125")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "class_kind",
        "effective_from",
        "rate_model",
        "rate",
        "status",
    }
    assert body["rate"] == "250.1250"
    assert compensation(client, staff, tutor)["rates"] == [body]


def test_status_and_order_per_key(client, staff, tutor):
    for kind in ("course", "individual"):
        for on in ("2051-01-01", "2051-03-10", "2051-06-01"):
            model = "per_course" if kind == "course" else "per_hour"
            add_rate(client, staff, tutor, on=on, class_kind=kind, rate_model=model)
    add_terms(client, staff, tutor, on="2051-01-01")
    add_terms(client, staff, tutor, on="2051-02-01", pay_frequency=None)
    upcoming = add_terms(client, staff, tutor, on="2051-04-01")
    assert upcoming.json()["status"] == "upcoming"

    read = compensation(client, staff, tutor)

    assert [
        (row["class_kind"], row["effective_from"], row["status"])
        for row in read["rates"]
    ] == [
        ("individual", "2051-06-01", "upcoming"),
        ("individual", "2051-03-10", "current"),
        ("individual", "2051-01-01", "past"),
        ("course", "2051-06-01", "upcoming"),
        ("course", "2051-03-10", "current"),
        ("course", "2051-01-01", "past"),
    ]
    # A current NULL version stays current.
    assert [
        (row["effective_from"], row["pay_frequency"], row["status"])
        for row in read["terms"]
    ] == [
        ("2051-04-01", "monthly", "upcoming"),
        ("2051-02-01", None, "current"),
        ("2051-01-01", "monthly", "past"),
    ]


def test_any_date_is_accepted(client, staff, tutor):
    add_rate(client, staff, tutor, on="2051-01-01")
    add_rate(client, staff, tutor, on="2051-03-01")

    between = add_rate(client, staff, tutor, on="2051-02-01")
    before_first = add_rate(client, staff, tutor, on="2040-01-01")
    upcoming = add_rate(client, staff, tutor, on="2060-01-01")

    assert between.status_code == 201
    assert between.json()["status"] == "past"
    assert before_first.status_code == 201
    assert upcoming.json()["status"] == "upcoming"


def test_a_null_first_version_is_accepted(client, staff, tutor, module_seeded_url):
    assert add_terms(client, staff, tutor, pay_frequency=None).status_code == 201
    assert add_rate(client, staff, tutor, rate_model=None, rate=None).status_code == 201
    assert _lookups(module_seeded_url, tutor, "individual", TODAY) == (None, None)


def test_zero_is_a_real_rate(client, staff, tutor, module_seeded_url):
    assert add_rate(client, staff, tutor, rate="0").json()["rate"] == "0.0000"
    found, _ = _lookups(module_seeded_url, tutor, "individual", TODAY)
    assert found is not None
    assert found.rate == Rate(0)


# --- G6, G7: deletion, no edit -------------------------------------------------------


def test_any_unused_version_is_deleted(client, staff, tutor, module_seeded_url):
    old = add_rate(client, staff, tutor, on="2051-01-01").json()
    current = add_rate(client, staff, tutor, on="2051-03-01").json()
    terms = add_terms(client, staff, tutor).json()

    assert (
        delete(client, staff, f"{ROOT}/{tutor}/rates/{current['id']}").status_code
        == 204
    )
    assert (
        delete(client, staff, f"{ROOT}/{tutor}/pay-terms/{terms['id']}").status_code
        == 204
    )

    read = compensation(client, staff, tutor)
    assert read["terms"] == []
    assert [(row["id"], row["status"]) for row in read["rates"]] == [
        (old["id"], "current")
    ]
    found, _ = _lookups(module_seeded_url, tutor, "individual", TODAY)
    assert found is not None
    assert found.compensation_rate_id == uuid.UUID(old["id"])


def test_a_version_of_another_tutor_is_404(client, staff, tutor, module_seeded_url):
    other = create_user(module_seeded_url, roles=(TUTOR,)).id
    rate = add_rate(client, staff, other).json()["id"]
    terms = add_terms(client, staff, other).json()["id"]

    assert code_of(delete(client, staff, f"{ROOT}/{tutor}/rates/{rate}")) == NOT_FOUND
    assert (
        code_of(delete(client, staff, f"{ROOT}/{tutor}/pay-terms/{terms}")) == NOT_FOUND
    )
    missing = uuid.uuid4()
    assert (
        code_of(delete(client, staff, f"{ROOT}/{tutor}/rates/{missing}")) == NOT_FOUND
    )
    assert len(compensation(client, staff, other)["rates"]) == 1


@pytest.mark.parametrize("method", ["patch", "put"])
@pytest.mark.parametrize("kind", ["rates", "pay-terms"])
def test_no_version_is_edited(client, staff, tutor, method, kind):
    client.cookies.clear()
    response = client.request(method, f"{ROOT}/{tutor}/{kind}/{uuid.uuid4()}")
    assert response.status_code == 405


def test_the_service_has_no_update():
    assert not [name for name in payroll.__all__ if "update" in name]


# --- G10: the target -----------------------------------------------------------------


def test_an_unknown_user_or_a_non_tutor_is_404(client, staff, module_seeded_url):
    non_tutor = create_user(module_seeded_url, roles=(ADMINISTRATOR,)).id
    for target in (uuid.uuid4(), non_tutor):
        assert code_of(add_terms(client, staff, target)) == NOT_FOUND
        assert code_of(add_rate(client, staff, target)) == NOT_FOUND
        assert code_of(get(client, staff, f"{ROOT}/{target}/compensation")) == NOT_FOUND
        assert (
            code_of(delete(client, staff, f"{ROOT}/{target}/rates/{uuid.uuid4()}"))
            == NOT_FOUND
        )


def test_an_archived_tutor_can_still_get_a_stop(client, staff, module_seeded_url):
    leaver = create_user(module_seeded_url, roles=(TUTOR,), state="archived").id

    assert add_terms(client, staff, leaver, pay_frequency=None).status_code == 201
    assert add_rate(client, staff, leaver).status_code == 201
    assert compensation(client, staff, leaver)["tutor"]["state"] == "archived"


def test_a_user_who_lost_the_role(client, staff, tutor, module_seeded_url):
    url = module_seeded_url
    add_rate(client, staff, tutor)
    run_sql(url, "DELETE FROM user_roles WHERE user_id = :u", {"u": tutor})

    assert code_of(get(client, staff, f"{ROOT}/{tutor}/compensation")) == NOT_FOUND
    assert code_of(add_rate(client, staff, tutor, on="2051-02-01")) == NOT_FOUND
    # The rows stay, and the lookups don't check the role (F4).
    found, _ = _lookups(url, tutor, "individual", TODAY)
    assert found is not None


# --- G11: errors ---------------------------------------------------------------------


def test_the_same_key_is_409(client, staff, tutor):
    add_terms(client, staff, tutor)
    add_rate(client, staff, tutor)

    assert code_of(add_terms(client, staff, tutor)) == (409, "pay_terms_exists")
    assert code_of(add_rate(client, staff, tutor)) == (
        409,
        "compensation_rate_exists",
    )
    # Another class kind is another key.
    assert add_rate(client, staff, tutor, class_kind="group").status_code == 201


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"salary_amount": "0.00"}, "salary_not_positive"),
        ({"salary_amount": "-1.00"}, "salary_not_positive"),
        (
            {"pay_frequency": None, "salary_amount": "100.00"},
            "salary_without_frequency",
        ),
        ({"pay_frequency": "daily"}, "literal_error"),
        ({"salary_amount": "1.234"}, "money_precision"),
        ({"salary_amount": "abc"}, "money_invalid"),
        ({"salary_amount": 100}, "money_invalid"),
        ({"salary_amount": "1234567890123"}, "money_out_of_range"),
    ],
)
def test_terms_body_errors(client, staff, tutor, overrides, code):
    assert code in field_codes(add_terms(client, staff, tutor, **overrides))


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"rate": "-0.0001"}, "rate_negative"),
        ({"rate_model": None}, "rate_model_rate_mismatch"),
        ({"rate": None}, "rate_model_rate_mismatch"),
        ({"rate_model": "per_course"}, "rate_model_not_allowed"),
        (
            {"rate_model": "per_course", "class_kind": "group"},
            "rate_model_not_allowed",
        ),
        ({"class_kind": "workshop"}, "literal_error"),
        ({"rate_model": "per_day"}, "literal_error"),
        ({"rate": "1.23456"}, "rate_precision"),
        ({"rate": "abc"}, "rate_invalid"),
        ({"rate": 250}, "rate_invalid"),
        ({"rate": "12345678901"}, "rate_out_of_range"),
    ],
)
def test_rate_body_errors(client, staff, tutor, overrides, code):
    assert code in field_codes(add_rate(client, staff, tutor, **overrides))


def test_a_field_left_out_or_extra_is_422(client, staff, tutor):
    path = f"{ROOT}/{tutor}/pay-terms"
    assert post(client, staff, path, {"effective_from": "2051-01-01"}).status_code == (
        422
    )
    body = terms_body(version=1)
    assert post(client, staff, path, body).status_code == 422


def test_per_course_is_accepted_on_course(client, staff, tutor):
    response = add_rate(
        client, staff, tutor, class_kind="course", rate_model="per_course"
    )
    assert response.status_code == 201


# --- G1: the list --------------------------------------------------------------------


def _item(
    client: TestClient, staff: SetCookie, tutor: Any, username: str
) -> dict[str, Any]:
    response = get(client, staff, ROOT, q=username)
    assert response.status_code == 200, response.text
    [item] = [row for row in response.json()["items"] if row["user_id"] == str(tutor)]
    return dict(item)


def test_the_list_item_has_the_summary_and_no_contact_data(
    client, staff, module_seeded_url
):
    made = create_user(module_seeded_url, roles=(TUTOR,))
    tutor = made.id
    add_terms(client, staff, tutor, pay_frequency="weekly", salary_amount="900.00")
    add_terms(client, staff, tutor, on="2060-01-01", pay_frequency="monthly")
    add_rate(client, staff, tutor, class_kind="group")
    add_rate(client, staff, tutor, class_kind="individual")
    add_rate(client, staff, tutor, class_kind="course", on="2060-01-01")
    add_rate(
        client,
        staff,
        tutor,
        class_kind="group",
        on="2051-03-01",
        rate_model=None,
        rate=None,
    )

    item = _item(client, staff, tutor, made.username)

    assert set(item) == {
        "user_id",
        "display_name",
        "username",
        "state",
        "current_terms",
        "current_rate_kinds",
    }
    assert item["current_terms"] == {
        "pay_frequency": "weekly",
        "salary_amount": "900.00",
    }
    assert item["current_rate_kinds"] == ["individual"]


def test_the_list_has_no_summary_without_versions_or_after_a_stop(
    client, staff, module_seeded_url
):
    fresh = create_user(module_seeded_url, roles=(TUTOR,))
    stopped = create_user(module_seeded_url, roles=(TUTOR,))
    add_terms(client, staff, stopped.id)
    add_terms(client, staff, stopped.id, on="2051-02-01", pay_frequency=None)

    assert _item(client, staff, fresh.id, fresh.username)["current_terms"] is None
    stopped_item = _item(client, staff, stopped.id, stopped.username)
    assert stopped_item["current_terms"] is None
    assert stopped_item["current_rate_kinds"] == []


def test_the_list_filters_and_lists_every_state(client, staff, module_seeded_url):
    archived = create_user(module_seeded_url, roles=(TUTOR,), state="archived")
    active = create_user(module_seeded_url, roles=(TUTOR,))
    non_tutor = create_user(module_seeded_url, roles=(FINANCE_STAFF,))

    everyone = {
        row["user_id"] for row in get(client, staff, ROOT, limit=100).json()["items"]
    }
    filtered = get(client, staff, ROOT, state="archived", limit=100).json()

    assert {str(archived.id), str(active.id)} <= everyone
    assert str(non_tutor.id) not in everyone
    assert str(archived.id) in {row["user_id"] for row in filtered["items"]}
    assert {row["state"] for row in filtered["items"]} == {"archived"}
    assert get(client, staff, ROOT, state="nope").status_code == 422


def test_the_list_statement_count_does_not_grow(client, staff, module_seeded_url):
    url = module_seeded_url
    for _ in range(3):
        tutor = create_user(url, roles=(TUTOR,)).id
        add_terms(client, staff, tutor)
        add_rate(client, staff, tutor)

    def page(limit: int) -> tuple[int, Any]:
        return count_statements(
            url,
            lambda s: payroll_compensation.list_tutors(
                s, q=None, state=None, page=OffsetParams(limit=limit, offset=0)
            ),
        )

    one, small = page(1)
    many, big = page(50)

    assert len(small.items) == 1
    assert len(big.items) >= 3
    assert one == many == 6


# --- F: the lookups ------------------------------------------------------------------


def _lookups(url: str, tutor: uuid.UUID, kind: str, on: date) -> tuple[Any, Any]:
    async def _read(session: AsyncSession) -> tuple[Any, Any]:
        return (
            await payroll.rate_in_effect(session, tutor, kind, on),
            await payroll.terms_in_effect(session, tutor, on),
        )

    return in_uow(url, _read)


def test_rate_history(client, staff, tutor, module_seeded_url):
    url = module_seeded_url
    add_rate(client, staff, tutor, on="2026-01-01", rate="100")
    add_rate(client, staff, tutor, on="2026-03-01", rate="200")

    def on(day: date) -> str | None:
        found, _ = _lookups(url, tutor, "individual", day)
        return None if found is None else str(found.rate)

    assert on(date(2026, 2, 28)) == "100.0000"
    assert on(date(2026, 3, 1)) == "200.0000"
    assert on(date(2025, 12, 31)) is None

    add_rate(client, staff, tutor, on="2026-04-01", rate_model=None, rate=None)
    assert on(date(2026, 3, 31)) == "200.0000"
    assert on(date(2026, 4, 1)) is None
    assert on(date(2026, 4, 30)) is None

    add_rate(client, staff, tutor, on="2026-05-01", rate="300")
    assert on(date(2026, 5, 1)) == "300.0000"


def test_the_lookup_is_per_class_kind(client, staff, tutor, module_seeded_url):
    add_rate(client, staff, tutor, class_kind="group", rate="10")

    assert _lookups(module_seeded_url, tutor, "individual", TODAY)[0] is None
    found = _lookups(module_seeded_url, tutor, "group", TODAY)[0]
    assert found.rate == Rate(10)


def test_a_rate_in_effect_has_its_version(client, staff, tutor, module_seeded_url):
    made = add_rate(client, staff, tutor, on="2051-01-01").json()

    found, _ = _lookups(module_seeded_url, tutor, "individual", TODAY)

    assert found.compensation_rate_id == uuid.UUID(made["id"])
    assert found.rate_model == "per_hour"
    assert found.rate == Rate("250")
    assert found.effective_from == date(2051, 1, 1)
    assert found.rate.to_money(90, 60) == Money("375.00")


def test_terms_in_effect_and_a_stop(client, staff, tutor, module_seeded_url):
    url = module_seeded_url
    made = add_terms(
        client,
        staff,
        tutor,
        on="2051-01-01",
        pay_frequency="weekly",
        salary_amount="500.00",
    ).json()
    add_terms(client, staff, tutor, on="2051-02-01", pay_frequency=None)

    _, before = _lookups(url, tutor, "individual", date(2051, 1, 31))
    _, stopped = _lookups(url, tutor, "individual", date(2051, 2, 1))
    _, earlier = _lookups(url, tutor, "individual", date(2050, 12, 31))

    assert before.tutor_pay_terms_id == uuid.UUID(made["id"])
    assert before.pay_frequency == "weekly"
    assert before.salary_amount == Money("500.00")
    assert before.effective_from == date(2051, 1, 1)
    assert stopped is None
    assert earlier is None


# --- H1: the five models -------------------------------------------------------------


def test_hourly(client, staff, tutor, module_seeded_url):
    add_terms(client, staff, tutor, pay_frequency="biweekly")
    add_rate(client, staff, tutor, rate_model="per_hour", rate="300")

    rate, terms = _lookups(module_seeded_url, tutor, "individual", TODAY)

    assert (rate.rate_model, rate.rate) == ("per_hour", Rate(300))
    assert (terms.pay_frequency, terms.salary_amount) == ("biweekly", None)


def test_per_session(client, staff, tutor, module_seeded_url):
    add_terms(client, staff, tutor, pay_frequency="weekly")
    add_rate(
        client, staff, tutor, class_kind="group", rate_model="per_session", rate="450"
    )

    rate, terms = _lookups(module_seeded_url, tutor, "group", TODAY)

    assert rate.rate_model == "per_session"
    assert terms.salary_amount is None


def test_per_course(client, staff, tutor, module_seeded_url):
    add_terms(client, staff, tutor, pay_frequency="monthly")
    add_rate(
        client, staff, tutor, class_kind="course", rate_model="per_course", rate="9000"
    )

    rate, _ = _lookups(module_seeded_url, tutor, "course", TODAY)

    assert rate.rate_model == "per_course"
    assert rate.rate.to_money() == Money("9000.00")


def test_fixed_salary(client, staff, tutor, module_seeded_url):
    add_terms(client, staff, tutor, pay_frequency="monthly", salary_amount="20000.00")

    rate, terms = _lookups(module_seeded_url, tutor, "individual", TODAY)

    assert rate is None  # covered by the salary (ADR-0017 E1, #107)
    assert terms.salary_amount == Money("20000.00")
    assert compensation(client, staff, tutor)["rates"] == []


def test_salary_plus_extras(client, staff, tutor, module_seeded_url):
    add_terms(client, staff, tutor, pay_frequency="monthly", salary_amount="15000.00")
    add_rate(
        client, staff, tutor, class_kind="group", rate_model="per_hour", rate="200"
    )

    individual, terms = _lookups(module_seeded_url, tutor, "individual", TODAY)
    group, _ = _lookups(module_seeded_url, tutor, "group", TODAY)

    assert individual is None
    assert group.rate == Rate(200)
    assert terms.salary_amount == Money("15000.00")


# --- C: the audit masks --------------------------------------------------------------


def test_the_audit_log_masks_the_amounts(client, staff, tutor, module_seeded_url):
    url = module_seeded_url
    admin = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
    salary = "73951.37"
    rate = "8246.1937"
    terms_id = add_terms(
        client, staff, tutor, pay_frequency="monthly", salary_amount=salary
    ).json()["id"]
    rate_id = add_rate(client, staff, tutor, rate=rate).json()["id"]
    assert (
        delete(client, staff, f"{ROOT}/{tutor}/pay-terms/{terms_id}").status_code == 204
    )
    assert delete(client, staff, f"{ROOT}/{tutor}/rates/{rate_id}").status_code == 204

    for record_type, record_id, masked, shown in (
        ("tutor_pay_terms", terms_id, "salary_amount", "pay_frequency"),
        ("compensation_rates", rate_id, "rate", "rate_model"),
    ):
        response = get(
            client,
            admin,
            "/api/v1/audit-events",
            record_type=record_type,
            record_id=record_id,
        )
        assert response.status_code == 200, response.text
        assert salary not in response.text
        assert rate not in response.text
        events = response.json()["items"]
        assert len(events) == 2
        for event in events:
            assert event["actor_id"] is not None
            assert "[masked]" in str(event["changes"][masked])
            assert shown in event["changes"]


# --- B, P1: keys and CHECKs in SQL ---------------------------------------------------

TERMS_SQL = (
    "INSERT INTO tutor_pay_terms (tutor_user_id, effective_from, pay_frequency,"
    " salary_amount) VALUES (:u, :on, :frequency, :salary) RETURNING id"
)
RATE_SQL = (
    "INSERT INTO compensation_rates (tutor_user_id, class_kind, effective_from,"
    " rate_model, rate) VALUES (:u, :kind, :on, :model, :rate) RETURNING id"
)


def _state_of(url: str, sql: str, params: dict[str, Any]) -> str | None:
    with pytest.raises(DBAPIError) as caught:
        run_sql(url, sql, params)
    return getattr(caught.value.orig, "sqlstate", None)


def test_p1_the_keys_hold_in_sql(tutor, module_seeded_url):
    url = module_seeded_url
    terms = {"u": tutor, "on": TODAY, "frequency": "weekly", "salary": None}
    rate = {
        "u": tutor,
        "kind": "group",
        "on": TODAY,
        "model": "per_hour",
        "rate": Decimal(1),
    }
    run_sql(url, TERMS_SQL, terms)
    run_sql(url, RATE_SQL, rate)

    assert _state_of(url, TERMS_SQL, terms) == "23505"
    assert _state_of(url, RATE_SQL, rate) == "23505"


@pytest.mark.parametrize(
    ("frequency", "salary"),
    [("daily", None), ("weekly", "0"), ("weekly", "-1"), (None, "100")],
)
def test_terms_checks(tutor, module_seeded_url, frequency, salary):
    params = {
        "u": tutor,
        "on": TODAY,
        "frequency": frequency,
        "salary": None if salary is None else Decimal(salary),
    }
    assert _state_of(module_seeded_url, TERMS_SQL, params) == "23514"


@pytest.mark.parametrize(
    ("kind", "model", "rate"),
    [
        ("workshop", "per_hour", "1"),
        ("group", "per_day", "1"),
        ("group", "per_hour", "-1"),
        ("group", "per_hour", None),
        ("group", None, "1"),
        ("group", "per_course", "1"),
        ("individual", "per_course", "1"),
    ],
)
def test_rate_checks(tutor, module_seeded_url, kind, model, rate):
    params = {
        "u": tutor,
        "kind": kind,
        "on": TODAY,
        "model": model,
        "rate": None if rate is None else Decimal(rate),
    }
    assert _state_of(module_seeded_url, RATE_SQL, params) == "23514"


# --- J4: the guards ------------------------------------------------------------------

PREDICATE = (
    "CREATE OR REPLACE FUNCTION {name}(id uuid) RETURNS boolean"
    " LANGUAGE sql VOLATILE AS $$ {body} $$"
)
PREDICATES = {
    "tutor_pay_terms": "tutor_pay_terms_is_referenced",
    "compensation_rates": "compensation_rate_is_referenced",
}


@contextmanager
def referenced(url: str, table: str) -> Iterator[None]:
    """The table's predicate answers ``true`` for every row (committed, for
    the routes' own connections), as if a finalized line used it; the original
    body comes back afterwards, also when the block fails."""
    name = PREDICATES[table]
    [original] = run_sql(
        url, "SELECT prosrc FROM pg_proc WHERE proname = :n", {"n": name}
    )
    run_sql(url, PREDICATE.format(name=name, body="SELECT true"))
    try:
        yield
    finally:
        run_sql(url, PREDICATE.format(name=name, body=original.prosrc))


def test_a_referenced_terms_version_cannot_change(
    client, staff, tutor, module_seeded_url
):
    url = module_seeded_url
    terms = add_terms(client, staff, tutor).json()["id"]
    params = {"id": terms}

    with referenced(url, "tutor_pay_terms"):
        update = "UPDATE tutor_pay_terms SET pay_frequency = 'weekly' WHERE id = :id"
        assert _state_of(url, update, params) == "42501"
        remove = "DELETE FROM tutor_pay_terms WHERE id = :id"
        assert _state_of(url, remove, params) == "42501"
        assert code_of(delete(client, staff, f"{ROOT}/{tutor}/pay-terms/{terms}")) == (
            409,
            "pay_terms_in_use",
        )

    assert len(compensation(client, staff, tutor)["terms"]) == 1


def test_a_referenced_rate_version_cannot_change(
    client, staff, tutor, module_seeded_url
):
    url = module_seeded_url
    rate = add_rate(client, staff, tutor).json()["id"]
    params = {"id": rate}

    with referenced(url, "compensation_rates"):
        update = "UPDATE compensation_rates SET rate = 1 WHERE id = :id"
        assert _state_of(url, update, params) == "42501"
        remove = "DELETE FROM compensation_rates WHERE id = :id"
        assert _state_of(url, remove, params) == "42501"
        assert code_of(delete(client, staff, f"{ROOT}/{tutor}/rates/{rate}")) == (
            409,
            "compensation_rate_in_use",
        )

    assert len(compensation(client, staff, tutor)["rates"]) == 1


def test_an_unreferenced_version_changes_in_sql_only(
    client, staff, tutor, module_seeded_url
):
    url = module_seeded_url
    rate = add_rate(client, staff, tutor).json()["id"]

    run_sql(url, "UPDATE compensation_rates SET rate = 1 WHERE id = :id", {"id": rate})
    assert compensation(client, staff, tutor)["rates"][0]["rate"] == "1.0000"
    run_sql(url, "DELETE FROM compensation_rates WHERE id = :id", {"id": rate})
    assert compensation(client, staff, tutor)["rates"] == []


@pytest.mark.parametrize("table", sorted(PREDICATES))
def test_truncate_is_always_refused(module_seeded_url, table):
    assert _state_of(module_seeded_url, f"TRUNCATE {table}", {}) == "42501"


# --- G12: the insert race ------------------------------------------------------------


def _race(url: str, call: Callable[[AsyncSession], Awaitable[uuid.UUID]]) -> list[Any]:
    """Two connections insert the same key: the second one starts after the
    first has inserted and before it commits."""

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        inserted = asyncio.Event()

        async def first() -> uuid.UUID:
            async with UnitOfWork(sessions) as uow:
                made = await call(uow.session)
                inserted.set()
                await asyncio.sleep(HOLD_SECONDS)
                return made

        async def second() -> uuid.UUID:
            await inserted.wait()
            async with UnitOfWork(sessions) as uow:
                return await call(uow.session)

        try:
            return await asyncio.gather(first(), second(), return_exceptions=True)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_two_identical_rate_inserts_give_one_row(tutor, module_seeded_url):
    made, refused = _race(
        module_seeded_url,
        lambda s: payroll.add_compensation_rate(
            s,
            tutor,
            class_kind="group",
            effective_from=TODAY,
            rate_model="per_hour",
            rate=Rate(1),
        ),
    )

    assert isinstance(made, uuid.UUID)
    assert isinstance(refused, CompensationRateExistsError)


def test_two_identical_terms_inserts_give_one_row(tutor, module_seeded_url):
    made, refused = _race(
        module_seeded_url,
        lambda s: payroll.add_pay_terms(
            s,
            tutor,
            effective_from=TODAY,
            pay_frequency="weekly",
            salary_amount=None,
        ),
    )

    assert isinstance(made, uuid.UUID)
    assert isinstance(refused, PayTermsExistsError)


def test_the_owner_is_a_normal_writer(client, tutor, module_seeded_url):
    owner = sign_in(client, create_user(module_seeded_url, roles=(OWNER,)).username)
    assert add_terms(client, owner, tutor).status_code == 201
