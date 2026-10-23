import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The calls of the tutoring group pages (#234, the #56 API): the list, the
// detail, a student's sessions, the create, the rename / new maximum, and
// enrolling and withdrawing students. Plus the classroom options with their
// capacity (the create form shows it, E1).

export type GroupListItem = components['schemas']['GroupListItem'];
export type GroupPage = components['schemas']['GroupPage'];
export type GroupDetail = components['schemas']['GroupDetail'];
export type GroupCreate = components['schemas']['GroupCreate'];
export type GroupCreated = components['schemas']['GroupCreated'];
export type GroupUpdate = components['schemas']['GroupUpdate'];
export type GroupOverride = components['schemas']['GroupOverride'];
export type GroupEnrollmentItem = components['schemas']['GroupEnrollmentItem'];
export type GroupEnrollmentCreate =
  components['schemas']['GroupEnrollmentCreate'];
export type GroupEnrollmentCreated =
  components['schemas']['GroupEnrollmentCreated'];
export type GroupWithdrawal = components['schemas']['GroupWithdrawal'];
export type GroupSessionItem = components['schemas']['GroupSessionItem'];
export type GroupSessions = components['schemas']['GroupSessions'];
export type GroupOccurrenceItem = components['schemas']['GroupOccurrenceItem'];

export type GroupStatusFilter = 'active' | 'ended' | 'all';

/** The list's filters (the URL holds them, B1). */
export interface GroupListFilters {
  q: string;
  /** A subject id, or '' for every subject. */
  subject: string;
  status: GroupStatusFilter;
}

/** One page of the list (B2). */
export const GROUPS_PAGE_SIZE = 25;

export const GROUPS_KEY = ['groups'] as const;

export function groupsListKey(filters: GroupListFilters) {
  return [...GROUPS_KEY, 'list', filters] as const;
}

export function groupKey(id: string) {
  return [...GROUPS_KEY, 'detail', id] as const;
}

export function groupSessionsKey(id: string, from: string, to: string) {
  return [...GROUPS_KEY, 'sessions', id, from, to] as const;
}

export function fetchGroups(
  filters: GroupListFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<GroupPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/groups', {
        params: {
          query: {
            ...(filters.q ? { q: filters.q } : {}),
            ...(filters.subject ? { subject_id: filters.subject } : {}),
            status: filters.status,
            limit: GROUPS_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchGroup(
  id: string,
  signal?: AbortSignal,
): Promise<GroupDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/groups/{group_id}', {
        params: { path: { group_id: id } },
        signal,
      }),
    { signal },
  );
}

/** The group's sessions overlapping [from, to) (instants, at most 45 days). */
export function fetchGroupSessions(
  id: string,
  from: string,
  to: string,
  signal?: AbortSignal,
): Promise<GroupSessions> {
  return apiCall(
    () =>
      api.GET('/api/v1/groups/{group_id}/sessions', {
        params: { path: { group_id: id }, query: { from, to } },
        signal,
      }),
    { signal },
  );
}

export function createGroup(body: GroupCreate): Promise<GroupCreated> {
  return apiCall(() => api.POST('/api/v1/groups', { body }));
}

export function updateGroup(
  id: string,
  body: GroupUpdate,
): Promise<GroupDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/groups/{group_id}', {
      params: { path: { group_id: id } },
      body,
    }),
  );
}

export function enrollStudent(
  id: string,
  body: GroupEnrollmentCreate,
): Promise<GroupEnrollmentCreated> {
  return apiCall(() =>
    api.POST('/api/v1/groups/{group_id}/enrollments', {
      params: { path: { group_id: id } },
      body,
    }),
  );
}

export function withdrawEnrollment(
  groupId: string,
  enrollmentId: string,
  body: GroupWithdrawal,
): Promise<GroupEnrollmentItem> {
  return apiCall(() =>
    api.POST('/api/v1/groups/{group_id}/enrollments/{enrollment_id}/withdraw', {
      params: { path: { group_id: groupId, enrollment_id: enrollmentId } },
      body,
    }),
  );
}

/** A classroom option with its capacity (E1). */
export interface ClassroomCapacityOption {
  id: string;
  name: string;
  capacity: number;
}

export const GROUP_OPTIONS_KEY = [...GROUPS_KEY, 'options'] as const;

/** The active classrooms (one page) with their capacity. */
export async function fetchClassroomCapacityOptions(
  signal: AbortSignal,
): Promise<ClassroomCapacityOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/classrooms', {
        params: { query: { status: 'active', limit: 100 } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((room) => room.is_active)
    .map((room) => ({ id: room.id, name: room.name, capacity: room.capacity }));
}
