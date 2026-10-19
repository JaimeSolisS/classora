import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The student profile calls (#33). The list, the detail and the active
// levels for the selects are queries; create and update are direct calls
// whose answer (the detail) replaces the cached detail.

export type StudentListItem = components['schemas']['StudentListItem'];
export type StudentListPage =
  components['schemas']['OffsetPage_StudentListItem_'];
export type StudentDetail = components['schemas']['StudentDetail'];
export type StudentPersonal = components['schemas']['StudentPersonal'];
export type EmergencyContact = components['schemas']['EmergencyContact'];
export type StudentCreate = components['schemas']['StudentCreate'];
export type StudentUpdate = components['schemas']['StudentUpdate'];
export type EmergencyContactUpdate =
  components['schemas']['EmergencyContactUpdate'];
export type EducationLevelReference =
  components['schemas']['EducationLevelReference'];
export type ActiveLevelPage =
  components['schemas']['OffsetPage_EducationLevelDetail_'];
export type StudentStatusFilter = 'active' | 'inactive' | 'all';

/** Rows per page of the student list (the API default). */
export const STUDENTS_PAGE_SIZE = 25;

/** Every cached student query (list and detail). */
export const STUDENTS_QUERY_KEY = ['students'] as const;
/** Every cached list page: invalidated after any change. */
export const STUDENTS_LIST_QUERY_KEY = [...STUDENTS_QUERY_KEY, 'list'] as const;
/** The active education levels offered in the selects. */
export const ACTIVE_LEVELS_QUERY_KEY = [
  'catalog',
  'education-levels',
  'active-options',
] as const;

export interface StudentListParams {
  q: string;
  /** An education level id, or '' for all. */
  level: string;
  status: StudentStatusFilter;
  /** 1-based page number. */
  page: number;
}

export function studentsListKey(params: StudentListParams) {
  return [...STUDENTS_LIST_QUERY_KEY, params] as const;
}

export function studentDetailKey(studentId: string) {
  return [...STUDENTS_QUERY_KEY, 'detail', studentId] as const;
}

export function fetchStudents(
  { q, level, status, page }: StudentListParams,
  signal: AbortSignal,
): Promise<StudentListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(level ? { education_level_id: level } : {}),
            ...(status !== 'active' ? { status } : {}),
            limit: STUDENTS_PAGE_SIZE,
            offset: (page - 1) * STUDENTS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchStudent(
  studentId: string,
  signal: AbortSignal,
): Promise<StudentDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}', {
        params: { path: { student_id: studentId } },
        signal,
      }),
    { signal },
  );
}

/** The active education levels (one page of up to 100, by position). */
export function fetchActiveLevels(
  signal: AbortSignal,
): Promise<ActiveLevelPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/education-levels', {
        params: { query: { status: 'active', limit: 100 } },
        signal,
      }),
    { signal },
  );
}

export function createStudent(body: StudentCreate): Promise<StudentDetail> {
  return apiCall(() => api.POST('/api/v1/students', { body }));
}

/** PATCH a profile: `body` holds `version` and only the changed fields. */
export function updateStudent(
  studentId: string,
  body: StudentUpdate,
): Promise<StudentDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/students/{student_id}', {
      params: { path: { student_id: studentId } },
      body,
    }),
  );
}
