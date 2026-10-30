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

// The cancellation billing rules (#99, #385): the overview (in force,
// upcoming oldest first, history newest first) is a query of the Settings
// tab; scheduling a version and deleting an upcoming one are direct calls
// after which the page invalidates the overview.

export type CancellationRule = components['schemas']['CancellationRuleItem'];
export type CancellationRuleOverview =
  components['schemas']['CancellationRuleOverview'];
export type CancellationRuleCreate =
  components['schemas']['CancellationRuleCreate'];
export type CancellationOutcome = CancellationRule['late_outcome'];

export const CANCELLATION_RULES_KEY = [
  'billing',
  'cancellation-rules',
] as const;

export function fetchCancellationRules(
  signal: AbortSignal,
): Promise<CancellationRuleOverview> {
  return apiCall(
    () => api.GET('/api/v1/billing/cancellation-rules', { signal }),
    { signal },
  );
}

export function createCancellationRule(
  body: CancellationRuleCreate,
): Promise<CancellationRule> {
  return apiCall(() =>
    api.POST('/api/v1/billing/cancellation-rules', { body }),
  );
}

export async function deleteCancellationRule(ruleId: string): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/billing/cancellation-rules/{rule_id}', {
      params: { path: { rule_id: ruleId } },
    }),
  );
}
