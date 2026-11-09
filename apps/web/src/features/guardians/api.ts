import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { COMBOBOX_PAGE_SIZE, type OptionsPage } from '@/components/ui/combobox';

// The guardian profile and link calls (#34). The list, the detail and the
// pickers' searches are queries; create, update, link and unlink are direct
// calls. A change replaces the cached guardian detail or refetches the
// student detail.

export type GuardianListItem = components['schemas']['GuardianListItem'];
export type GuardianListPage =
  components['schemas']['OffsetPage_GuardianListItem_'];
export type GuardianDetail = components['schemas']['GuardianDetail'];
export type GuardianAccount = components['schemas']['GuardianAccount'];
export type GuardianStudentItem = components['schemas']['GuardianStudentItem'];
export type GuardianCreate = components['schemas']['GuardianCreate'];
export type GuardianUpdate = components['schemas']['GuardianUpdate'];
export type GuardianRelationship =
  components['schemas']['GuardianRelationship'];
export type StudentGuardianItem = components['schemas']['StudentGuardianItem'];
export type StudentGuardianCreate =
  components['schemas']['StudentGuardianCreate'];
export type AccountOption = components['schemas']['UserListItem'];
export type GuardianStatusFilter = 'active' | 'inactive' | 'all';

/** Rows per page of the guardian list (the API default). */
export const GUARDIANS_PAGE_SIZE = 25;

/** Every cached guardian query (list, detail and picker searches). */
export const GUARDIANS_QUERY_KEY = ['guardians'] as const;
/** Every cached list page: invalidated after any change. */
export const GUARDIANS_LIST_QUERY_KEY = [
  ...GUARDIANS_QUERY_KEY,
  'list',
] as const;

export interface GuardianListParams {
  q: string;
  status: GuardianStatusFilter;
  /** 1-based page number. */
  page: number;
}

export function guardiansListKey(params: GuardianListParams) {
  return [...GUARDIANS_LIST_QUERY_KEY, params] as const;
}

export function guardianDetailKey(guardianId: string) {
  return [...GUARDIANS_QUERY_KEY, 'detail', guardianId] as const;
}

export function fetchGuardians(
  { q, status, page }: GuardianListParams,
  signal: AbortSignal,
): Promise<GuardianListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/guardians', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(status !== 'active' ? { status } : {}),
            limit: GUARDIANS_PAGE_SIZE,
            offset: (page - 1) * GUARDIANS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchGuardian(
  guardianId: string,
  signal: AbortSignal,
): Promise<GuardianDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/guardians/{guardian_id}', {
        params: { path: { guardian_id: guardianId } },
        signal,
      }),
    { signal },
  );
}

/**
 * The active guardians, or the matches for `q` (blank `q` is never sent):
 * the family dialog's guardian combobox (#545) and the student's "Link
 * guardian" dialog (#548).
 */
export async function fetchGuardianOptions(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<GuardianListItem>> {
  const text = q?.trim() ?? '';
  const page = await apiCall(
    () =>
      api.GET('/api/v1/guardians', {
        params: {
          query: {
            ...(text ? { q: text } : {}),
            status: 'active',
            limit: COMBOBOX_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
  return { items: page.items, total: page.total };
}

/**
 * The accounts holding the guardian role, or the matches for `q` (the
 * account picker, #548; blank `q` is never sent). Every family.manage
 * holder also holds user.view.
 */
export async function searchGuardianAccounts(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<AccountOption>> {
  const text = q?.trim() ?? '';
  const page = await apiCall(
    () =>
      api.GET('/api/v1/users', {
        params: {
          query: {
            ...(text ? { q: text } : {}),
            role: 'guardian',
            limit: COMBOBOX_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
  return { items: page.items, total: page.total };
}

export function createGuardian(body: GuardianCreate): Promise<GuardianDetail> {
  return apiCall(() => api.POST('/api/v1/guardians', { body }));
}

/** PATCH a profile: `body` holds `version` and only the changed fields. */
export function updateGuardian(
  guardianId: string,
  body: GuardianUpdate,
): Promise<GuardianDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/guardians/{guardian_id}', {
      params: { path: { guardian_id: guardianId } },
      body,
    }),
  );
}

export function linkGuardian(
  studentId: string,
  body: StudentGuardianCreate,
): Promise<StudentGuardianItem> {
  return apiCall(() =>
    api.POST('/api/v1/students/{student_id}/guardians', {
      params: { path: { student_id: studentId } },
      body,
    }),
  );
}

export async function unlinkGuardian(
  studentId: string,
  guardianId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/students/{student_id}/guardians/{guardian_id}', {
      params: { path: { student_id: studentId, guardian_id: guardianId } },
    }),
  );
}
