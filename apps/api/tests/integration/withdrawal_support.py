"""Shared helpers of the #101 withdrawal tests (``test_billing_withdrawal_*``,
``test_billing_withdrawals_*``).

Cohorts and groups are created and enrolled **through the API** on the
series timeline (``series_support``): each test takes its own decade, "now"
is a Sunday 12:00 (Mexico City) and a Monday/Wednesday 16:00 schedule
starts the next day, so moving the clock to the Thursday of week ``n`` at
noon leaves ``2 × n`` sessions held. The billing floor is set inside the
decade (``enrollment_charge_support.set_floor``), so earlier tests' periods
are never sweeper candidates.

- :func:`cohort_world` / :func:`group_world`: a student in a family, the
  subject, the owner and the enrollment (its fee posted by the M4 hook when
  ``fee`` is given).
- :func:`preview`, :func:`credit`, :func:`refund`: the routes; :func:`body`
  builds a confirm body from a preview.
- :func:`withdrawal_rows`, :func:`assert_no_session_credit`: the committed
  rows and the #92 J4 boundary (C3: no ``withdrawal`` adjustment on a
  ``session`` charge).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import OWNER
from tests.conftest import FrozenClock
from tests.integration import corrections_support as cs
from tests.integration import enrollment_charge_support as ec
from tests.integration import payment_support as ps
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.series_support import Timeline, owner_cookie
from tests.integration.test_class_session_create import new_student, new_subject

PREVIEW = "/api/v1/billing/withdrawal-preview"
WITHDRAWALS = "/api/v1/billing/withdrawals"
CREDIT = f"{WITHDRAWALS}/account-credit"
REFUND = f"{WITHDRAWALS}/refund"
REASON = "La familia se cambió de ciudad"
ZONE_NAME = "America/Mexico_City"


@dataclass(frozen=True)
class World:
    """One enrollment of a student in a family."""

    student: uuid.UUID
    family: uuid.UUID
    subject: uuid.UUID
    owner_id: str
    enrollment_id: str
    kind: str

    @property
    def subject_query(self) -> dict[str, str]:
        return {f"{self.kind}_id": self.enrollment_id}


def thursday(tl: Timeline, week: int) -> datetime:
    """Noon on the Thursday of ``week``: ``2 × week`` sessions held."""
    return tl.at(tl.week(week, 4), "12:00")


def cohort_world(
    url: str,
    client: TestClient,
    tl: Timeline,
    *,
    fee: str | None = "3000.00",
    session_count: int = 10,
    family: bool = True,
) -> World:
    """Call under the frozen clock at ``tl.now``."""
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    student = new_student(url)
    family_id = ec.add_family(url, student) if family else uuid.uuid4()
    cookie = owner_cookie(url, client)
    cohort = ec.make_cohort(
        client, cookie, subject, tl.monday, session_count=session_count
    )
    if fee is not None:
        ec.cohort_fee(url, subject, fee, cohort_id=uuid.UUID(cohort["id"]))
    enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)
    return World(
        student=student,
        family=family_id,
        subject=subject,
        owner_id=cohort["id"],
        enrollment_id=enrolled["id"],
        kind="cohort_enrollment",
    )


def group_world(
    url: str,
    client: TestClient,
    tl: Timeline,
    *,
    fee: str | None = "2000.00",
    count: int | None = 10,
    session_price: str | None = None,
) -> World:
    """A group of ``count`` sessions (``None``: open-ended) with its flat
    fee (or a per-session price). Call under the frozen clock at
    ``tl.now``."""
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    student = new_student(url)
    family_id = ec.add_family(url, student)
    cookie = owner_cookie(url, client)
    group = ec.make_group(client, cookie, subject, tl.monday, count=count)
    group_id = uuid.UUID(group["id"])
    if fee is not None:
        ec.group_fee(url, subject, fee, group_id=group_id)
    if session_price is not None:
        ec.group_session_price(url, subject, session_price, group_id=group_id)
    enrolled = ec.enroll_group(client, cookie, group["id"], student)
    return World(
        student=student,
        family=family_id,
        subject=subject,
        owner_id=group["id"],
        enrollment_id=enrolled["id"],
        kind="group_enrollment",
    )


def owner(url: str, client: TestClient) -> cs.Staff:
    """A new Owner, signed in and reauthenticated (call under the clock)."""
    return cs.staff(url, client, (OWNER,))


def preview(client: TestClient, cookie: Any, **subject: Any) -> Response:
    return get(client, cookie, PREVIEW, **{k: str(v) for k, v in subject.items()})


def preview_of(client: TestClient, cookie: Any, **subject: Any) -> dict[str, Any]:
    response = preview(client, cookie, **subject)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def body(
    found: dict[str, Any],
    *,
    reason: str = REASON,
    overrides: list[tuple[Any, str]] | None = None,
    refund_on: date | None = None,
    method: str = "transfer",
    **extra: Any,
) -> dict[str, Any]:
    subject = found["subject"]
    out: dict[str, Any] = {
        "subject": {f"{subject['kind']}_id": subject["id"]},
        "reason": reason,
        "fingerprint": found["fingerprint"],
    }
    if found["enrollment_version"] is not None:
        out["version"] = found["enrollment_version"]
    if overrides is not None:
        out["credit_overrides"] = [
            {"charge_id": str(c), "amount": a} for c, a in overrides
        ]
    if refund_on is not None:
        out["refund"] = {
            "refunded_on": refund_on.isoformat(),
            "payment_method_code": method,
            "reference": "SPEI-101",
        }
    out.update(extra)
    return out


def credit(
    client: TestClient, cookie: Any, payload: dict[str, Any], key: str | None = "new"
) -> Response:
    return ps._post(client, cookie, CREDIT, payload, key)


def refund(
    client: TestClient, cookie: Any, payload: dict[str, Any], key: str | None = "new"
) -> Response:
    return ps._post(client, cookie, REFUND, payload, key)


def pay(
    client: TestClient,
    cookie: Any,
    family: Any,
    amount: str,
    lines: list[tuple[Any, str]] | None,
    on: date,
) -> str:
    """A payment with explicit lines (``None``: unallocated), received on
    ``on``; its id."""
    payload = ps.payment_body(
        amount,
        mode="none" if lines is None else "explicit",
        lines=lines,
        received_on=on.isoformat(),
    )
    response = ps.pay(client, cookie, family, payload)
    assert response.status_code == 201, response.text
    payment_id: str = response.json()["payment"]["id"]
    return payment_id


def withdrawal_rows(url: str, **where: Any) -> list[Any]:
    [(column, value)] = where.items()
    return run_sql(
        url,
        f"SELECT * FROM withdrawals WHERE {column} = :v",  # noqa: S608
        {"v": str(value)},
    )


def withdrawal_adjustments(url: str, withdrawal_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT a.*, c.kind AS charge_kind FROM charge_adjustments a"
        " JOIN charges c ON c.id = a.charge_id"
        " WHERE a.kind = 'withdrawal' AND a.source_key LIKE :k ORDER BY a.posted_at",
        {"k": f"withdrawal:%:{withdrawal_id}"},
    )


def assert_no_session_credit(url: str) -> None:
    """C3: #101 never writes a ``withdrawal`` adjustment on a ``session``
    (or ``manual``) charge."""
    rows = run_sql(
        url,
        "SELECT a.id FROM charge_adjustments a JOIN charges c ON c.id = a.charge_id"
        " WHERE a.kind = 'withdrawal' AND c.kind IN ('session', 'manual')",
    )
    assert rows == []


def account_of(url: str, family: Any) -> uuid.UUID:
    account = ps.account_of(url, family)
    assert account is not None
    return account


def advance(clock: FrozenClock, to: datetime) -> None:
    clock.set(to)


def day(at: datetime) -> date:
    return at.astimezone(ps.ZONE).date()


def key_rows(url: str, key: str) -> int:
    return cs.key_count(url, key)


ONE_DAY = timedelta(days=1)
