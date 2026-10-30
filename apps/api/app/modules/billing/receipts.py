"""Payment receipts (#98; spec §1, §6; ADR-0006 E13, E15; ADR-0015;
data-model §3.21, §3.25, §6).

**Issuing** (:func:`issue_receipt`). ``record_payment`` calls it as its last
step, in the payment's transaction, so every payment gets exactly one
receipt (``receipts.payment_id`` is UNIQUE) and a #88 replay returns the
stored response with the same receipt. The order (P4):

1. the snapshot inputs, read before the counter lock: the payment and its
   family; its allocations (``payment_allocations`` at this moment, with
   their charges); the family name and the students' names
   (``families.service``); the business settings
   (``administration.service``); the language (P5, below);
2. ``issued_at = clock.now()``; the number's year is its year in the
   center's time zone (B2);
3. :func:`app.core.numbering.next_number` for ``R`` and that year: the
   ``number_series`` row lock, the last lock (after the account row
   ``FOR UPDATE`` the caller holds), held for the insert alone;
4. the ``receipts`` row (an audited ORM insert) → flush.

A rollback gives the number back (gap-free). Never commits.

**Language** (P5): the first of ``families.service.family_receipt_recipients``
(active guardian profiles with a login, in ``family_guardians`` order) with
a stored ``user_preferences.language``; otherwise ``es``. It is stored and
every download renders in it, whoever downloads (ADR-0015: the language of
an issued document never changes).

**Snapshot** (P6): :class:`ReceiptSnapshotV1` (``"v": 1``), validated on
write and on read; money as strings. It holds the center (name, address,
phone, email), the family (id, name), ``payer_name``, the payment (id,
amount, ``received_on``, method code, reference), ``issued_on`` (the
center-local date of ``issued_at``), the allocations at payment time
(charge and student ids, the student's name, the charge's language-neutral
``kind``, dates, minutes and manual description, the amount) and the totals
(``allocated_total``, ``unallocated``). Labels are built at render time
from those fields and the catalog (:func:`allocation_label`). No staff
``note`` and no actor names (#364 D4). Later changes (settings, names,
credit allocations, adjustments that release an allocation) never change a
row: ``receipts`` is append-only.

**Templates are versioned** (P8). A receipt stores ``template_version``
(:data:`RECEIPT_TEMPLATE_VERSION` when issued) and is always rendered with
that version's template (:func:`template_for`; an unknown version is a
programming error, a logged ``500``). Version ``N`` is the directory
``app/templates/receipts/vN/`` plus the catalog keys ``receipts.vN.*``. The
rule: a wording or layout change that would alter what an issued receipt
says creates ``v(N+1)`` (a new directory and new keys) and leaves ``vN``
intact; styling-only fixes may edit ``vN``. M8-04 (#121) builds editable
templates on top of this (new versions, rendered in a sandbox).

**Rendering** (:func:`render_receipt_document`) is synchronous and
CPU-bound (``app.core.pdf``): routes call it through
``anyio.to_thread.run_sync`` after their read transaction. It uses only the
row (:class:`StoredReceipt`) and, for the "reversed" banner (E13), the
center-local date of the payment's reversal, read live with the row.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock, i18n, pdf
from app.core.money import Money
from app.core.numbering import next_number
from app.modules.administration import service as administration
from app.modules.billing.models import (
    BillingAccount,
    Charge,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    Receipt,
)
from app.modules.families import service as families
from app.modules.identity import service as identity

RECEIPT_SERIES: Final = "R"
"""The ``number_series`` code of receipts."""

RECEIPT_TEMPLATE_VERSION: Final = 1
"""The template version new receipts are issued with (P8)."""

RECEIPT_TEMPLATES: Final[Mapping[int, str]] = {1: "receipts/v1/receipt.html"}
"""Each template version's template, under ``app/templates/``."""

type ChargeKind = Literal[
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
    "manual",
]


class ReceiptAlreadyIssuedError(ValueError):
    """The payment already has a receipt (a programming error, C2)."""


class UnknownReceiptTemplateError(LookupError):
    """A receipt names a template version with no template (D4; a ``500``)."""


# --- The snapshot (P6) ---------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotCenter(_Frozen):
    name: str
    address: str | None
    phone: str | None
    email: str | None


class SnapshotFamily(_Frozen):
    id: uuid.UUID
    name: str


class SnapshotPayment(_Frozen):
    id: uuid.UUID
    amount: Money
    received_on: date
    method_code: str
    reference: str | None


class SnapshotAllocation(_Frozen):
    charge_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    kind: ChargeKind
    service_date: date
    service_period_end: date | None
    scheduled_minutes: int | None
    description: str | None
    amount: Money


class SnapshotTotals(_Frozen):
    allocated_total: Money
    unallocated: Money


class ReceiptSnapshotV1(_Frozen):
    """What a receipt shows, frozen at payment time (#98 P6, C5)."""

    v: Literal[1]
    center: SnapshotCenter
    family: SnapshotFamily
    payer_name: str | None
    payment: SnapshotPayment
    issued_on: date
    allocations: tuple[SnapshotAllocation, ...]
    totals: SnapshotTotals

    @model_validator(mode="after")
    def _totals_add_up(self) -> ReceiptSnapshotV1:
        allocated = Money.total(line.amount for line in self.allocations)
        if self.totals.allocated_total != allocated:
            raise ValueError("allocated_total is not the sum of the allocations")
        if self.totals.unallocated != self.payment.amount - allocated:
            raise ValueError("unallocated is not the amount minus allocated_total")
        if self.totals.unallocated.is_negative():
            raise ValueError("the allocations add up to more than the payment")
        return self


def parse_snapshot(data: Mapping[str, Any]) -> ReceiptSnapshotV1:
    """A stored snapshot, validated (C5 "on read"); an unknown ``v`` or a
    malformed one is a :class:`pydantic.ValidationError`."""
    return ReceiptSnapshotV1.model_validate(data)


def receipt_number(year: int, value: int) -> str:
    """``R-2026-000123`` (B2)."""
    if not 1 <= value <= 999_999:
        raise ValueError(f"receipt number {value} is out of range")
    return f"{RECEIPT_SERIES}-{year:04d}-{value:06d}"


# --- Issuing (C) ---------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedReceipt:
    """The receipt :func:`issue_receipt` wrote."""

    receipt_id: uuid.UUID
    number: str


async def receipt_language(
    session: AsyncSession, family_id: uuid.UUID
) -> i18n.Language:
    """The language of a new receipt of the family (P5): the first receipt
    recipient with a stored language, else ``es``. One statement, plus one
    per recipient until one has a language."""
    for user_id in await families.family_receipt_recipients(session, family_id):
        stored = (await identity.get_user_preferences(session, user_id)).language
        if stored is not None:
            return i18n.resolve_language(stored)
    return i18n.DEFAULT_LANGUAGE


async def _allocations(
    session: AsyncSession, payment_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(
            PaymentAllocation.charge_id,
            PaymentAllocation.amount,
            Charge.student_id,
            Charge.kind,
            Charge.service_date,
            Charge.service_period_end,
            Charge.scheduled_minutes,
            Charge.description,
        )
        .join(Charge, Charge.id == PaymentAllocation.charge_id)
        .where(PaymentAllocation.payment_id == payment_id)
        .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
    )
    return [dict(row._mapping) for row in rows]


async def issue_receipt(session: AsyncSession, payment_id: uuid.UUID) -> IssuedReceipt:
    """Issue the payment's receipt (C1; the module docstring order). The
    caller holds the payment's account row ``FOR UPDATE`` (``record_payment``
    does). A payment that already has one → :class:`ReceiptAlreadyIssuedError`;
    an unknown payment → :class:`ValueError`. Never commits."""
    if await session.scalar(select(Receipt.id).where(Receipt.payment_id == payment_id)):
        raise ReceiptAlreadyIssuedError(f"payment {payment_id} already has a receipt")
    found = (
        await session.execute(
            select(Payment, BillingAccount.family_id)
            .join(BillingAccount, BillingAccount.id == Payment.billing_account_id)
            .where(Payment.id == payment_id)
        )
    ).first()
    if found is None:
        raise ValueError(f"no payment {payment_id}")
    payment, family_id = found
    lines = await _allocations(session, payment_id)
    family = await families.family_record_reference(session, family_id)
    if family is None:  # pragma: no cover - a foreign key
        raise ValueError(f"no family {family_id}")
    students = await families.student_references(
        session, {line["student_id"] for line in lines}
    )
    settings = await administration.get_business_settings(session)
    language = await receipt_language(session, family_id)
    issued_at = clock.now()
    local = issued_at.astimezone(ZoneInfo(settings.time_zone))
    allocated = Money.total(line["amount"] for line in lines)
    snapshot = ReceiptSnapshotV1(
        v=1,
        center=SnapshotCenter(
            name=settings.center_name,
            address=settings.address,
            phone=settings.contact_phone,
            email=settings.contact_email,
        ),
        family=SnapshotFamily(id=family.id, name=family.name),
        payer_name=payment.payer_name,
        payment=SnapshotPayment(
            id=payment.id,
            amount=payment.amount,
            received_on=payment.received_on,
            method_code=payment.payment_method_code,
            reference=payment.reference,
        ),
        issued_on=local.date(),
        allocations=tuple(
            SnapshotAllocation(
                charge_id=line["charge_id"],
                student_id=line["student_id"],
                student_name=students[line["student_id"]].display_name,
                kind=line["kind"],
                service_date=line["service_date"],
                service_period_end=line["service_period_end"],
                scheduled_minutes=line["scheduled_minutes"],
                description=line["description"],
                amount=line["amount"],
            )
            for line in lines
        ),
        totals=SnapshotTotals(
            allocated_total=allocated, unallocated=payment.amount - allocated
        ),
    )
    value = await next_number(session, RECEIPT_SERIES, local.year)
    receipt = Receipt(
        number=receipt_number(local.year, value),
        payment_id=payment.id,
        billing_account_id=payment.billing_account_id,
        issued_at=issued_at,
        language=language,
        template_version=RECEIPT_TEMPLATE_VERSION,
        snapshot=snapshot.model_dump(mode="json"),
    )
    session.add(receipt)
    await session.flush()
    return IssuedReceipt(receipt_id=receipt.id, number=receipt.number)


# --- Reading (F) ---------------------------------------------------------------------


@dataclass(frozen=True)
class StoredReceipt:
    """A receipt row as rendered (F1): the stored fields and, for the E13
    banner, the center-local date of the payment's reversal (``None`` while
    it has none)."""

    id: uuid.UUID
    number: str
    payment_id: uuid.UUID
    issued_at: datetime
    language: str
    template_version: int
    snapshot: Mapping[str, Any]
    reversed_on: date | None


async def get_receipt(
    session: AsyncSession, account_id: uuid.UUID, receipt_id: uuid.UUID
) -> StoredReceipt | None:
    """The account's receipt ``receipt_id`` with its reversal date, or
    ``None`` when the account has no such receipt. One statement (two with
    a reversal, for the center's time zone); no lock."""
    row = (
        await session.execute(
            select(
                Receipt.id,
                Receipt.number,
                Receipt.payment_id,
                Receipt.issued_at,
                Receipt.language,
                Receipt.template_version,
                Receipt.snapshot,
                PaymentReversal.created_at.label("reversed_at"),
            )
            .outerjoin(
                PaymentReversal, PaymentReversal.payment_id == Receipt.payment_id
            )
            .where(Receipt.id == receipt_id, Receipt.billing_account_id == account_id)
        )
    ).first()
    if row is None:
        return None
    reversed_on = None
    if row.reversed_at is not None:
        zone = await administration.get_center_time_zone(session)
        reversed_on = row.reversed_at.astimezone(ZoneInfo(zone)).date()
    return StoredReceipt(
        id=row.id,
        number=row.number,
        payment_id=row.payment_id,
        issued_at=row.issued_at,
        language=row.language,
        template_version=row.template_version,
        snapshot=row.snapshot,
        reversed_on=reversed_on,
    )


# --- Rendering (D3, D4, E) -----------------------------------------------------------


def template_for(version: int) -> str:
    """The template of ``version`` (D4); an unknown one →
    :class:`UnknownReceiptTemplateError`."""
    try:
        return RECEIPT_TEMPLATES[version]
    except KeyError:
        raise UnknownReceiptTemplateError(
            f"no receipt template version {version}"
        ) from None


def allocation_label(lang: i18n.Language, line: SnapshotAllocation) -> str:
    """An allocation line's label in ``lang`` from its language-neutral
    fields (E1): ``session`` the date and minutes, ``tuition_period`` the
    period, ``package_purchase`` the purchase date, ``manual`` the
    description as typed; the enrollment fees their kind."""
    prefix = "receipts.v1.kinds"
    match line.kind:
        case "session":
            day = i18n.format_date(lang, line.service_date)
            if line.scheduled_minutes is None:
                return i18n.t(lang, f"{prefix}.session_untimed", date=day)
            return i18n.t(
                lang, f"{prefix}.session", date=day, minutes=line.scheduled_minutes
            )
        case "tuition_period":
            start = i18n.format_date(lang, line.service_date)
            if line.service_period_end is None:
                return i18n.t(lang, f"{prefix}.tuition_period_open", start=start)
            end = i18n.format_date(lang, line.service_period_end)
            return i18n.t(lang, f"{prefix}.tuition_period", start=start, end=end)
        case "package_purchase":
            day = i18n.format_date(lang, line.service_date)
            return i18n.t(lang, f"{prefix}.package_purchase", date=day)
        case "manual":
            if line.description:
                return line.description
            return i18n.t(lang, f"{prefix}.manual")
        case "group_enrollment" | "cohort_enrollment":
            return i18n.t(lang, f"{prefix}.{line.kind}")


def _language(stored: StoredReceipt) -> i18n.Language:
    language = i18n.resolve_language(stored.language)
    if language != stored.language:
        raise ValueError(f"receipt {stored.id} has an unknown language")
    return language


def receipt_context(stored: StoredReceipt) -> dict[str, object]:
    """The template context of a stored receipt (the snapshot validated)."""
    lang = _language(stored)
    snapshot = parse_snapshot(stored.snapshot)
    lines: Sequence[dict[str, object]] = [
        {
            "student": line.student_name,
            "label": allocation_label(lang, line),
            "amount": line.amount,
        }
        for line in snapshot.allocations
    ]
    return {
        "center": snapshot.center.model_dump(),
        "receipt": {
            "number": stored.number,
            "issued_on": snapshot.issued_on,
            "received_from": snapshot.payer_name or snapshot.family.name,
            "family_name": snapshot.family.name,
            "received_on": snapshot.payment.received_on,
            "method": i18n.t(
                lang, f"receipts.v1.methods.{snapshot.payment.method_code}"
            ),
            "reference": snapshot.payment.reference,
            "amount": snapshot.payment.amount,
            "allocated_total": snapshot.totals.allocated_total,
            "unallocated": snapshot.totals.unallocated,
            "has_credit": snapshot.totals.unallocated.is_positive(),
            "reversed_on": stored.reversed_on,
        },
        "lines": lines,
    }


def receipt_title(stored: StoredReceipt) -> str:
    """The PDF title metadata: ``Recibo de pago R-2026-000123``."""
    return f"{i18n.t(_language(stored), 'receipts.v1.title')} {stored.number}"


def receipt_html(stored: StoredReceipt) -> str:
    """The receipt's HTML in its stored language and template version."""
    template = template_for(stored.template_version)
    return pdf.render_html(
        template,
        {**receipt_context(stored), "document_title": receipt_title(stored)},
        lang=_language(stored),
    )


def render_receipt_document(stored: StoredReceipt) -> pdf.RenderedPdf:
    """The receipt's PDF with its page count and refused URLs (synchronous,
    CPU-bound; see the module docstring)."""
    template = template_for(stored.template_version)
    return pdf.render_pdf_document(
        template,
        receipt_context(stored),
        lang=_language(stored),
        title=receipt_title(stored),
    )


def render_receipt(stored: StoredReceipt) -> bytes:
    """The receipt's PDF bytes."""
    return render_receipt_document(stored).data
