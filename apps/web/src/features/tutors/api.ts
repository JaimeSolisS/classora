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
