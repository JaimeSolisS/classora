import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { APPROVALS_QUERY_KEY } from '@/features/approvals/api';
import { ASSIGNMENTS_KEY } from '@/features/assignments/api';
import { OVERDUE_QUERY_KEY } from '@/features/billing/overdue/api';
import { OFFERS_KEY } from '@/features/tutor-portal/offers/api';

// The pending-item counts of the home page (HF-06 C1) that have no
// existing fetcher of the right shape: each is one small read of an
// existing endpoint. The keys sit under the feature's own key, so a write
// elsewhere that invalidates that feature refreshes Home too (D6). The
// agenda and the other counts reuse the calendar, My schedule, closeout and
// family billing fetchers as they are.

export type StudentAssignmentSummary =
  components['schemas']['AssignmentStateSummary'];

export const HOME_KEY = ['home'] as const;

export const homeKeys = {
  approvals: [...APPROVALS_QUERY_KEY, 'home-count'] as const,
  overdueAccounts: [...OVERDUE_QUERY_KEY, 'home-count'] as const,
  offers: [...OFFERS_KEY, 'home-count'] as const,
  // Under the working lists, which every assignment write invalidates.
  toGrade: [...ASSIGNMENTS_KEY, 'working', 'home-to-grade'] as const,
  studentHomework: (studentId: string) =>
    [...ASSIGNMENTS_KEY, 'student', studentId, 'list', 'home-summary'] as const,
};

/** The open approval requests the caller reviews (`total`). */
export async function fetchOpenApprovalsTotal(
  signal: AbortSignal,
): Promise<number> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/approval-requests', {
        params: { query: { status: 'open', limit: 1 } },
        signal,
      }),
    { signal },
  );
  return page.total;
}

/** The accounts with an overdue charge, as the overdue page's default. */
export async function fetchOverdueAccountsTotal(
  signal: AbortSignal,
): Promise<number> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/billing/overdue-accounts', {
        params: {
          query: { min_days_overdue: 1, family_status: 'all', limit: 1 },
        },
        signal,
      }),
    { signal },
  );
  return page.total;
}

/** The tutor's own offers waiting for an answer (`total`). */
export async function fetchPendingOffersTotal(
  signal: AbortSignal,
): Promise<number> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/assignment-offers', {
        params: { query: { status: 'pending', limit: 1 } },
        signal,
      }),
    { signal },
  );
  return page.total;
}

/** The most homework one "to grade" read looks at (the API maximum). */
export const TO_GRADE_LIMIT = 50;

export interface ToGrade {
  /** Submissions handed in and not graded, over the page read. */
  count: number;
  /** More homework than one page: the count is a lower bound. */
  more: boolean;
}

/**
 * The tutor's homework past its deadline: the handed-in, ungraded
 * submissions over one page (C1), "more" when there is a next page.
 */
export async function fetchToGrade(signal: AbortSignal): Promise<ToGrade> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/assignments', {
        params: { query: { due: 'past', limit: TO_GRADE_LIMIT } },
        signal,
      }),
    { signal },
  );
  return {
    count: page.items.reduce((sum, item) => sum + item.counts.submitted, 0),
    more: page.next_cursor !== null,
  };
}

/** One student's homework summary per state (the list's `summary`). */
export async function fetchHomeworkSummary(
  studentId: string,
  signal: AbortSignal,
): Promise<StudentAssignmentSummary> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/assignments', {
        params: { path: { student_id: studentId }, query: { limit: 1 } },
        signal,
      }),
    { signal },
  );
  return page.summary;
}
