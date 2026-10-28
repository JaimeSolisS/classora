import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The unbilled session items (#91 D, #366 C): one page of the confirmed and
// completed sessions since the billing start date that can't be charged,
// optionally of one reason. Computed on read by the API; empty while
// billing is off.

export type UnbilledItem = components['schemas']['UnbilledSessionItem'];
export type UnbilledPage =
  components['schemas']['OffsetPage_UnbilledSessionItem_'];
export type UnbilledReason = components['schemas']['UnbilledReason'];

export const UNBILLED_REASONS: readonly UnbilledReason[] = [
  'no_billing_account',
  'no_price',
];

/** Rows per page (the API default). */
export const UNBILLED_PAGE_SIZE = 25;

/** Every cached unbilled list page. */
export const UNBILLED_QUERY_KEY = ['billing', 'unbilled-items'] as const;

export interface UnbilledFilters {
  reason: UnbilledReason | null;
  page: number;
}

export function unbilledListKey(filters: UnbilledFilters) {
  return [...UNBILLED_QUERY_KEY, filters] as const;
}

export function fetchUnbilledItems(
  filters: UnbilledFilters,
  signal: AbortSignal,
): Promise<UnbilledPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/unbilled-items', {
        params: {
          query: {
            ...(filters.reason ? { reason: [filters.reason] } : {}),
            limit: UNBILLED_PAGE_SIZE,
            offset: (filters.page - 1) * UNBILLED_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

/** The filters in the URL (?reason=&page=); anything unknown is dropped. */
export function unbilledFilters(params: URLSearchParams): UnbilledFilters {
  const reason =
    UNBILLED_REASONS.find((value) => value === params.get('reason')) ?? null;
  const page = Number(params.get('page'));
  return {
    reason,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export function unbilledParams(filters: UnbilledFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.reason) params.set('reason', filters.reason);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}
