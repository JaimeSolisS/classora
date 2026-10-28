import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The billing settings calls (#91, #366 B): the start date is a query that
// the Settings and Unbilled tabs share; changing it is a direct call after
// which the page invalidates the billing settings and the unbilled list.

export type BillingSettings = components['schemas']['BillingSettingsDetail'];
export type BillingSettingsUpdate =
  components['schemas']['BillingSettingsUpdate'];

export const BILLING_SETTINGS_KEY = ['billing', 'settings'] as const;

export function fetchBillingSettings(
  signal: AbortSignal,
): Promise<BillingSettings> {
  return apiCall(() => api.GET('/api/v1/billing/settings', { signal }), {
    signal,
  });
}

export function updateBillingSettings(
  body: BillingSettingsUpdate,
): Promise<BillingSettings> {
  return apiCall(() => api.PATCH('/api/v1/billing/settings', { body }));
}
