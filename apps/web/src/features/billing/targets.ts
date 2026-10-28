import { api, apiCall } from '@/app/api';
import { PICKER_LIMIT } from '@/features/guardians/api';
import { fullName } from '@/features/guardians/rules';

// The students and families a discount is assigned to or a custom price is
// for (#367 D2, E3, E4): the picker's search (GET /students?q=, student.view;
// GET /families?q=, guardian.view; inactive ones too, the API accepts them)
// and the name of a target given by id (a deep link).

export type TargetKind = 'student' | 'family';

export interface TargetOption {
  kind: TargetKind;
  id: string;
  name: string;
}

export const TARGET_KINDS: readonly TargetKind[] = ['student', 'family'];

export const TARGETS_QUERY_KEY = ['billing', 'targets'] as const;

/** Up to PICKER_LIMIT students or families matching `q`, any status. */
export async function searchTargets(
  kind: TargetKind,
  q: string,
  signal: AbortSignal,
): Promise<TargetOption[]> {
  if (kind === 'student') {
    const page = await apiCall(
      () =>
        api.GET('/api/v1/students', {
          params: { query: { q, status: 'all', limit: PICKER_LIMIT } },
          signal,
        }),
      { signal },
    );
    return page.items.map((student) => ({
      kind,
      id: student.id,
      name: fullName(student),
    }));
  }
  const page = await apiCall(
    () =>
      api.GET('/api/v1/families', {
        params: { query: { q, status: 'all', limit: PICKER_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((family) => ({
    kind,
    id: family.id,
    name: family.name,
  }));
}

/** One student or family by id, with its name. */
export async function fetchTarget(
  kind: TargetKind,
  id: string,
  signal: AbortSignal,
): Promise<TargetOption> {
  if (kind === 'student') {
    const student = await apiCall(
      () =>
        api.GET('/api/v1/students/{student_id}', {
          params: { path: { student_id: id } },
          signal,
        }),
      { signal },
    );
    return { kind, id: student.id, name: fullName(student) };
  }
  const family = await apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}', {
        params: { path: { family_id: id } },
        signal,
      }),
    { signal },
  );
  return { kind, id: family.id, name: family.name };
}

export function targetKey(kind: TargetKind, id: string) {
  return [...TARGETS_QUERY_KEY, kind, id] as const;
}
