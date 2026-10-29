import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The unbilled session items (#91 D, #366 C): one page of the confirmed and
// completed sessions since the billing start date that can't be charged,
// optionally of one reason. The unbilled enrollments (#92 J9, #372 B): one
// page of the group and course enrollment periods that have no fee charge,
// with the same reason filter and their own page. Both are computed on read
// by the API and empty while billing is off.

export type UnbilledItem = components['schemas']['UnbilledSessionItem'];
export type UnbilledPage =
  components['schemas']['OffsetPage_UnbilledSessionItem_'];
export type UnbilledReason = components['schemas']['UnbilledReason'];
export type UnbilledEnrollment =
  components['schemas']['UnbilledEnrollmentItem'];
export type UnbilledEnrollmentPage =
  components['schemas']['OffsetPage_UnbilledEnrollmentItem_'];

export const UNBILLED_REASONS: readonly UnbilledReason[] = [
  'no_billing_account',
  'no_price',
];

/** Rows per page (the API default). */
export const UNBILLED_PAGE_SIZE = 25;

/**
 * Every cached unbilled list page, sessions and enrollments: the enrollment
 * pages sit under this prefix too, so the pages that invalidate it (billing
 * settings, custom prices) refresh both lists.
 */
export const UNBILLED_QUERY_KEY = ['billing', 'unbilled-items'] as const;

export interface UnbilledFilters {
  reason: UnbilledReason | null;
  /** The sessions table's 1-based page. */
  page: number;
  /** The enrollments table's 1-based page. */
  enrollmentPage: number;
}

export function unbilledListKey(filters: UnbilledFilters) {
  return [
    ...UNBILLED_QUERY_KEY,
    { reason: filters.reason, page: filters.page },
  ] as const;
}

export function unbilledEnrollmentsKey(filters: UnbilledFilters) {
  return [
    ...UNBILLED_QUERY_KEY,
    'enrollments',
    { reason: filters.reason, page: filters.enrollmentPage },
  ] as const;
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

export function fetchUnbilledEnrollments(
  filters: UnbilledFilters,
  signal: AbortSignal,
): Promise<UnbilledEnrollmentPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/unbilled-enrollments', {
        params: {
          query: {
            ...(filters.reason ? { reason: [filters.reason] } : {}),
            limit: UNBILLED_PAGE_SIZE,
            offset: (filters.enrollmentPage - 1) * UNBILLED_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

function pageParam(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}

/**
 * The filters in the URL (?reason=&page=&enrollment_page=); anything
 * unknown is dropped.
 */
export function unbilledFilters(params: URLSearchParams): UnbilledFilters {
  const reason =
    UNBILLED_REASONS.find((value) => value === params.get('reason')) ?? null;
  return {
    reason,
    page: pageParam(params.get('page')),
    enrollmentPage: pageParam(params.get('enrollment_page')),
  };
}

export function unbilledParams(filters: UnbilledFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.reason) params.set('reason', filters.reason);
  if (filters.page > 1) params.set('page', String(filters.page));
  if (filters.enrollmentPage > 1) {
    params.set('enrollment_page', String(filters.enrollmentPage));
  }
  return params;
}
