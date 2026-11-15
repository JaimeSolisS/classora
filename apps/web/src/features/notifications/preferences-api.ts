import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The caller's notification preferences (#119 C; M8-02b B1, B5). Not under
// the ['notifications'] key: the live stream invalidates that one on every
// new notification, and the catalogue doesn't change with them.

export type NotificationPreferenceItem =
  components['schemas']['NotificationPreferenceItem'];
export type NotificationPreferences =
  components['schemas']['NotificationPreferences'];

export const NOTIFICATION_PREFERENCES_KEY = [
  'notification-preferences',
] as const;

/** The page's route (E1: any signed-in user, next to /account/password). */
export const NOTIFICATION_PREFERENCES_PATH = '/account/notifications';

/** Refusals that mean the shown catalogue is out of date (B5). */
export const CATALOGUE_CHANGED_CODES: ReadonlySet<string> = new Set([
  'notification_type_unknown',
  'notification_type_mandatory',
]);

export function fetchNotificationPreferences(
  signal: AbortSignal,
): Promise<NotificationPreferences> {
  return apiCall(
    () => api.GET('/api/v1/notifications/preferences', { signal }),
    { signal },
  );
}

/**
 * Turns one type on or off (E2: one item per request). Set semantics on the
 * server, so resending after a lost answer changes nothing (#119 E7).
 */
export function updateNotificationPreference(
  type: string,
  enabled: boolean,
): Promise<NotificationPreferences> {
  return apiCall(() =>
    api.PATCH('/api/v1/notifications/preferences', {
      body: { items: [{ notification_type: type, enabled }] },
    }),
  );
}
