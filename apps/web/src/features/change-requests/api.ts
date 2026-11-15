import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import {
  fetchSlots,
  type AvailableSlots,
  type SlotsQuery,
} from '@/features/family-portal/requests/api';

// Change requests by families and tutors (#62b, the #62 API): the pending
// request of a session (the details dialog's check), sending one, the
// caller's list, the withdrawal, the current scheduling policy (the outcome
// preview) and the family's available times (#59) for a reschedule. The
// API's record policy decides what each caller may ask for and read.

export type ChangeRequestItem = components['schemas']['ChangeRequestItem'];
export type ChangeRequestDetail = components['schemas']['ChangeRequestDetail'];
export type ChangeRequestCreate = components['schemas']['ChangeRequestCreate'];
export type ChangeRequestKind = components['schemas']['ChangeRequestKind'];
export type ChangeRequestStatus = components['schemas']['ChangeRequestStatus'];
export type ChangeRequestOutcome =
  components['schemas']['ChangeRequestOutcome'];
export type ChangeRequestPage =
  components['schemas']['OffsetPage_ChangeRequestItem_'];
export type SchedulingPolicy = components['schemas']['SchedulingPolicyItem'];

/** The list's status filter (`all` is the API's default). */
export type StatusFilter = ChangeRequestStatus | 'all';

/** One page of "My change requests" (#62 D1: offset pages). */
export const PAGE_SIZE = 25;

export const CHANGE_REQUESTS_KEY = ['change-requests'] as const;

export function pendingRequestKey(sessionId: string) {
  return [...CHANGE_REQUESTS_KEY, 'pending', sessionId] as const;
}

export const CURRENT_POLICY_KEY = [
  ...CHANGE_REQUESTS_KEY,
  'current-policy',
] as const;

export const RESCHEDULE_SLOTS_KEY = [
  ...CHANGE_REQUESTS_KEY,
  'available-slots',
] as const;

export interface ListQuery {
  status: StatusFilter;
  studentId: string | null;
  offset: number;
}

/** The caller's requests, newest first (one offset page). */
export function fetchChangeRequests(
  query: ListQuery,
  signal?: AbortSignal,
): Promise<ChangeRequestPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/change-requests', {
        params: {
          query: {
            status: query.status,
            limit: PAGE_SIZE,
            offset: query.offset,
            ...(query.studentId ? { student_id: query.studentId } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

/** The pending request of one session the caller may see, or null. */
export async function fetchPendingRequest(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ChangeRequestItem | null> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/change-requests', {
        params: {
          query: { class_session_id: sessionId, status: 'pending', limit: 1 },
        },
        signal,
      }),
    { signal },
  );
  return page.items[0] ?? null;
}

export function createChangeRequest(
  body: ChangeRequestCreate,
): Promise<ChangeRequestDetail> {
  return apiCall(() => api.POST('/api/v1/change-requests', { body }));
}

export function withdrawChangeRequest(
  request: Pick<ChangeRequestItem, 'id' | 'version'>,
): Promise<ChangeRequestDetail> {
  return apiCall(() =>
    api.POST('/api/v1/change-requests/{change_request_id}/withdraw', {
      params: { path: { change_request_id: request.id } },
      body: { version: request.version },
    }),
  );
}

/** The scheduling policy in force now (#61). */
export async function fetchCurrentPolicy(
  signal?: AbortSignal,
): Promise<SchedulingPolicy> {
  const overview = await apiCall(
    () => api.GET('/api/v1/scheduling-policies', { signal }),
    { signal },
  );
  return overview.current;
}

/** The calls of the request dialog (stubs in stories and tests). */
export interface ChangeRequestLoaders {
  fetchPendingRequest: typeof fetchPendingRequest;
  fetchCurrentPolicy: typeof fetchCurrentPolicy;
  fetchSlots: (
    query: SlotsQuery,
    signal?: AbortSignal,
  ) => Promise<AvailableSlots>;
  createChangeRequest: typeof createChangeRequest;
}

export const CHANGE_REQUEST_LOADERS: ChangeRequestLoaders = {
  fetchPendingRequest,
  fetchCurrentPolicy,
  fetchSlots,
  createChangeRequest,
};
