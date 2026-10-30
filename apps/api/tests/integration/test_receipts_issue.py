"""Issuing receipts inside ``record_payment`` and keeping them frozen (#98
C1-C7, D1-D3; E13, P4-P6).

- C1/C2: a payment recorded through the route has exactly one receipt; the
  response, the list and the detail carry ``receipt``; a second
  ``issue_receipt`` for the payment is refused; the receipt is audited.
- C3: an idempotent replay returns the same receipt, the counter doesn't
  move. C4: a refused payment consumes no number.
- C5: the snapshot (allocations at payment time, oldest first; totals;
  mode ``none``), validated on read; no staff note in it.
- C6: the P5 language rule. C7: older payments have ``receipt: null``.
- D1: later changes (center, family and student names, a credit
  allocation, an adjustment that releases one of the payment's
  allocations) change neither the row nor the PDF text.
- D2: raw ``UPDATE``/``DELETE`` → SQLSTATE 42501.
- D3: a reversal adds the "ANULADO" banner with its local date; the number
  and the row stay the same.

The D1 and D3 downloads render PDFs (WeasyPrint: Pango in the API image
and CI). The clock is frozen at noon on 2031-03-10 (center zone)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import format_date
from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.models import Payment
from app.modules.billing.receipts import parse_snapshot
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.identity.permissions import FINANCE_STAFF, GUARDIAN
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
from tests.factories.base import persist
from tests.factories.families import FamilyFactory, GuardianFactory, add_to_family
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow

TODAY, YESTERDAY = ps.TODAY, ps.YESTERDAY
NUMBER = r"^R-2031-\d{6}$"


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[Any]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def cashier(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


@pytest.fixture(scope="module")
def staff(client: Any, cashier: CreatedUser) -> Any:
    return sign_in(client, cashier.username)


def created(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def compact(text: str) -> str:
    """No whitespace at all: pypdf may split kerned words (#98a D5)."""
    return "".join(text.split())


# --- C1-C4 ---------------------------------------------------------------------------


def test_a_recorded_payment_has_exactly_one_receipt(
    module_seeded_url, client, staff, cashier
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", YESTERDAY))
    before = rs.series_value(url, 2031)

    body = created(ps.pay(client, staff, fam, ps.payment_body("120.00")))

    payment_id = body["payment"]["id"]
    row = rs.receipt_of_payment(url, payment_id)
    assert row is not None
    assert len(rs.receipts_of(url, ps.account_of(url, fam))) == 1
    assert body["receipt"] == {"id": str(row.id), "number": row.number}
    assert body["payment"]["receipt"] == body["receipt"]
    assert row.number == rs.receipt_number_text(2031, before + 1)
    assert (row.language, row.template_version) == ("es", 1)
    assert row.issued_at == ps.NOW
    assert str(row.payment_id) == payment_id
    listed = get(client, staff, ps.account_path(fam, "/payments")).json()["items"]
    assert [item["receipt"] for item in listed] == [body["receipt"]]
    detail = get(client, staff, ps.account_path(fam, f"/payments/{payment_id}"))
    assert detail.json()["receipt"] == body["receipt"]
    [event] = events(url, record_type="receipts", record_id=row.id)
    assert (event.action, event.actor_user_id) == ("create", cashier.id)


def test_a_second_receipt_for_a_payment_is_refused(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, _ = ps.family(url)
    body = created(ps.pay(client, staff, fam, ps.payment_body("10.00", "none")))
    before = rs.series_value(url, 2031)

    with pytest.raises(billing.ReceiptAlreadyIssuedError):
        in_uow(
            url,
            lambda s: billing.issue_receipt(s, uuid.UUID(body["payment"]["id"])),
        )

    assert rs.series_value(url, 2031) == before


def test_a_replay_returns_the_same_receipt(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", TODAY))
    key = ps.new_key()
    first = created(ps.pay(client, staff, fam, ps.payment_body("60.00"), key=key))
    after_first = rs.series_value(url, 2031)

    replay = created(ps.pay(client, staff, fam, ps.payment_body("60.00"), key=key))

    assert replay["receipt"] == first["receipt"]
    assert replay["payment"]["id"] == first["payment"]["id"]
    assert len(rs.receipts_of(url, ps.account_of(url, fam))) == 1
    assert rs.series_value(url, 2031) == after_first


def test_a_refused_payment_consumes_no_number(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [charge] = ps.charges(url, (s1, "50.00", TODAY))
    before = rs.series_value(url, 2031)

    refused = ps.pay(
        client, staff, fam, ps.payment_body("80.00", "explicit", [(charge, "80.00")])
    )
    accepted = created(ps.pay(client, staff, fam, ps.payment_body("50.00")))

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "allocation_exceeds_open"
    assert accepted["receipt"]["number"] == rs.receipt_number_text(2031, before + 1)


# --- C5 the snapshot -----------------------------------------------------------------


def _center(url: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT center_name, address, contact_phone, contact_email"
        " FROM business_settings",
    )
    return row


def test_the_snapshot_holds_the_payment_as_it_was(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c1, c2 = ps.charges(url, (s2, "100.00", YESTERDAY), (s1, "200.00", TODAY))
    body = created(
        ps.pay(
            client,
            staff,
            fam,
            ps.payment_body(
                "350.00",
                payer_name="Rosa Pérez",
                reference="SPEI-98",
                note="Nota interna del cajero",
            ),
        )
    )
    row = rs.receipt_of_payment(url, body["payment"]["id"])
    [family] = run_sql(url, "SELECT name FROM families WHERE id = :f", {"f": fam})
    names = {
        r.id: f"{r.given_names} {r.family_names}"
        for r in run_sql(
            url,
            "SELECT id, given_names, family_names FROM students WHERE id IN (:a, :b)",
            {"a": s1, "b": s2},
        )
    }
    center = _center(url)

    snapshot = parse_snapshot(row.snapshot)

    assert snapshot.v == 1
    assert snapshot.center.model_dump() == {
        "name": center.center_name,
        "address": center.address,
        "phone": center.contact_phone,
        "email": center.contact_email,
    }
    assert (snapshot.family.id, snapshot.family.name) == (fam, family.name)
    assert snapshot.payer_name == "Rosa Pérez"
    assert snapshot.payment.model_dump(mode="json") == {
        "id": body["payment"]["id"],
        "amount": "350.00",
        "received_on": TODAY.isoformat(),
        "method_code": "cash",
        "reference": "SPEI-98",
    }
    assert snapshot.issued_on == TODAY
    assert [
        (a.charge_id, a.student_id, a.student_name, a.kind, a.amount)
        for a in snapshot.allocations
    ] == [
        (c1, s2, names[s2], "manual", Money("100.00")),
        (c2, s1, names[s1], "manual", Money("200.00")),
    ]
    first = snapshot.allocations[0]
    assert (first.service_date, first.description) == (TODAY, "Cargo de prueba")
    assert (first.service_period_end, first.scheduled_minutes) == (None, None)
    assert snapshot.totals.allocated_total == Money("300.00")
    assert snapshot.totals.unallocated == Money("50.00")
    # Money is stored as strings (#87); the staff note is never stored.
    assert row.snapshot["totals"] == {
        "allocated_total": "300.00",
        "unallocated": "50.00",
    }
    assert "Nota interna" not in str(row.snapshot)
    assert "note" not in str(row.snapshot)


def test_a_payment_without_allocations_is_all_unallocated(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", TODAY))

    body = created(ps.pay(client, staff, fam, ps.payment_body("75.00", "none")))

    snapshot = parse_snapshot(
        rs.receipt_of_payment(url, body["payment"]["id"]).snapshot
    )
    assert snapshot.allocations == ()
    assert snapshot.payer_name is None
    assert snapshot.totals.allocated_total == Money("0.00")
    assert snapshot.totals.unallocated == Money("75.00")


# --- C6 the language -----------------------------------------------------------------


def _guardian_user(url: str, language: str | None) -> CreatedUser:
    user = create_user(url, roles=(GUARDIAN,))
    if language is not None:
        run_sql(
            url,
            "INSERT INTO user_preferences (user_id, language) VALUES (:u, :l)",
            {"u": user.id, "l": language},
        )
    return user


def _family_with(url: str, *guardians: tuple[CreatedUser | None, bool]) -> uuid.UUID:
    """A family; its guardian profiles ``(user, active)`` are added one per
    transaction, so they are in that membership order."""
    family = in_uow(url, lambda s: persist(s, FamilyFactory))

    for user, active in guardians:

        async def _add(
            session: AsyncSession, user: Any = user, active: bool = active
        ) -> None:
            guardian = await persist(
                session,
                GuardianFactory,
                user_id=None if user is None else user.id,
                is_active=active,
            )
            await add_to_family(session, family, guardian)

        in_uow(url, _add)
    return family.id


@pytest.mark.parametrize(
    ("guardians", "language"),
    [
        pytest.param([("en", True), ("es", True)], "en", id="first-en"),
        pytest.param([(None, True), ("en", True)], "en", id="first-unset-second-en"),
        pytest.param([(None, True), (None, True)], "es", id="nothing-stored"),
        pytest.param([("nologin", True)], "es", id="no-login"),
        pytest.param([("en", False), ("es", True)], "es", id="inactive-ignored"),
        pytest.param([], "es", id="no-guardian"),
    ],
)
def test_the_language_is_the_first_recipients(
    module_seeded_url, client, staff, guardians, language
):
    url = module_seeded_url
    members = [
        (None if stored == "nologin" else _guardian_user(url, stored), active)
        for stored, active in guardians
    ]
    fam = _family_with(url, *members)

    body = created(ps.pay(client, staff, fam, ps.payment_body("10.00", "none")))

    assert rs.receipt_of_payment(url, body["payment"]["id"]).language == language


# --- C7 older payments ---------------------------------------------------------------


def test_a_payment_recorded_before_receipts_has_none(module_seeded_url, client, staff):
    url = module_seeded_url

    async def _old(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        recorder = await factories.staff_user_id(session)
        family_id, _ = await factories.family_with_students(session, 1)
        account = await factories.billing_account(session, family_id)
        paid = await factories.payment(session, account, "40.00", recorded_by=recorder)
        return family_id, paid.id

    fam, payment_id = in_uow(url, _old)

    listed = get(client, staff, ps.account_path(fam, "/payments")).json()["items"]
    detail = get(client, staff, ps.account_path(fam, f"/payments/{payment_id}")).json()

    assert [item["receipt"] for item in listed] == [None]
    assert detail["receipt"] is None
    assert rs.receipt_of_payment(url, payment_id) is None


# --- D1-D3 frozen contents -----------------------------------------------------------


def _download_text(client: Any, staff: Any, fam: uuid.UUID, receipt_id: str) -> str:
    response = rs.download(client, staff, fam, receipt_id)
    assert response.status_code == 200, response.text
    return rs.pdf_text(response.content)


def test_later_changes_dont_change_an_issued_receipt(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c1, _ = ps.charges(url, (s1, "100.00", YESTERDAY), (s2, "100.00", TODAY))
    body = created(ps.pay(client, staff, fam, ps.payment_body("250.00")))
    receipt_id = body["receipt"]["id"]
    row_before = rs.receipt_of_payment(url, body["payment"]["id"])
    text_before = compact(_download_text(client, staff, fam, receipt_id))
    snapshot = parse_snapshot(row_before.snapshot)
    old_center = snapshot.center.name
    old_family = snapshot.family.name
    old_student = snapshot.allocations[0].student_name
    for old in (old_center, old_family, old_student, "$250.00", "$200.00", "$50.00"):
        assert compact(old) in text_before, old
    # The center, the family and a student change their names.
    run_sql(
        url,
        "UPDATE business_settings SET center_name = 'Centro Renombrado',"
        " address = 'Otra calle 9', contact_phone = '+52 55 0000 0000',"
        " contact_email = 'nuevo@example.com'",
    )
    try:
        run_sql(
            url, "UPDATE families SET name = 'Familia Nueva' WHERE id = :f", {"f": fam}
        )
        run_sql(
            url,
            "UPDATE students SET given_names = 'Renombrada' WHERE id = :s",
            {"s": s1},
        )
        # The leftover credit goes to a new charge, and an adjustment
        # releases this payment's allocation on c1.
        [c3] = ps.charges(url, (s2, "50.00", TODAY))
        allocated = ps.allocate(client, staff, fam, ps.credit_body())
        assert allocated.status_code == 201, allocated.text
        assert ps.valid_allocated(url, c3) == Money("50.00")

        async def _release(session: AsyncSession) -> None:
            await billing.post_adjustment(
                session,
                AdjustmentDraft(
                    charge_id=c1,
                    kind="cancellation_credit",
                    amount=Money("-60.00"),
                    source_key=f"test:{uuid.uuid4()}",
                ),
            )

        in_uow(url, _release)
        released = [
            a for a in ps.allocations_of(url, ps.account_of(url, fam)) if not a.valid
        ]
        assert [a.charge_id for a in released] == [c1]

        text_after = compact(_download_text(client, staff, fam, receipt_id))
    finally:
        run_sql(
            url,
            "UPDATE business_settings SET center_name = :n, address = :a,"
            " contact_phone = :p, contact_email = :e",
            {
                "n": snapshot.center.name,
                "a": snapshot.center.address,
                "p": snapshot.center.phone,
                "e": snapshot.center.email,
            },
        )

    assert text_after == text_before
    for new in ("CentroRenombrado", "Otracalle9", "FamiliaNueva", "Renombrada"):
        assert new not in text_after
    row_after = rs.receipt_of_payment(url, body["payment"]["id"])
    assert row_after._mapping == row_before._mapping


def _receipt_row(url: str) -> tuple[uuid.UUID, Any, Any]:
    """A payment of 30.00 (mode ``none``) on a new account, and its receipt."""
    fam, account, _ = rs.new_account(url, 0)
    actor = in_uow(url, factories.staff_user_id)
    recorded = rs.record(url, actor, rs.draft(account, "30.00", rs.NONE))
    return fam, recorded, rs.receipt_of_payment(url, recorded.payment_id)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE receipts SET language = 'en' WHERE id = :id",
        "DELETE FROM receipts WHERE id = :id",
    ],
)
def test_receipts_refuse_raw_updates_and_deletes(module_seeded_url, statement):
    url = module_seeded_url
    _, _, row = _receipt_row(url)

    with pytest.raises(DBAPIError) as caught:
        run_sql(url, statement, {"id": row.id})

    assert getattr(caught.value.orig, "sqlstate", "") == "42501"
    assert rs.receipt_of_payment(url, row.payment_id)._mapping == row._mapping


def test_a_reversed_payment_shows_the_banner_and_keeps_its_number(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, recorded, row = _receipt_row(url)
    before = compact(_download_text(client, staff, fam, str(row.id)))
    assert "ANULADO" not in before

    async def _reverse(session: AsyncSession) -> None:
        paid = await session.get(Payment, recorded.payment_id)
        assert paid is not None
        await factories.reversal(session, paid)

    in_uow(url, _reverse)
    [reversal] = run_sql(
        url,
        "SELECT created_at FROM payment_reversals WHERE payment_id = :p",
        {"p": recorded.payment_id},
    )
    local = reversal.created_at.astimezone(ps.ZONE).date()

    after = compact(_download_text(client, staff, fam, str(row.id)))

    assert compact(f"ANULADO el {format_date('es', local)}") in after
    assert compact(row.number) in after
    assert rs.receipt_of_payment(url, row.payment_id)._mapping == row._mapping
