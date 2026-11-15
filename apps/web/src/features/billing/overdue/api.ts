import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The overdue accounts list (#97, #382 B): `GET /api/v1/billing/overdue-
// accounts` (billing.view), one row per account with a charge past its
// due date, the aging buckets and the totals over every matching account.

export type OverduePage = components['schemas']['OverdueAccountsPage'];
export type OverdueAccount = components['schemas']['OverdueAccountItem'];
export type AgingBuckets = components['schemas']['AgingBucketsItem'];

/** The aging buckets, in display order. */
export const BUCKETS: readonly (keyof AgingBuckets)[] = [
  'days_1_30',
  'days_31_60',
  'days_61_90',
  'days_over_90',
];

/** Rows per page (the API default). */
export const OVERDUE_PAGE_SIZE = 25;

/** The B3 filter "Minimum days overdue": any, 31+, 61+, 91+. */
export const MIN_DAYS_OPTIONS = [1, 31, 61, 91] as const;
export type MinDaysOverdue = (typeof MIN_DAYS_OPTIONS)[number];
export type OverdueFamilyStatus = 'all' | 'active' | 'inactive';

export interface OverdueFilters {
  minDays: MinDaysOverdue;
  familyStatus: OverdueFamilyStatus;
  page: number;
}

export const OVERDUE_QUERY_KEY = ['billing', 'overdue-accounts'] as const;

export function overdueKey(filters: OverdueFilters) {
  return [...OVERDUE_QUERY_KEY, filters] as const;
}

export function fetchOverdueAccounts(
  filters: OverdueFilters,
  signal: AbortSignal,
): Promise<OverduePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/overdue-accounts', {
        params: {
          query: {
            min_days_overdue: filters.minDays,
            family_status: filters.familyStatus,
            limit: OVERDUE_PAGE_SIZE,
            offset: (filters.page - 1) * OVERDUE_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}
