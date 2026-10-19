import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The academic catalog calls (#32): education levels and their subjects.
// The lists and the level are queries; creates and updates are direct calls
// after which the pages invalidate the catalog queries.

export type EducationLevel = components['schemas']['EducationLevelDetail'];
export type EducationLevelPage =
  components['schemas']['OffsetPage_EducationLevelDetail_'];
export type EducationLevelCreate =
  components['schemas']['EducationLevelCreate'];
export type EducationLevelUpdate =
  components['schemas']['EducationLevelUpdate'];
export type Subject = components['schemas']['SubjectDetail'];
export type SubjectPage = components['schemas']['OffsetPage_SubjectDetail_'];
export type SubjectUpdate = components['schemas']['SubjectUpdate'];

/** Rows per page of the catalog tables (the API default). */
export const CATALOG_PAGE_SIZE = 25;

/** Every cached catalog query. */
export const CATALOG_QUERY_KEY = ['catalog'] as const;
/** Every cached level query (the list pages and each level). */
export const LEVELS_QUERY_KEY = [
  ...CATALOG_QUERY_KEY,
  'education-levels',
] as const;
/** Every cached subject list. */
export const SUBJECTS_QUERY_KEY = [...CATALOG_QUERY_KEY, 'subjects'] as const;

export function levelsListKey(page: number) {
  return [...LEVELS_QUERY_KEY, 'list', page] as const;
}

export function levelKey(levelId: string) {
  return [...LEVELS_QUERY_KEY, 'detail', levelId] as const;
}

export function subjectsListKey(levelId: string, page: number) {
  return [...SUBJECTS_QUERY_KEY, levelId, page] as const;
}

const offsetOf = (page: number) => (page - 1) * CATALOG_PAGE_SIZE;

/** One page of every level, active or not (the managers' view). */
export function fetchLevels(
  page: number,
  signal: AbortSignal,
): Promise<EducationLevelPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/education-levels', {
        params: {
          query: {
            status: 'all',
            limit: CATALOG_PAGE_SIZE,
            offset: offsetOf(page),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchLevel(
  levelId: string,
  signal: AbortSignal,
): Promise<EducationLevel> {
  return apiCall(
    () =>
      api.GET('/api/v1/education-levels/{level_id}', {
        params: { path: { level_id: levelId } },
        signal,
      }),
    { signal },
  );
}

/** One page of a level's subjects, active or not. */
export function fetchSubjects(
  levelId: string,
  page: number,
  signal: AbortSignal,
): Promise<SubjectPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/subjects', {
        params: {
          query: {
            education_level_id: levelId,
            status: 'all',
            limit: CATALOG_PAGE_SIZE,
            offset: offsetOf(page),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createLevel(
  body: EducationLevelCreate,
): Promise<EducationLevel> {
  return apiCall(() => api.POST('/api/v1/education-levels', { body }));
}

/** PATCH a level: `body` holds `version` and only the changed fields. */
export function updateLevel(
  levelId: string,
  body: EducationLevelUpdate,
): Promise<EducationLevel> {
  return apiCall(() =>
    api.PATCH('/api/v1/education-levels/{level_id}', {
      params: { path: { level_id: levelId } },
      body,
    }),
  );
}

export function createSubject(levelId: string, name: string): Promise<Subject> {
  return apiCall(() =>
    api.POST('/api/v1/education-levels/{level_id}/subjects', {
      params: { path: { level_id: levelId } },
      body: { name },
    }),
  );
}

/** PATCH a subject: `body` holds `version` and only the changed fields. */
export function updateSubject(
  subjectId: string,
  body: SubjectUpdate,
): Promise<Subject> {
  return apiCall(() =>
    api.PATCH('/api/v1/subjects/{subject_id}', {
      params: { path: { subject_id: subjectId } },
      body,
    }),
  );
}
