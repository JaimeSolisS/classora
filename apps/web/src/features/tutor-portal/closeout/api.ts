import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The session closeout calls (#77): the closeout view (the current tutor or
// a schedule.view holder; anyone else 404) and the tutor's submission. The
// API's record policy decides; the pages only show what it returns. The
// staff session page reuses fetchCloseout.

export type SessionCloseoutView = components['schemas']['SessionCloseoutView'];
export type SessionCloseoutRecord =
  components['schemas']['SessionCloseoutRecord'];
export type SessionCloseoutSubmit =
  components['schemas']['SessionCloseoutSubmit'];
export type CloseoutSummary = components['schemas']['CloseoutSummary'];
export type CloseoutBlock = NonNullable<SessionCloseoutView['block']>;

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

/** The tutor's closeout flow of a session (G1). */
export function closeoutPath(sessionId: string): string {
  return `/my-schedule/sessions/${sessionId}/closeout`;
}
