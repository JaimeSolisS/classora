import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The discount calls (#95, #367): the list and one discount are queries;
// creating, editing (and deactivating), assigning and removing an
// assignment are direct calls after which the pages invalidate the
// discount queries.

export type Discount = components['schemas']['DiscountItem'];
export type DiscountDetail = components['schemas']['DiscountDetail'];
export type DiscountPage = components['schemas']['OffsetPage_DiscountItem_'];
export type DiscountCreate = components['schemas']['DiscountCreate'];
export type DiscountUpdate = components['schemas']['DiscountUpdate'];
export type DiscountEligibility = components['schemas']['DiscountEligibility'];
export type DiscountAssignment =
  components['schemas']['DiscountAssignmentItem'];
export type DiscountKind = components['schemas']['DiscountKind'];
export type DiscountStatus = components['schemas']['DiscountStatus'];
export type DiscountAudience = components['schemas']['DiscountAudience'];
export type CriteriaChargeKind = components['schemas']['CriteriaChargeKind'];
export type CriteriaClassType = components['schemas']['CriteriaClassType'];

/** Rows per page of the discount list (the API default). */
export const DISCOUNTS_PAGE_SIZE = 25;

/** Every cached discount query (the list pages and each discount). */
export const DISCOUNTS_QUERY_KEY = ['billing', 'discounts'] as const;

/**
 * The status filter: `open` (the default) is current and upcoming, `all`
 * every status, otherwise one status.
 */
export type DiscountStatusChoice = 'open' | 'all' | DiscountStatus;

export interface DiscountListFilters {
  kind: DiscountKind | null;
  status: DiscountStatusChoice;
  q: string;
  page: number;
}

/** The statuses a choice asks the API for (none = every status). */
export function statusesOf(choice: DiscountStatusChoice): DiscountStatus[] {
  if (choice === 'open') return ['current', 'upcoming'];
  if (choice === 'all') return [];
  return [choice];
}

export function discountsListKey(filters: DiscountListFilters) {
  return [...DISCOUNTS_QUERY_KEY, 'list', filters] as const;
}

export function discountKey(discountId: string) {
  return [...DISCOUNTS_QUERY_KEY, 'detail', discountId] as const;
}

export function fetchDiscounts(
  filters: DiscountListFilters,
  signal: AbortSignal,
): Promise<DiscountPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/discounts', {
        params: {
          query: {
            ...(filters.kind ? { kind: [filters.kind] } : {}),
            ...(statusesOf(filters.status).length > 0
              ? { status: statusesOf(filters.status) }
              : {}),
            ...(filters.q ? { q: filters.q } : {}),
            limit: DISCOUNTS_PAGE_SIZE,
            offset: (filters.page - 1) * DISCOUNTS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchDiscount(
  discountId: string,
  signal: AbortSignal,
): Promise<DiscountDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/discounts/{discount_id}', {
        params: { path: { discount_id: discountId } },
        signal,
      }),
    { signal },
  );
}

export function createDiscount(body: DiscountCreate): Promise<DiscountDetail> {
  return apiCall(() => api.POST('/api/v1/billing/discounts', { body }));
}

export function updateDiscount(
  discountId: string,
  body: DiscountUpdate,
): Promise<DiscountDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/billing/discounts/{discount_id}', {
      params: { path: { discount_id: discountId } },
      body,
    }),
  );
}

export function addAssignment(
  discountId: string,
  body: { student_id: string } | { family_id: string },
): Promise<DiscountDetail> {
  return apiCall(() =>
    api.POST('/api/v1/billing/discounts/{discount_id}/assignments', {
      params: { path: { discount_id: discountId } },
      body,
    }),
  );
}

export async function removeAssignment(
  discountId: string,
  assignmentId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE(
      '/api/v1/billing/discounts/{discount_id}/assignments/{assignment_id}',
      {
        params: {
          path: { discount_id: discountId, assignment_id: assignmentId },
        },
      },
    ),
  );
}
