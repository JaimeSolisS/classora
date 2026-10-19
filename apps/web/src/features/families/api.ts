import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { PICKER_LIMIT } from '@/features/guardians/api';

// The family grouping calls (#172). The list and the detail are queries;
// create, update, add and remove are direct calls. A change replaces the
// cached family detail (or refetches it) and invalidates the list.

export type FamilyListItem = components['schemas']['FamilyListItem'];
export type FamilyListPage =
  components['schemas']['OffsetPage_FamilyListItem_'];
export type FamilyDetail = components['schemas']['FamilyDetail'];
export type FamilyCreate = components['schemas']['FamilyCreate'];
export type FamilyUpdate = components['schemas']['FamilyUpdate'];
export type FamilyStudentItem = components['schemas']['FamilyStudentItem'];
export type FamilyGuardianItem = components['schemas']['FamilyGuardianItem'];
export type FamilyReference = components['schemas']['FamilyReference'];
export type StudentOption = components['schemas']['StudentListItem'];
export type FamilyStatusFilter = 'active' | 'inactive' | 'all';
/** Which members a dialog or a remove button changes. */
export type MemberKind = 'students' | 'guardians';

/** Rows per page of the family list (the API default). */
export const FAMILIES_PAGE_SIZE = 25;

/** Every cached family query (list and detail). */
export const FAMILIES_QUERY_KEY = ['families'] as const;
/** Every cached list page: invalidated after any change. */
export const FAMILIES_LIST_QUERY_KEY = [...FAMILIES_QUERY_KEY, 'list'] as const;

export interface FamilyListParams {
  q: string;
  status: FamilyStatusFilter;
  /** 1-based page number. */
  page: number;
}

export function familiesListKey(params: FamilyListParams) {
  return [...FAMILIES_LIST_QUERY_KEY, params] as const;
}

export function familyDetailKey(familyId: string) {
  return [...FAMILIES_QUERY_KEY, 'detail', familyId] as const;
}

export function fetchFamilies(
  { q, status, page }: FamilyListParams,
  signal: AbortSignal,
): Promise<FamilyListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/families', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(status !== 'active' ? { status } : {}),
            limit: FAMILIES_PAGE_SIZE,
            offset: (page - 1) * FAMILIES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchFamily(
  familyId: string,
  signal: AbortSignal,
): Promise<FamilyDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}', {
        params: { path: { family_id: familyId } },
        signal,
      }),
    { signal },
  );
}

/** Up to 10 students matching `q` (any status: inactive students may join). */
export async function searchStudents(
  q: string,
  signal: AbortSignal,
): Promise<StudentOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: { query: { q, status: 'all', limit: PICKER_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items;
}

export function createFamily(body: FamilyCreate): Promise<FamilyDetail> {
  return apiCall(() => api.POST('/api/v1/families', { body }));
}

/** PATCH a family: `body` holds `version` and only the changed fields. */
export function updateFamily(
  familyId: string,
  body: FamilyUpdate,
): Promise<FamilyDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/families/{family_id}', {
      params: { path: { family_id: familyId } },
      body,
    }),
  );
}

/** PUT a member (idempotent: a member of this family is a no-op). */
export async function addMember(
  familyId: string,
  kind: MemberKind,
  memberId: string,
): Promise<void> {
  if (kind === 'students') {
    await apiCall(() =>
      api.PUT('/api/v1/families/{family_id}/students/{student_id}', {
        params: { path: { family_id: familyId, student_id: memberId } },
      }),
    );
    return;
  }
  await apiCall(() =>
    api.PUT('/api/v1/families/{family_id}/guardians/{guardian_id}', {
      params: { path: { family_id: familyId, guardian_id: memberId } },
    }),
  );
}

export async function removeMember(
  familyId: string,
  kind: MemberKind,
  memberId: string,
): Promise<void> {
  if (kind === 'students') {
    await apiCall(() =>
      api.DELETE('/api/v1/families/{family_id}/students/{student_id}', {
        params: { path: { family_id: familyId, student_id: memberId } },
      }),
    );
    return;
  }
  await apiCall(() =>
    api.DELETE('/api/v1/families/{family_id}/guardians/{guardian_id}', {
      params: { path: { family_id: familyId, guardian_id: memberId } },
    }),
  );
}
