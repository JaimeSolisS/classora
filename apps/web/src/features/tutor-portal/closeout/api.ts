import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The session closeout calls (#77): the closeout view (the current tutor or
// a schedule.view holder; anyone else 404) and the tutor's submission. The
// API's record policy decides; the pages only show what it returns. The
// staff session page reuses fetchCloseout and adds the review decisions
// (#78: approve, request changes, correct, each with the closeout's
// version). #305: the tutor's resubmission and correction request (the
// same PUT, with the closeout's version).

export type SessionCloseoutView = components['schemas']['SessionCloseoutView'];
export type SessionCloseoutRecord =
  components['schemas']['SessionCloseoutRecord'];
export type SessionCloseoutSubmit =
  components['schemas']['SessionCloseoutSubmit'];
export type CloseoutSummary = components['schemas']['CloseoutSummary'];
export type CloseoutBlock = NonNullable<SessionCloseoutView['block']>;
export type CloseoutStatus = SessionCloseoutRecord['status'];
export type CloseoutApprovalRevision =
  components['schemas']['CloseoutApprovalRevision'];
export type CloseoutCorrectionItem =
  components['schemas']['CloseoutCorrectionItem'];
export type CloseoutCorrectionBody =
  components['schemas']['CloseoutCorrection'];
export type CloseoutSummaryInput =
  components['schemas']['CloseoutSummaryInput'];
export type CloseoutResubmitBody = components['schemas']['CloseoutResubmit'];

export const CLOSEOUT_KEY = ['closeout'] as const;

export function closeoutKey(sessionId: string) {
  return [...CLOSEOUT_KEY, sessionId] as const;
}

export function fetchCloseout(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionCloseoutView> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}/closeout', {
        params: { path: { class_session_id: sessionId } },
        signal,
      }),
    { signal },
  );
}

export function submitCloseout(
  sessionId: string,
  body: SessionCloseoutSubmit,
): Promise<SessionCloseoutView> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/closeout', {
      params: { path: { class_session_id: sessionId } },
      body,
    }),
  );
}

/**
 * #305 A5: the tutor's resubmission (changes requested; an optional note)
 * or correction request (approved; a required reason), through #78's PUT.
 */
export function resubmitCloseout(
  sessionId: string,
  body: CloseoutResubmitBody,
): Promise<SessionCloseoutView> {
  return apiCall(() =>
    api.PUT('/api/v1/class-sessions/{class_session_id}/closeout', {
      params: { path: { class_session_id: sessionId } },
      body,
    }),
  );
}

function sessionPath(sessionId: string) {
  return { path: { class_session_id: sessionId } };
}

/** #78 C1: approve a submitted closeout (its version). */
export function approveCloseout(
  sessionId: string,
  version: number,
): Promise<SessionCloseoutView> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/closeout/approve', {
      params: sessionPath(sessionId),
      body: { version },
    }),
  );
}

/** #78 C2: send a submitted closeout back to its tutor with a reason. */
export function requestCloseoutChanges(
  sessionId: string,
  version: number,
  reason: string,
): Promise<SessionCloseoutView> {
  return apiCall(() =>
    api.POST(
      '/api/v1/class-sessions/{class_session_id}/closeout/request-changes',
      { params: sessionPath(sessionId), body: { version, reason } },
    ),
  );
}

/** #78 C3: correct the minutes or the summary with a reason. */
export function correctCloseout(
  sessionId: string,
  body: CloseoutCorrectionBody,
): Promise<SessionCloseoutView> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/closeout/corrections', {
      params: sessionPath(sessionId),
      body,
    }),
  );
}

/** The tutor's closeout flow of a session (G1). */
export function closeoutPath(sessionId: string): string {
  return `/my-schedule/sessions/${sessionId}/closeout`;
}
