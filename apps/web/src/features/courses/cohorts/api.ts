import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The calls of the cohort pages (#243, the #65 API): the list (cursor
// pagination), one cohort, its sessions in a range, the create and the
// rename / new maximum. Plus the course templates the pickers offer (one
// page of the templates list: the active ones for the create form, every
// one for the list's filter).

export type CohortListItem = components['schemas']['CohortListItem'];
export type CohortPage = components['schemas']['CohortPage'];
export type CohortDetail = components['schemas']['CohortDetail'];
export type CohortCreate = components['schemas']['CohortCreate'];
export type CohortCreated = components['schemas']['CohortCreated'];
export type CohortUpdate = components['schemas']['CohortUpdate'];
export type CohortStatus = components['schemas']['CohortStatus'];
export type CohortStatusFilter = components['schemas']['CohortStatusFilter'];
export type CohortSessionItem = components['schemas']['CohortSessionItem'];
export type CohortSessions = components['schemas']['CohortSessions'];
export type CohortOccurrenceItem =
  components['schemas']['CohortOccurrenceItem'];
export type CohortSyllabus = components['schemas']['CohortSyllabus'];
export type CohortSeries = components['schemas']['ClassSeriesSummary'];
export type CohortOverride = components['schemas']['SeriesOverride'];
export type TemplateOption = components['schemas']['CourseTemplateSummary'];

/** The list's filters (the URL holds them, B1). */
export interface CohortListFilters {
  q: string;
  /** A template id, or '' for every template. */
  template: string;
  /** A subject id, or '' for every subject. */
  subject: string;
  status: CohortStatusFilter;
}

/** One page of the list. */
export const COHORTS_PAGE_SIZE = 25;

export const COHORTS_KEY = ['courses', 'cohorts'] as const;

export function cohortsListKey(filters: CohortListFilters) {
  return [...COHORTS_KEY, 'list', filters] as const;
}

export function cohortKey(id: string) {
  return [...COHORTS_KEY, 'detail', id] as const;
}

export function cohortSessionsKey(id: string, from: string, to: string) {
  return [...COHORTS_KEY, 'sessions', id, from, to] as const;
}

export function templateOptionsKey(status: 'active' | 'all') {
  return [...COHORTS_KEY, 'template-options', status] as const;
}

export function fetchCohorts(
  filters: CohortListFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<CohortPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/cohorts', {
        params: {
          query: {
            ...(filters.q ? { q: filters.q } : {}),
            ...(filters.template ? { template_id: filters.template } : {}),
            ...(filters.subject ? { subject_id: filters.subject } : {}),
            status: filters.status,
            limit: COHORTS_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchCohort(
  id: string,
  signal?: AbortSignal,
): Promise<CohortDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/cohorts/{cohort_id}', {
        params: { path: { cohort_id: id } },
        signal,
      }),
    { signal },
  );
}

/** The cohort's sessions overlapping [from, to) (instants, at most 45 days). */
export function fetchCohortSessions(
  id: string,
  from: string,
  to: string,
  signal?: AbortSignal,
): Promise<CohortSessions> {
  return apiCall(
    () =>
      api.GET('/api/v1/cohorts/{cohort_id}/sessions', {
        params: { path: { cohort_id: id }, query: { from, to } },
        signal,
      }),
    { signal },
  );
}

export function createCohort(body: CohortCreate): Promise<CohortCreated> {
  return apiCall(() => api.POST('/api/v1/cohorts', { body }));
}

export function updateCohort(
  id: string,
  body: CohortUpdate,
): Promise<CohortDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/cohorts/{cohort_id}', {
      params: { path: { cohort_id: id } },
      body,
    }),
  );
}

/** One page (up to 100) of course templates: the active ones, or all. */
export async function fetchTemplateOptions(
  status: 'active' | 'all',
  signal: AbortSignal,
): Promise<TemplateOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/course-templates', {
        params: { query: { status, limit: 100, offset: 0 } },
        signal,
      }),
    { signal },
  );
  return status === 'active'
    ? page.items.filter((template) => template.is_active)
    : page.items;
}
