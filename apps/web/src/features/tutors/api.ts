import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The tutor profile calls (#43). The list and the detail are queries; the
// profile change is a direct call whose answer (the updated detail) replaces
// the detail in the cache.

export type TutorListItem = components['schemas']['TutorListItem'];
export type TutorListPage = components['schemas']['OffsetPage_TutorListItem_'];
export type TutorDetail = components['schemas']['TutorDetail'];
export type TutorUpdate = components['schemas']['TutorUpdate'];
export type TutorField = components['schemas']['TutorField'];
export type UserState = components['schemas']['UserState'];

/** Rows per page of the tutor list (the API default). */
export const TUTORS_PAGE_SIZE = 25;

/** Every cached tutor query (list and detail). */
export const TUTORS_QUERY_KEY = ['tutors'] as const;
/** Every cached list page: invalidated after any change. */
export const TUTORS_LIST_QUERY_KEY = [...TUTORS_QUERY_KEY, 'list'] as const;

export interface TutorListParams {
  q: string;
  state: UserState | '';
  /** 1-based page number. */
  page: number;
}

export function tutorsListKey(params: TutorListParams) {
  return [...TUTORS_LIST_QUERY_KEY, params] as const;
}

export function tutorDetailKey(userId: string) {
  return [...TUTORS_QUERY_KEY, 'detail', userId] as const;
}

export function fetchTutors(
  { q, state, page }: TutorListParams,
  signal: AbortSignal,
): Promise<TutorListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/tutors', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(state ? { state } : {}),
            limit: TUTORS_PAGE_SIZE,
            offset: (page - 1) * TUTORS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchTutor(
  userId: string,
  signal: AbortSignal,
): Promise<TutorDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/tutors/{user_id}', {
        params: { path: { user_id: userId } },
        signal,
      }),
    { signal },
  );
}

/** PATCH a profile: `body` holds `version` and only the changed fields. */
export function updateTutor(
  userId: string,
  body: TutorUpdate,
): Promise<TutorDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/tutors/{user_id}', {
      params: { path: { user_id: userId } },
      body,
    }),
  );
}

// --- Tutor qualifications (#44) ---------------------------------------------

export type QualificationItem = components['schemas']['QualificationItem'];
export type QualificationStatus = components['schemas']['QualificationStatus'];
export type TeachingSubject = components['schemas']['TeachingSubject'];

/** An active level or subject offered in the request dialog. */
export interface CatalogOption {
  id: string;
  name: string;
}

export function qualificationsKey(userId: string) {
  return [...TUTORS_QUERY_KEY, 'qualifications', userId] as const;
}

/** The active levels of the request dialog (catalog queries share the
 * `catalog` prefix, so a catalog change refreshes them). */
export const ACTIVE_LEVEL_OPTIONS_KEY = [
  'catalog',
  'education-levels',
  'qualification-options',
] as const;

export function activeSubjectOptionsKey(levelId: string) {
  return ['catalog', 'subjects', 'qualification-options', levelId] as const;
}

export function fetchQualifications(
  userId: string,
  signal: AbortSignal,
): Promise<QualificationItem[]> {
  return apiCall(
    () =>
      api.GET('/api/v1/tutors/{user_id}/qualifications', {
        params: { path: { user_id: userId } },
        signal,
      }),
    { signal },
  );
}

export function requestQualification(
  userId: string,
  subjectId: string,
): Promise<QualificationItem> {
  return apiCall(() =>
    api.POST('/api/v1/tutors/{user_id}/qualifications', {
      params: { path: { user_id: userId } },
      body: { subject_id: subjectId },
    }),
  );
}

export function cancelQualification(
  userId: string,
  qualificationId: string,
): Promise<QualificationItem> {
  return apiCall(() =>
    api.POST(
      '/api/v1/tutors/{user_id}/qualifications/{qualification_id}/cancel',
      {
        params: {
          path: { user_id: userId, qualification_id: qualificationId },
        },
      },
    ),
  );
}

/** The active education levels (one page of up to 100, by position). */
export async function fetchActiveLevelOptions(
  signal: AbortSignal,
): Promise<CatalogOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/education-levels', {
        params: { query: { status: 'active', limit: 100 } },
        signal,
      }),
    { signal },
  );
  return page.items.map(({ id, name }) => ({ id, name }));
}

/** The active subjects of one level (up to 100, by name). */
export async function fetchActiveSubjectOptions(
  levelId: string,
  signal: AbortSignal,
): Promise<CatalogOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/subjects', {
        params: {
          query: { education_level_id: levelId, status: 'active', limit: 100 },
        },
        signal,
      }),
    { signal },
  );
  // The API already filters; a stale or mixed answer never offers another
  // level's or an inactive subject.
  return page.items
    .filter((item) => item.is_active && item.education_level_id === levelId)
    .map(({ id, name }) => ({ id, name }));
}

// --- Tutor availability (#45) -----------------------------------------------

export type TutorAvailability =
  components['schemas']['TutorAvailabilityDetail'];
export type AvailabilityException =
  components['schemas']['AvailabilityExceptionItem'];
export type AvailabilityExceptionCreate =
  components['schemas']['AvailabilityExceptionCreate'];
export type ExceptionKind = components['schemas']['ExceptionKind'];
export type WeeklyAvailabilityUpdate =
  components['schemas']['WeeklyAvailabilityUpdate'];

export function availabilityKey(userId: string) {
  return [...TUTORS_QUERY_KEY, 'availability', userId] as const;
}

export function fetchAvailability(
  userId: string,
  signal: AbortSignal,
): Promise<TutorAvailability> {
  return apiCall(
    () =>
      api.GET('/api/v1/tutors/{user_id}/availability', {
        params: { path: { user_id: userId } },
        signal,
      }),
    { signal },
  );
}

/** PUT the full list of weekly windows with the loaded `version`. */
export function replaceWeeklyAvailability(
  userId: string,
  body: WeeklyAvailabilityUpdate,
): Promise<TutorAvailability> {
  return apiCall(() =>
    api.PUT('/api/v1/tutors/{user_id}/availability/weekly', {
      params: { path: { user_id: userId } },
      body,
    }),
  );
}

export function createAvailabilityException(
  userId: string,
  body: AvailabilityExceptionCreate,
): Promise<AvailabilityException> {
  return apiCall(() =>
    api.POST('/api/v1/tutors/{user_id}/availability/exceptions', {
      params: { path: { user_id: userId } },
      body,
    }),
  );
}

export async function deleteAvailabilityException(
  userId: string,
  exceptionId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE(
      '/api/v1/tutors/{user_id}/availability/exceptions/{exception_id}',
      {
        params: { path: { user_id: userId, exception_id: exceptionId } },
      },
    ),
  );
}
