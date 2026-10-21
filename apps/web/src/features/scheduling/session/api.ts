import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { TutorSuggestionList } from '../create/api';

// The calls of the staff session page (#52 H2): the session, its offer
// history, the replacement tutors (#51) and the offer.

export type ClassSessionDetail = components['schemas']['ClassSessionDetail'];
export type AssignmentOfferItem = components['schemas']['AssignmentOfferItem'];
export type AssignmentOfferCreate =
  components['schemas']['AssignmentOfferCreate'];
export type AssignmentOfferCreated =
  components['schemas']['AssignmentOfferCreated'];
export type ClassSessionOffers = components['schemas']['ClassSessionOffers'];

export const SESSION_KEY = ['scheduling', 'session'] as const;

export function sessionKey(id: string) {
  return [...SESSION_KEY, id] as const;
}

export function fetchClassSession(
  id: string,
  signal?: AbortSignal,
): Promise<ClassSessionDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}', {
        params: { path: { class_session_id: id } },
        signal,
      }),
    { signal },
  );
}

export function fetchSessionOffers(
  id: string,
  signal?: AbortSignal,
): Promise<ClassSessionOffers> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}/offers', {
        params: { path: { class_session_id: id } },
        signal,
      }),
    { signal },
  );
}

/** The eligible replacement tutors for the session's own slot (#51 E). */
export function fetchReplacementTutors(
  id: string,
): Promise<TutorSuggestionList> {
  return apiCall(() =>
    api.GET('/api/v1/class-sessions/{class_session_id}/replacement-tutors', {
      params: { path: { class_session_id: id } },
    }),
  );
}

export function offerClassSession(
  id: string,
  body: AssignmentOfferCreate,
): Promise<AssignmentOfferCreated> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/offers', {
      params: { path: { class_session_id: id } },
      body,
    }),
  );
}

// The series of an occurrence (#233 G2): its assignment, and the offer of
// the series from a date.

export type ClassSeriesDetail = components['schemas']['ClassSeriesDetail'];
export type ClassSeriesOfferCreate =
  components['schemas']['ClassSeriesOfferCreate'];

export function seriesKey(id: string) {
  return ['scheduling', 'series', id] as const;
}

export function fetchClassSeries(
  id: string,
  signal?: AbortSignal,
): Promise<ClassSeriesDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-series/{class_series_id}', {
        params: { path: { class_series_id: id } },
        signal,
      }),
    { signal },
  );
}

export function offerClassSeries(
  id: string,
  body: ClassSeriesOfferCreate,
): Promise<AssignmentOfferCreated> {
  return apiCall(() =>
    api.POST('/api/v1/class-series/{class_series_id}/offers', {
      params: { path: { class_series_id: id } },
      body,
    }),
  );
}
