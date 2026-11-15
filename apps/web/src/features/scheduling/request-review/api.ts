import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The staff review of a family's session request (#60 B-D): the review
// read, the confirmation (the session is offered to the chosen tutor) and
// the rejection with a reason the family sees.

export type SessionRequestReview =
  components['schemas']['SessionRequestReview'];
export type SessionRequestConfirm =
  components['schemas']['SessionRequestConfirm'];
export type SessionRequestConfirmed =
  components['schemas']['SessionRequestConfirmed'];

export function requestReviewKey(requestId: string) {
  return ['scheduling', 'request-review', requestId] as const;
}

export function fetchRequestReview(
  requestId: string,
  signal?: AbortSignal,
): Promise<SessionRequestReview> {
  return apiCall(
    () =>
      api.GET('/api/v1/session-requests/{request_id}/review', {
        params: { path: { request_id: requestId } },
        signal,
      }),
    { signal },
  );
}

export function confirmSessionRequest(
  requestId: string,
  body: SessionRequestConfirm,
): Promise<SessionRequestConfirmed> {
  return apiCall(() =>
    api.POST('/api/v1/session-requests/{request_id}/confirm', {
      params: { path: { request_id: requestId } },
      body,
    }),
  );
}

export function rejectSessionRequest(
  requestId: string,
  body: { version: number; reason: string },
): Promise<SessionRequestReview> {
  return apiCall(() =>
    api.POST('/api/v1/session-requests/{request_id}/reject', {
      params: { path: { request_id: requestId } },
      body,
    }),
  );
}
