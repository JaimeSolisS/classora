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

// --- Academic periods and closures (#38) ---------------------------------

export type AcademicPeriod = components['schemas']['AcademicPeriodDetail'];
export type AcademicPeriodPage =
  components['schemas']['OffsetPage_AcademicPeriodDetail_'];
export type AcademicPeriodCreate =
  components['schemas']['AcademicPeriodCreate'];
export type AcademicPeriodUpdate =
  components['schemas']['AcademicPeriodUpdate'];
export type PeriodType = components['schemas']['PeriodType'];
export type Closure = components['schemas']['ClosureDetail'];
export type ClosurePage = components['schemas']['OffsetPage_ClosureDetail_'];
export type ClosureCreate = components['schemas']['ClosureCreate'];
export type ClosureUpdate = components['schemas']['ClosureUpdate'];
export type ClosureType = components['schemas']['ClosureType'];

/** Every cached period list. */
export const PERIODS_QUERY_KEY = [...CATALOG_QUERY_KEY, 'periods'] as const;
/** Every cached closure list. */
export const CLOSURES_QUERY_KEY = [...CATALOG_QUERY_KEY, 'closures'] as const;

export function periodsListKey(page: number) {
  return [...PERIODS_QUERY_KEY, 'list', page] as const;
}

export function closuresListKey(year: number, page: number) {
  return [...CLOSURES_QUERY_KEY, 'list', year, page] as const;
}

/** One page of every period, active or not (the managers' view). */
export function fetchPeriods(
  page: number,
  signal: AbortSignal,
): Promise<AcademicPeriodPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/academic-periods', {
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

export function createPeriod(
  body: AcademicPeriodCreate,
): Promise<AcademicPeriod> {
  return apiCall(() => api.POST('/api/v1/academic-periods', { body }));
}

/** PATCH a period: `body` holds `version` and only the changed fields. */
export function updatePeriod(
  periodId: string,
  body: AcademicPeriodUpdate,
): Promise<AcademicPeriod> {
  return apiCall(() =>
    api.PATCH('/api/v1/academic-periods/{period_id}', {
      params: { path: { period_id: periodId } },
      body,
    }),
  );
}

/**
 * One page of the closures, active or not, that overlap `[from, to]`
 * (inclusive local dates).
 */
export function fetchClosures(
  range: { from: string; to: string },
  page: number,
  signal: AbortSignal,
): Promise<ClosurePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/closures', {
        params: {
          query: {
            status: 'all',
            from: range.from,
            to: range.to,
            limit: CATALOG_PAGE_SIZE,
            offset: offsetOf(page),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createClosure(body: ClosureCreate): Promise<Closure> {
  return apiCall(() => api.POST('/api/v1/closures', { body }));
}

/** PATCH a closure: `body` holds `version` and only the changed fields. */
export function updateClosure(
  closureId: string,
  body: ClosureUpdate,
): Promise<Closure> {
  return apiCall(() =>
    api.PATCH('/api/v1/closures/{closure_id}', {
      params: { path: { closure_id: closureId } },
      body,
    }),
  );
}
