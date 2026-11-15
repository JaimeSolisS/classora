import type { QueryClient } from '@tanstack/react-query';
import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { idempotencyHeaders } from '@/lib/idempotency';
import { accountKey } from '../accounts/api';

// The withdrawal calls (#101 C1-C3, D1-D3; #387): the preview of a subject
// (a group enrollment, a cohort enrollment or a package), the two confirms
// (account credit, refund) with the `Idempotency-Key` the panel computes
// outside the call (#88), and the staff reads (the list, one withdrawal).

export type WithdrawalPreview = components['schemas']['WithdrawalPreview'];
export type WithdrawalLine = components['schemas']['WithdrawalLine'];
export type PreviewTotals = components['schemas']['PreviewTotals'];
export type PreviewBlocking = components['schemas']['PreviewBlocking'];
export type PreviewWarning = components['schemas']['PreviewWarning'];
export type CreditPolicy = components['schemas']['CreditPolicy'];
export type WithdrawalItem = components['schemas']['WithdrawalItem'];
export type WithdrawalDetail = components['schemas']['WithdrawalDetail'];
export type WithdrawalPage =
  components['schemas']['OffsetPage_WithdrawalItem_'];
export type WithdrawalConfirmed = components['schemas']['WithdrawalConfirmed'];
export type WithdrawalCreditConfirm =
  components['schemas']['WithdrawalCreditConfirm'];
export type WithdrawalRefundConfirm =
  components['schemas']['WithdrawalRefundConfirm'];
export type SubjectKind = components['schemas']['SubjectKind'];
export type WithdrawalOutcome = WithdrawalItem['outcome'];

/** The enrollment or package a withdrawal is about. */
export interface SubjectRef {
  kind: SubjectKind;
  id: string;
}

/** The stored preview of a confirmed withdrawal (#101 C2 step 8). */
export type StoredPreview = WithdrawalPreview & {
  outcome?: WithdrawalOutcome;
  overrides?: { charge_id: string; amount: string }[];
  applied?: { charge_id: string; applied_credit: string }[];
};

/** Rows per page of the account page's Withdrawals section. */
export const WITHDRAWALS_PAGE_SIZE = 25;

export const WITHDRAWALS_KEY = ['billing', 'withdrawals'] as const;

export function previewKey(subject: SubjectRef) {
  return [...WITHDRAWALS_KEY, 'preview', subject.kind, subject.id] as const;
}

/** The withdrawal of one enrollment, if any ("Settle billing", B1). */
export function subjectWithdrawalKey(subject: SubjectRef) {
  return [...WITHDRAWALS_KEY, 'of', subject.kind, subject.id] as const;
}

export function withdrawalKey(id: string) {
  return [...WITHDRAWALS_KEY, 'detail', id] as const;
}

/** Under the family's account queries: a write there refreshes it too. */
export function familyWithdrawalsKey(familyId: string, page: number) {
  return [...accountKey(familyId), 'withdrawals', page] as const;
}

/** The subject as the preview's query (or the confirm's `subject`). */
export function subjectParams(subject: SubjectRef): {
  group_enrollment_id?: string;
  cohort_enrollment_id?: string;
  package_id?: string;
} {
  if (subject.kind === 'group_enrollment') {
    return { group_enrollment_id: subject.id };
  }
  if (subject.kind === 'cohort_enrollment') {
    return { cohort_enrollment_id: subject.id };
  }
  return { package_id: subject.id };
}

export function fetchPreview(
  subject: SubjectRef,
  signal: AbortSignal,
): Promise<WithdrawalPreview> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/withdrawal-preview', {
        params: { query: subjectParams(subject) },
        signal,
      }),
    { signal },
  );
}

/** The withdrawal of one enrollment, or null (one request, `limit=1`). */
export async function fetchSubjectWithdrawal(
  subject: SubjectRef,
  signal: AbortSignal,
): Promise<WithdrawalItem | null> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/billing/withdrawals', {
        params: { query: { ...subjectParams(subject), limit: 1 } },
        signal,
      }),
    { signal },
  );
  return page.items[0] ?? null;
}

export function fetchFamilyWithdrawals(
  familyId: string,
  page: number,
  signal: AbortSignal,
): Promise<WithdrawalPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/withdrawals', {
        params: {
          query: {
            family_id: familyId,
            limit: WITHDRAWALS_PAGE_SIZE,
            offset: (page - 1) * WITHDRAWALS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchWithdrawal(
  id: string,
  signal: AbortSignal,
): Promise<WithdrawalDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/withdrawals/{withdrawal_id}', {
        params: { path: { withdrawal_id: id } },
        signal,
      }),
    { signal },
  );
}

/**
 * Confirms a withdrawal (#101 C2): account credit or a refund. The key is
 * computed by the caller BEFORE this call, so apiCall's retry after the
 * password dialog resends it (#88).
 */
export function confirmWithdrawal(
  body: WithdrawalCreditConfirm | WithdrawalRefundConfirm,
  idempotencyKey: string,
): Promise<WithdrawalConfirmed> {
  const headers = idempotencyHeaders(idempotencyKey);
  if ('refund' in body) {
    return apiCall(() =>
      api.POST('/api/v1/billing/withdrawals/refund', {
        body,
        params: { header: headers },
      }),
    );
  }
  return apiCall(() =>
    api.POST('/api/v1/billing/withdrawals/account-credit', {
      body,
      params: { header: headers },
    }),
  );
}

/**
 * B5: after a confirm, the withdrawal reads (previews, the per-enrollment
 * lookups, the lists) and the family's account (summary, charges, ledger,
 * payments, its Withdrawals section). The caller also refreshes its own
 * roster or package list and the sessions.
 */
export async function invalidateAfterWithdrawal(
  client: QueryClient,
  familyId: string,
): Promise<void> {
  await Promise.all([
    client.invalidateQueries({ queryKey: WITHDRAWALS_KEY }),
    client.invalidateQueries({ queryKey: accountKey(familyId) }),
  ]);
}
