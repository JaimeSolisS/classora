import type { components } from '@classora/api-client';
import { api, apiCall, ApiError } from '@/app/api';
import type { ExpectedErrorsMeta } from '@/app/queryClient';
import { shouldRetryQuery } from '@/app/queryClient';
import { COHORTS_KEY } from '../api';

// The cohort's course completion (#323, the #85 API): each actively
// enrolled student's progress against the completion criteria of the
// cohort's snapshot, computed live by the API. The page only shows it.

export type CohortCompletion = components['schemas']['CohortCompletion'];
export type StudentCompletion = components['schemas']['StudentCompletion'];
export type CompletionCriteria = components['schemas']['CompletionCriteria'];
export type CompletionStatus = components['schemas']['CompletionStatus'];
export type CriterionStatus = components['schemas']['CriterionStatus'];
export type AssessmentCompletionItem =
  components['schemas']['AssessmentCompletionItem'];
export type ModuleCompletionItem =
  components['schemas']['ModuleCompletionItem'];

/** Under COHORTS_KEY: an enroll or a withdrawal refreshes it too. */
export function completionKey(cohortId: string) {
  return [...COHORTS_KEY, 'completion', cohortId] as const;
}

export function fetchCohortCompletion(
  cohortId: string,
  signal?: AbortSignal,
): Promise<CohortCompletion> {
  return apiCall(
    () =>
      api.GET('/api/v1/cohorts/{cohort_id}/completion', {
        params: { path: { cohort_id: cohortId } },
        signal,
      }),
    { signal },
  );
}

/**
 * The answers that hide the section (W4): a `403` (Finance-like callers)
 * or a `404` (no visibility: a withdrawn student, an adult's guardian
 * without consent, a former tutor). Shown as nothing, so never reported
 * (#315) and never retried.
 */
export const HIDDEN_STATUSES = [403, 404] as const;

export const COMPLETION_META = {
  expectedStatuses: HIDDEN_STATUSES,
} as const satisfies ExpectedErrorsMeta;

/** Whether `error` hides the section. */
export function hidesCompletion(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status !== undefined &&
    (HIDDEN_STATUSES as readonly number[]).includes(error.status)
  );
}

/** The app's retry policy, never for a hiding answer. */
export function retryCompletion(failureCount: number, error: unknown) {
  return !hidesCompletion(error) && shouldRetryQuery(failureCount, error);
}
