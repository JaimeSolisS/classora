import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { Instant } from '@/lib/time';
import type { FilterQuery } from './urlState';

// The calls of the calendar (#57): the range read, the tutor filter options
// (schedule.view, so Finance can use it, #57 E2), the student search and
// the classrooms.

export type CalendarItem = components['schemas']['ClassSessionCalendarItem'];
export type CalendarPage = components['schemas']['ClassSessionCalendar'];
export type SessionState = CalendarItem['state'];
export type MarkerCode = CalendarItem['markers'][number];

export const SESSION_STATES: readonly SessionState[] = [
  'unassigned',
  'offered',
  'confirmed',
  'completed',
  'cancelled',
  'skipped_closure',
  'conflict',
];

export const MARKER_CODES: readonly MarkerCode[] = [
  'center_closed',
  'tutor_not_qualified',
];

/** A filter option: a tutor, a student or a room. */
export interface FilterOption {
  id: string;
  name: string;
  /** Inactive (a suspended or archived tutor, an inactive room or student). */
  inactive: boolean;
}

export interface CalendarQuery extends FilterQuery {
  from: Instant;
  to: Instant;
}

export const CALENDAR_KEY = ['scheduling', 'calendar'] as const;

/** One query per (range, filters): the key holds both (#57 D5). */
export function calendarQueryKey(query: CalendarQuery) {
  return [...CALENDAR_KEY, 'sessions', query] as const;
}

export function fetchCalendar(
  query: CalendarQuery,
  signal: AbortSignal,
): Promise<CalendarPage> {
  return apiCall(
    () => api.GET('/api/v1/class-sessions', { params: { query }, signal }),
    { signal },
  );
}

/** How many options a filter search shows. */
export const FILTER_SEARCH_LIMIT = 10;
/** The most classrooms the room filter loads (one page). */
export const ROOMS_LIMIT = 100;

export async function searchTutorOptions(
  q: string,
  signal: AbortSignal,
): Promise<FilterOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/schedule/tutor-options', {
        params: { query: { q, limit: FILTER_SEARCH_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((tutor) => ({
    id: tutor.id,
    name: tutor.display_name,
    inactive: tutor.state !== 'active',
  }));
}

export async function searchStudentOptions(
  q: string,
  signal: AbortSignal,
): Promise<FilterOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: { query: { q, status: 'all', limit: FILTER_SEARCH_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((student) => ({
    id: student.id,
    name: `${student.given_names} ${student.family_names}`,
    inactive: !student.is_active,
  }));
}

export async function fetchRoomOptions(
  signal: AbortSignal,
): Promise<FilterOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/classrooms', {
        params: { query: { status: 'all', limit: ROOMS_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((room) => ({
    id: room.id,
    name: room.name,
    inactive: !room.is_active,
  }));
}

/**
 * The series of one session (#54b D2): the event dialog's "Open series"
 * link. The range item has no `series_id`, so the dialog reads the
 * session (schedule.view, like the calendar).
 */
export async function fetchSessionSeriesId(
  sessionId: string,
  signal?: AbortSignal,
): Promise<string | null> {
  const session = await apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}', {
        params: { path: { class_session_id: sessionId } },
        signal,
      }),
    { signal },
  );
  return session.series_id;
}

/** The loaders the calendar needs (stubs in stories and tests). */
export interface CalendarLoaders {
  fetchCalendar: typeof fetchCalendar;
  searchTutors: typeof searchTutorOptions;
  searchStudents: typeof searchStudentOptions;
  fetchRooms: typeof fetchRoomOptions;
  /** none: the event dialog has no "Open series" link. */
  fetchSeriesId?: typeof fetchSessionSeriesId;
}

export const API_LOADERS: CalendarLoaders = {
  fetchCalendar,
  searchTutors: searchTutorOptions,
  searchStudents: searchStudentOptions,
  fetchRooms: fetchRoomOptions,
  fetchSeriesId: fetchSessionSeriesId,
};
