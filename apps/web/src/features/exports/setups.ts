import type { components } from '@classora/api-client';
import { api, apiCall, ApiError } from '@/app/api';
import { EXPORTS_QUERY_KEY } from './api';

// Saved export setups (M8-08, #125; `_docs/api.md` "Saved export setups").
// A setup is a name plus one export's parameters. Running it posts
// `setup.parameters` unchanged to `POST /exports` (no run route, E4), so
// every export check applies to whoever runs it.

export type ReportSetup = components['schemas']['ReportSetupItem'];
export type SharedRole = components['schemas']['SharedRole'];

/** The staff roles a setup may be shared with, in the roles' usual order. */
export const SHARED_ROLES: readonly SharedRole[] = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
];

/** The API's name limit (after trimming). */
export const SETUP_NAME_MAX = 80;

export const SETUPS_KEY = [...EXPORTS_QUERY_KEY, 'setups'] as const;

export function fetchSetups(
  signal: AbortSignal,
): Promise<components['schemas']['ReportSetupList']> {
  return apiCall(() => api.GET('/api/v1/report-setups', { signal }), {
    signal,
  });
}

export function createSetup(
  body: components['schemas']['ReportSetupCreate'],
): Promise<ReportSetup> {
  return apiCall(() => api.POST('/api/v1/report-setups', { body }));
}

export function updateSetup(
  setupId: string,
  body: components['schemas']['ReportSetupUpdate'],
): Promise<ReportSetup> {
  return apiCall(() =>
    api.PATCH('/api/v1/report-setups/{setup_id}', {
      params: { path: { setup_id: setupId } },
      body,
    }),
  );
}

export async function deleteSetup(setupId: string): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/report-setups/{setup_id}', {
      params: { path: { setup_id: setupId } },
    }),
  );
}

/** The client's name check (D4): required, at most 80 after trimming. */
export function setupNameError(
  name: string,
): 'nameRequired' | 'nameTooLong' | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'nameRequired';
  if (trimmed.length > SETUP_NAME_MAX) return 'nameTooLong';
  return undefined;
}

/** What a failed save means for the dialog (D4). */
export type SetupProblem =
  | { kind: 'name'; key: 'nameTaken' | 'nameInvalid' }
  | { kind: 'limit'; max: number }
  | { kind: 'stale' }
  | { kind: 'range' }
  | { kind: 'other' };

export function setupProblem(error: unknown): SetupProblem {
  if (!(error instanceof ApiError)) return { kind: 'other' };
  if (error.code === 'report_setup_name_taken') {
    return { kind: 'name', key: 'nameTaken' };
  }
  if (error.code === 'report_setup_limit_reached') {
    const max = error.problem?.details?.max;
    return { kind: 'limit', max: typeof max === 'number' ? max : 50 };
  }
  if (error.code === 'version_conflict') return { kind: 'stale' };
  if (error.code === 'export_range_invalid') return { kind: 'range' };
  if (
    error.fieldErrors.some(
      (fieldError) =>
        fieldError.path[0] === 'body' && fieldError.path[1] === 'name',
    )
  ) {
    return { kind: 'name', key: 'nameInvalid' };
  }
  return { kind: 'other' };
}
