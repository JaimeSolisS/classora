import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import {
  fetchCharge,
  fetchCharges,
  fetchChargesByIds,
  fetchLedger,
  fetchPayment,
  fetchPayments,
  fetchSummary,
  type AccountSummary,
  type ChargeDetail,
  type ChargeItem,
  type ChargeStatus,
  type LedgerPage,
  type PaymentDetail,
  type PaymentItem,
} from '@/features/billing/accounts/api';
import type {
  PackageDetail,
  PackagePage,
  SoldPackage,
} from '@/features/billing/packages/api';

// The family billing portal's reads (#388; the #102 and #364 routes). The
// page first asks for the caller's own family (`GET /families/own`, T2),
// then reads that family's account with the staff page's calls (the same
// routes, #379), plus the family package reads (T3).
//
// E4: a staff user who is also a guardian holds billing.view, so the API
// doesn't redact their responses. Every read here strips the staff-only
// fields (the payment note, every reason, every `*_by_user_id`, a refund's
// withdrawal id) before anything is cached, so the portal's components
// can't render them: their types don't have them.

export type OwnFamily = components['schemas']['OwnFamily'];
export type OwnFamilyReference = components['schemas']['OwnFamilyReference'];
export type { AccountSummary, ChargeStatus, LedgerPage };
export type FamilyPackage = SoldPackage;
export type FamilyPackageDetail = PackageDetail;
export type FamilyPackagePage = PackagePage;

/** A charge without its poster. */
export type FamilyCharge = Omit<ChargeItem, 'posted_by_user_id'>;
export interface FamilyChargePage {
  items: FamilyCharge[];
  total: number;
  limit: number;
  offset: number;
}

type Adjustment = ChargeDetail['adjustments'][number];
/** A charge's detail: its adjustments without reason or poster. */
export type FamilyChargeDetail = Omit<
  ChargeDetail,
  'posted_by_user_id' | 'adjustments'
> & {
  adjustments: Omit<Adjustment, 'reason' | 'posted_by_user_id'>[];
};

/** A payment without its note or recorder. */
export type FamilyPayment = Omit<PaymentItem, 'note' | 'recorded_by_user_id'>;
export interface FamilyPaymentPage {
  items: FamilyPayment[];
  total: number;
  limit: number;
  offset: number;
}

type Allocation = PaymentDetail['allocations'][number];
type Refund = PaymentDetail['refunds'][number];
/** A payment's detail: the reversal's date only, refunds without reason. */
export type FamilyPaymentDetail = FamilyPayment & {
  allocations: Omit<Allocation, 'created_by_user_id'>[];
  reversal: { reversed_at: string } | null;
  refunds: Omit<
    Refund,
    'reason' | 'refunded_by_user_id' | 'withdrawal_id' | 'refunded_at'
  >[];
};

// ------------------------------------------------------------ redaction
// Allowlists (not deletions): a field the API adds later stays out of the
// portal until it is listed here.

export function familyCharge(charge: ChargeItem): FamilyCharge {
  return {
    id: charge.id,
    student: charge.student,
    kind: charge.kind,
    service_date: charge.service_date,
    due_date: charge.due_date,
    original_due_date: charge.original_due_date,
    description: charge.description,
    list_amount: charge.list_amount,
    discount_amount: charge.discount_amount,
    amount: charge.amount,
    adjustments_total: charge.adjustments_total,
    net: charge.net,
    allocated: charge.allocated,
    open: charge.open,
    status: charge.status,
    posted_at: charge.posted_at,
    class_session_id: charge.class_session_id,
    group_enrollment_id: charge.group_enrollment_id,
    cohort_enrollment_id: charge.cohort_enrollment_id,
  };
}

export function familyChargeDetail(detail: ChargeDetail): FamilyChargeDetail {
  return {
    ...familyCharge(detail),
    discounts: detail.discounts.map((line) => ({
      position: line.position,
      kind: line.kind,
      name: line.name,
      percent: line.percent,
      fixed_amount: line.fixed_amount,
      amount: line.amount,
    })),
    adjustments: detail.adjustments.map((adjustment) => ({
      id: adjustment.id,
      kind: adjustment.kind,
      amount: adjustment.amount,
      posted_at: adjustment.posted_at,
    })),
    allocations: detail.allocations.map((allocation) => ({
      id: allocation.id,
      payment_id: allocation.payment_id,
      amount: allocation.amount,
      created_at: allocation.created_at,
      is_valid: allocation.is_valid,
    })),
  };
}

export function familyPayment(
  payment: PaymentItem | PaymentDetail,
): FamilyPayment {
  return {
    id: payment.id,
    amount: payment.amount,
    received_on: payment.received_on,
    payment_method_code: payment.payment_method_code,
    reference: payment.reference,
    payer_name: payment.payer_name,
    recorded_at: payment.recorded_at,
    allocated_amount: payment.allocated_amount,
    refunded_amount: payment.refunded_amount,
    unallocated_amount: payment.unallocated_amount,
    is_reversed: payment.is_reversed,
    reversed_at: payment.reversed_at,
    receipt: payment.receipt
      ? { id: payment.receipt.id, number: payment.receipt.number }
      : null,
  };
}

export function familyPaymentDetail(
  detail: PaymentDetail,
): FamilyPaymentDetail {
  return {
    ...familyPayment(detail),
    allocations: detail.allocations.map((allocation) => ({
      id: allocation.id,
      charge_id: allocation.charge_id,
      student_id: allocation.student_id,
      amount: allocation.amount,
      created_at: allocation.created_at,
      is_valid: allocation.is_valid,
    })),
    reversal: detail.reversal
      ? { reversed_at: detail.reversal.reversed_at }
      : null,
    refunds: detail.refunds.map((refund) => ({
      id: refund.id,
      amount: refund.amount,
      refunded_on: refund.refunded_on,
      payment_method_code: refund.payment_method_code,
      reference: refund.reference,
    })),
  };
}

// ----------------------------------------------------------------- keys

/** Every cached portal query (none is shared with the staff pages). */
export const FAMILY_BILLING_KEY = ['family-billing'] as const;
export const OWN_FAMILY_KEY = [...FAMILY_BILLING_KEY, 'own'] as const;

/** Every cached query of one family's portal. */
export function portalKey(familyId: string) {
  return [...FAMILY_BILLING_KEY, 'family', familyId] as const;
}

export interface PortalChargeFilters {
  studentId: string | null;
  /** Empty: every status. */
  statuses: ChargeStatus[];
  page: number;
}

export const portalKeys = {
  summary: (familyId: string) => [...portalKey(familyId), 'summary'] as const,
  charges: (familyId: string, filters: PortalChargeFilters) =>
    [...portalKey(familyId), 'charges', filters] as const,
  charge: (familyId: string, chargeId: string) =>
    [...portalKey(familyId), 'charge', chargeId] as const,
  payments: (familyId: string, page: number) =>
    [...portalKey(familyId), 'payments', page] as const,
  payment: (familyId: string, paymentId: string) =>
    [...portalKey(familyId), 'payment', paymentId] as const,
  paymentCharges: (familyId: string, ids: readonly string[]) =>
    [...portalKey(familyId), 'payment-charges', ids] as const,
  packages: (familyId: string, studentId: string | null, page: number) =>
    [...portalKey(familyId), 'packages', studentId, page] as const,
  package: (familyId: string, packageId: string) =>
    [...portalKey(familyId), 'package', packageId] as const,
  ledger: (familyId: string, studentId: string | null) =>
    [...portalKey(familyId), 'ledger', studentId] as const,
};

/** Packages per page (the API default). */
export const PACKAGES_PAGE_SIZE = 25;

// ---------------------------------------------------------------- reads

/** The caller's own family (T2): `{family: null}` when they have none. */
export function fetchOwnFamily(signal: AbortSignal): Promise<OwnFamily> {
  return apiCall(() => api.GET('/api/v1/families/own', { signal }), {
    signal,
  });
}

export { fetchSummary, fetchLedger };

export async function fetchFamilyCharges(
  familyId: string,
  filters: PortalChargeFilters,
  signal: AbortSignal,
): Promise<FamilyChargePage> {
  const page = await fetchCharges(familyId, filters, signal);
  return { ...page, items: page.items.map(familyCharge) };
}

export async function fetchFamilyCharge(
  familyId: string,
  chargeId: string,
  signal: AbortSignal,
): Promise<FamilyChargeDetail> {
  return familyChargeDetail(await fetchCharge(familyId, chargeId, signal));
}

export async function fetchFamilyPayments(
  familyId: string,
  page: number,
  signal: AbortSignal,
): Promise<FamilyPaymentPage> {
  const result = await fetchPayments(familyId, page, signal);
  return { ...result, items: result.items.map(familyPayment) };
}

export async function fetchFamilyPayment(
  familyId: string,
  paymentId: string,
  signal: AbortSignal,
): Promise<FamilyPaymentDetail> {
  return familyPaymentDetail(await fetchPayment(familyId, paymentId, signal));
}

/** The charges a payment was applied to, by id (their labels). */
export async function fetchFamilyChargesByIds(
  familyId: string,
  ids: readonly string[],
  signal: AbortSignal,
): Promise<Record<string, FamilyCharge>> {
  const found = await fetchChargesByIds(familyId, ids, signal);
  return Object.fromEntries(
    Object.entries(found).map(([id, charge]) => [id, familyCharge(charge)]),
  );
}

export function fetchFamilyPackages(
  familyId: string,
  studentId: string | null,
  page: number,
  signal: AbortSignal,
): Promise<FamilyPackagePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}/billing-account/packages', {
        params: {
          path: { family_id: familyId },
          query: {
            ...(studentId ? { student_id: studentId } : {}),
            limit: PACKAGES_PAGE_SIZE,
            offset: (page - 1) * PACKAGES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchFamilyPackage(
  familyId: string,
  packageId: string,
  signal: AbortSignal,
): Promise<FamilyPackageDetail> {
  return apiCall(
    () =>
      api.GET(
        '/api/v1/families/{family_id}/billing-account/packages/{package_id}',
        {
          params: { path: { family_id: familyId, package_id: packageId } },
          signal,
        },
      ),
    { signal },
  );
}
