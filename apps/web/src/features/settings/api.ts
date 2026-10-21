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

// The scheduling policy calls (#61): the overview (current, upcoming,
// history), publishing a new version and deleting an upcoming one.

export type SchedulingPolicy = components['schemas']['SchedulingPolicyItem'];
export type SchedulingPolicyOverview =
  components['schemas']['SchedulingPolicyOverview'];
export type SchedulingPolicyCreate =
  components['schemas']['SchedulingPolicyCreate'];

/** The cached scheduling policy overview. */
export const POLICIES_QUERY_KEY = ['scheduling-policies'] as const;

export function fetchPolicies(
  signal: AbortSignal,
): Promise<SchedulingPolicyOverview> {
  return apiCall(() => api.GET('/api/v1/scheduling-policies', { signal }), {
    signal,
  });
}

export function createPolicy(
  body: SchedulingPolicyCreate,
): Promise<SchedulingPolicy> {
  return apiCall(() => api.POST('/api/v1/scheduling-policies', { body }));
}

export async function deletePolicy(policyId: string): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/scheduling-policies/{policy_id}', {
      params: { path: { policy_id: policyId } },
    }),
  );
}
