import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The financial report reads (#117, M7-14b). Read-only: no Idempotency-Key.

export type Profitability = components['schemas']['Profitability'];
export type Balances = components['schemas']['Balances'];
export type ReportBasis = components['schemas']['ReportBasis'];
export type ReportClassType = components['schemas']['ReportClassType'];
export type ClassRow = components['schemas']['ClassRow'];
export type CategoryAmount = components['schemas']['CategoryAmount'];
export type VendorAmount = components['schemas']['VendorAmount'];

export interface ProfitabilityParams {
  basis: ReportBasis;
  from: string;
  to: string;
}

/** Every cached report query. */
export const REPORTS_KEY = ['reports', 'finance'] as const;
export const BALANCES_KEY = [...REPORTS_KEY, 'balances'] as const;

export function profitabilityKey(params: ProfitabilityParams) {
  return [...REPORTS_KEY, 'profitability', params] as const;
}

export function fetchProfitability(
  { basis, from, to }: ProfitabilityParams,
  signal: AbortSignal,
): Promise<Profitability> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/finance/profitability', {
        params: { query: { basis, from, to } },
        signal,
      }),
    { signal },
  );
}

export function fetchBalances(signal: AbortSignal): Promise<Balances> {
  return apiCall(
    () => api.GET('/api/v1/reports/finance/balances', { signal }),
    { signal },
  );
}
