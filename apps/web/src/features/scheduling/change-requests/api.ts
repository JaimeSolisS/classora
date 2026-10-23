import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The staff review of a cancellation or reschedule request (#62 C): the
// review read, the approval (optionally as an exception, with a room for a
// reschedule) and the rejection with a reason the requester sees.

export type ChangeRequestReview = components['schemas']['ChangeRequestReview'];
export type ChangeRequestApprove =
  components['schemas']['ChangeRequestApprove'];
export type ChangeRequestApproved =
  components['schemas']['ChangeRequestApproved'];
export type ChangeRequestOutcome =
  components['schemas']['ChangeRequestOutcome'];

export function changeRequestReviewKey(requestId: string) {
  return ['scheduling', 'change-request-review', requestId] as const;
}

export function fetchChangeRequestReview(
  requestId: string,
  signal?: AbortSignal,
): Promise<ChangeRequestReview> {
  return apiCall(
    () =>
      api.GET('/api/v1/change-requests/{change_request_id}/review', {
        params: { path: { change_request_id: requestId } },
        signal,
      }),
    { signal },
  );
}

export function approveChangeRequest(
  requestId: string,
  body: ChangeRequestApprove,
): Promise<ChangeRequestApproved> {
  return apiCall(() =>
    api.POST('/api/v1/change-requests/{change_request_id}/approve', {
      params: { path: { change_request_id: requestId } },
      body,
    }),
  );
}

export function rejectChangeRequest(
  requestId: string,
  body: { version: number; reason: string },
): Promise<ChangeRequestReview> {
  return apiCall(() =>
    api.POST('/api/v1/change-requests/{change_request_id}/reject', {
      params: { path: { change_request_id: requestId } },
      body,
    }),
  );
}
