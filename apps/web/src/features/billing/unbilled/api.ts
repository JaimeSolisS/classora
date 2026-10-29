import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The unbilled session items (#91 D, #366 C): one page of the confirmed and
// completed sessions since the billing start date that can't be charged,
// optionally of one reason. The unbilled enrollments (#92 J9, #372 B): one
// page of the group and course enrollment periods that have no fee charge,
// with the same reason filter and their own page. The unbilled tuition
// periods (#93, #374 F): one page of the due tuition periods the daily job
// skips, with their own page and the tab's reason where it overlaps
// (no_price). All are computed on read by the API and empty while billing
// is off.

export type UnbilledItem = components['schemas']['UnbilledSessionItem'];
export type UnbilledPage =
  components['schemas']['OffsetPage_UnbilledSessionItem_'];
export type UnbilledReason = components['schemas']['UnbilledReason'];
export type UnbilledEnrollment =
  components['schemas']['UnbilledEnrollmentItem'];
export type UnbilledEnrollmentPage =
  components['schemas']['OffsetPage_UnbilledEnrollmentItem_'];
export type UnbilledTuition = components['schemas']['UnbilledTuitionItem'];
export type UnbilledTuitionPage =
  components['schemas']['OffsetPage_UnbilledTuitionItem_'];
export type TuitionUnbilledReason =
  components['schemas']['TuitionUnbilledReason'];

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
  /** The tuition table's 1-based page. */
  tuitionPage: number;
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

/**
 * The tuition reason the tab's reason stands for (#374 F1: only no_price
 * overlaps), null for every reason, or 'none' when the tab's reason has no
 * tuition counterpart (the section then lists nothing).
 */
export function tuitionReasonOf(
  reason: UnbilledReason | null,
): TuitionUnbilledReason | null | 'none' {
  if (reason === null) return null;
  return reason === 'no_price' ? 'no_price' : 'none';
}

export function unbilledTuitionKey(filters: UnbilledFilters) {
  return [
    ...UNBILLED_QUERY_KEY,
    'tuition',
    { reason: tuitionReasonOf(filters.reason), page: filters.tuitionPage },
  ] as const;
}

export function fetchUnbilledTuition(
  filters: UnbilledFilters,
  signal: AbortSignal,
): Promise<UnbilledTuitionPage> {
  const reason = tuitionReasonOf(filters.reason);
  return apiCall(
    () =>
      api.GET('/api/v1/billing/unbilled-tuition-periods', {
        params: {
          query: {
            ...(reason && reason !== 'none' ? { reason: [reason] } : {}),
            limit: UNBILLED_PAGE_SIZE,
            offset: (filters.tuitionPage - 1) * UNBILLED_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
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
 * The filters in the URL (?reason=&page=&enrollment_page=&tuition_page=);
 * anything unknown is dropped.
 */
export function unbilledFilters(params: URLSearchParams): UnbilledFilters {
  const reason =
    UNBILLED_REASONS.find((value) => value === params.get('reason')) ?? null;
  return {
    reason,
    page: pageParam(params.get('page')),
    enrollmentPage: pageParam(params.get('enrollment_page')),
    tuitionPage: pageParam(params.get('tuition_page')),
  };
}

export function unbilledParams(filters: UnbilledFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.reason) params.set('reason', filters.reason);
  if (filters.page > 1) params.set('page', String(filters.page));
  if (filters.enrollmentPage > 1) {
    params.set('enrollment_page', String(filters.enrollmentPage));
  }
  if (filters.tuitionPage > 1) {
    params.set('tuition_page', String(filters.tuitionPage));
  }
  return params;
}
