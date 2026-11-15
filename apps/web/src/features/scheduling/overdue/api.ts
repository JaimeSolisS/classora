import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The overdue closeouts list (#301 B; #301b A3): confirmed classes that
// have ended without a closeout, oldest first (`teaching_hours.approve`).
// Every read sits under OVERDUE_CLOSEOUTS_KEY, which the Schedule page's
// count link shares and the on-behalf writes invalidate.

export type OverdueCloseoutItem = components['schemas']['OverdueCloseoutItem'];
export type OverdueCloseoutPage =
  components['schemas']['OffsetPage_OverdueCloseoutItem_'];

/** The API's default page size. */
export const OVERDUE_PAGE_SIZE = 25;

export const OVERDUE_CLOSEOUTS_KEY = ['closeouts', 'overdue'] as const;

export interface OverdueQuery {
  limit: number;
  offset: number;
  tutor_user_id?: string;
  from?: string;
  to?: string;
}

export function overdueKey(query: OverdueQuery) {
  return [...OVERDUE_CLOSEOUTS_KEY, query] as const;
}

export function fetchOverdueCloseouts(
  query: OverdueQuery,
  signal?: AbortSignal,
): Promise<OverdueCloseoutPage> {
  return apiCall(
    () => api.GET('/api/v1/closeouts/overdue', { params: { query }, signal }),
    { signal },
  );
}
