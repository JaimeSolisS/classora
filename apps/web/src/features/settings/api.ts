import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The business settings calls (#37): one query for the settings and one
// PATCH. A time-zone change answers 403 reauth_required without a recent
// password confirmation: apiCall opens the #28 dialog and retries once.

export type BusinessSettings = components['schemas']['BusinessSettingsDetail'];
export type BusinessSettingsUpdate =
  components['schemas']['BusinessSettingsUpdate'];
export type OperatingHoursItem = components['schemas']['OperatingHoursItem'];

/** The cached settings query. */
export const SETTINGS_QUERY_KEY = ['business-settings'] as const;

export function fetchSettings(signal: AbortSignal): Promise<BusinessSettings> {
  return apiCall(() => api.GET('/api/v1/business-settings', { signal }), {
    signal,
  });
}

/** PATCH the settings: `body` holds `version` and only the changed fields. */
export function updateSettings(
  body: BusinessSettingsUpdate,
): Promise<BusinessSettings> {
  return apiCall(() => api.PATCH('/api/v1/business-settings', { body }));
}
