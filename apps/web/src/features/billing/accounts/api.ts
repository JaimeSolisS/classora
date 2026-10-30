import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { idempotencyHeaders } from '@/lib/idempotency';
import { apiCents } from './cents';

// The family billing account calls (#364, #96, #379): the summary, the
// charges, the ledger, the payments and a payment are queries under
// ACCOUNT_QUERY_KEY (one family's queries under accountKey(familyId), so a
// recorded payment invalidates them all at once); recording a payment and
// allocating account credit post ledger rows, so they send an
// `Idempotency-Key` (#88) that the dialog computes outside the call.

export type AccountSummary = components['schemas']['BillingAccountSummary'];
export type StudentBalance = components['schemas']['StudentBalanceItem'];
export type ChargeItem = components['schemas']['ChargeItem'];
export type ChargeStatus = components['schemas']['ChargeStatus'];
export type ChargePage = components['schemas']['OffsetPage_ChargeItem_'];
export type LedgerEntry = components['schemas']['LedgerEntryItem'];
export type LedgerEntryType = components['schemas']['LedgerEntryType'];
export type LedgerPage = components['schemas']['CursorPage_LedgerEntryItem_'];
export type PaymentItem = components['schemas']['PaymentItem'];
export type PaymentDetail = components['schemas']['PaymentDetail'];
export type PaymentAllocation = components['schemas']['PaymentAllocationItem'];
export type PaymentPage = components['schemas']['OffsetPage_PaymentItem_'];
export type PaymentMethod = components['schemas']['PaymentMethodItem'];
export type PaymentCreate = components['schemas']['PaymentCreate'];
export type PaymentRecorded = components['schemas']['PaymentRecorded'];
export type ReceiptReference = components['schemas']['ReceiptReference'];
export type CreditAllocationCreate =
  components['schemas']['CreditAllocationCreate'];
export type CreditAllocated = components['schemas']['CreditAllocated'];
export type AllocationLine = components['schemas']['AllocationLineBody'];

/**
 * The receipt PDF of a payment (#98 F1): a plain same-origin link (the
 * session cookie authenticates it), so the browser downloads the file.
 */
export function receiptUrl(familyId: string, receiptId: string): string {
  return `/api/v1/families/${encodeURIComponent(familyId)}/billing-account/receipts/${encodeURIComponent(receiptId)}/pdf`;
}

/** The charge statuses, in the filter's order. */
export const CHARGE_STATUSES: readonly ChargeStatus[] = [
  'pending',
  'partially_paid',
  'overdue',
  'paid',
  'void',
];
/** The statuses of a charge that may still have an open amount. */
export const OPEN_STATUSES: readonly ChargeStatus[] = [
  'pending',
  'partially_paid',
  'overdue',
];

/** Rows per page of the charges and the payments (the API default). */
export const ACCOUNT_PAGE_SIZE = 25;
/** Ledger rows per "Load more". */
export const LEDGER_PAGE_SIZE = 25;
const SCAN_PAGE_SIZE = 100;
const SCAN_MAX_PAGES = 20;

/** Every cached account query, of every family. */
export const ACCOUNT_QUERY_KEY = ['billing', 'accounts'] as const;

/** Every cached query of one family's account (invalidated after a write). */
export function accountKey(familyId: string) {
  return [...ACCOUNT_QUERY_KEY, familyId] as const;
}

export function summaryKey(familyId: string) {
  return [...accountKey(familyId), 'summary'] as const;
}

export interface ChargeFilters {
  studentId: string | null;
  /** Empty: every status. */
  statuses: ChargeStatus[];
  page: number;
}

export function chargesKey(familyId: string, filters: ChargeFilters) {
  return [...accountKey(familyId), 'charges', filters] as const;
}

/** The open charges the allocation editor and the estimate use. */
export function openChargesKey(familyId: string) {
  return [...accountKey(familyId), 'open-charges'] as const;
}

export function ledgerKey(familyId: string, studentId: string | null) {
  return [...accountKey(familyId), 'ledger', studentId] as const;
}

export function paymentsKey(familyId: string, page: number) {
  return [...accountKey(familyId), 'payments', page] as const;
}

export function paymentKey(familyId: string, paymentId: string) {
  return [...accountKey(familyId), 'payment', paymentId] as const;
}

export const PAYMENT_METHODS_KEY = ['billing', 'payment-methods'] as const;

// ------------------------------------------------------------- reads

export function fetchSummary(
  familyId: string,
  signal: AbortSignal,
): Promise<AccountSummary> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}/billing-account', {
        params: { path: { family_id: familyId } },
        signal,
      }),
    { signal },
  );
}

export function fetchCharges(
  familyId: string,
  filters: ChargeFilters,
  signal: AbortSignal,
): Promise<ChargePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}/billing-account/charges', {
        params: {
          path: { family_id: familyId },
          query: {
            ...(filters.studentId ? { student_id: filters.studentId } : {}),
            ...(filters.statuses.length > 0
              ? { status: filters.statuses }
              : {}),
            limit: ACCOUNT_PAGE_SIZE,
            offset: (filters.page - 1) * ACCOUNT_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

async function scanCharges(
  familyId: string,
  statuses: readonly ChargeStatus[] | null,
  signal: AbortSignal,
  done: (items: ChargeItem[]) => boolean = () => false,
): Promise<ChargeItem[]> {
  const items: ChargeItem[] = [];
  for (let page = 0; page < SCAN_MAX_PAGES; page += 1) {
    const result = await apiCall(
      () =>
        api.GET('/api/v1/families/{family_id}/billing-account/charges', {
          params: {
            path: { family_id: familyId },
            query: {
              ...(statuses ? { status: [...statuses] } : {}),
              limit: SCAN_PAGE_SIZE,
              offset: page * SCAN_PAGE_SIZE,
            },
          },
          signal,
        }),
      { signal },
    );
    items.push(...result.items);
    const finished = done(items);
    if (finished || result.items.length === 0 || items.length >= result.total) {
      break;
    }
  }
  return items;
}

/**
 * Every charge of the account with an open amount (pending, partially paid
 * or overdue, open > 0), in the API's order; the dialogs sort them.
 */
export async function fetchOpenCharges(
  familyId: string,
  signal: AbortSignal,
): Promise<ChargeItem[]> {
  const items = await scanCharges(familyId, OPEN_STATUSES, signal);
  return items.filter((charge) => apiCents(charge.open) > 0);
}

/**
 * The charges with these ids (a payment's allocations), read from the
 * account's charges until every id is found.
 */
export async function fetchChargesByIds(
  familyId: string,
  ids: readonly string[],
  signal: AbortSignal,
): Promise<Record<string, ChargeItem>> {
  const wanted = new Set(ids);
  const found: Record<string, ChargeItem> = {};
  if (wanted.size === 0) {
    return found;
  }
  await scanCharges(familyId, null, signal, (items) => {
    for (const charge of items) {
      if (wanted.has(charge.id)) {
        found[charge.id] = charge;
      }
    }
    return Object.keys(found).length >= wanted.size;
  });
  return found;
}

export function fetchLedger(
  familyId: string,
  studentId: string | null,
  cursor: string | null,
  signal: AbortSignal,
): Promise<LedgerPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}/billing-account/ledger', {
        params: {
          path: { family_id: familyId },
          query: {
            ...(studentId ? { student_id: studentId } : {}),
            ...(cursor ? { cursor } : {}),
            limit: LEDGER_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPayments(
  familyId: string,
  page: number,
  signal: AbortSignal,
): Promise<PaymentPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}/billing-account/payments', {
        params: {
          path: { family_id: familyId },
          query: {
            limit: ACCOUNT_PAGE_SIZE,
            offset: (page - 1) * ACCOUNT_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPayment(
  familyId: string,
  paymentId: string,
  signal: AbortSignal,
): Promise<PaymentDetail> {
  return apiCall(
    () =>
      api.GET(
        '/api/v1/families/{family_id}/billing-account/payments/{payment_id}',
        {
          params: { path: { family_id: familyId, payment_id: paymentId } },
          signal,
        },
      ),
    { signal },
  );
}

/** Every payment method (the dialog offers the active ones). */
export function fetchPaymentMethods(
  signal: AbortSignal,
): Promise<PaymentMethod[]> {
  return apiCall(() => api.GET('/api/v1/billing/payment-methods', { signal }), {
    signal,
  });
}

// ------------------------------------------------------------ writes

/**
 * Records a payment (#96 C1). `idempotencyKey` comes from the dialog's
 * `IdempotentSubmission.keyFor(body)`, computed before this call so that
 * apiCall's own retries resend the same key.
 */
export function recordPayment(
  familyId: string,
  body: PaymentCreate,
  idempotencyKey: string,
): Promise<PaymentRecorded> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/families/{family_id}/billing-account/payments', {
      body,
      params: { path: { family_id: familyId }, header: headers },
    }),
  );
}

/** Allocates account credit (#96 C4); the key as in `recordPayment`. */
export function allocateCredit(
  familyId: string,
  body: CreditAllocationCreate,
  idempotencyKey: string,
): Promise<CreditAllocated> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/families/{family_id}/billing-account/allocations', {
      body,
      params: { path: { family_id: familyId }, header: headers },
    }),
  );
}
