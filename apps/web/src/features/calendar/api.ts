import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { OptionsPage } from '@/components/ui/combobox';
import { fetchStudentOptions } from '@/features/students/api';
import type { Instant } from '@/lib/time';
import type { FilterQuery } from './urlState';

// The calls of the calendar (#57): the range read, the tutor filter options
// (schedule.view, so Finance can use it, #57 E2), the student search and
// the classrooms; and the move of one session (#58, PATCH).

export type CalendarItem = components['schemas']['ClassSessionCalendarItem'];
export type ClassSessionUpdate = components['schemas']['ClassSessionUpdate'];
export type ClassSessionUpdated = components['schemas']['ClassSessionUpdated'];
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
  'overridden',
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

/** The tutor options a picker loads at once (the endpoint's maximum). */
export const TUTOR_OPTIONS_PAGE_SIZE = 50;
/** The most classrooms the room filter loads (one page). */
export const ROOMS_LIMIT = 100;

/**
 * The tutor pickers' options (#548): every tutor-role user, any state
 * (inactive marked), or the matches for `q` (blank `q` is never sent).
 */
export async function searchTutorOptions(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<FilterOption>> {
  const text = q?.trim() ?? '';
  const page = await apiCall(
    () =>
      api.GET('/api/v1/schedule/tutor-options', {
        params: {
          query: {
            ...(text ? { q: text } : {}),
            limit: TUTOR_OPTIONS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
  return {
    items: page.items.map((tutor) => ({
      id: tutor.id,
      name: tutor.display_name,
      inactive: tutor.state !== 'active',
    })),
    total: page.total,
  };
}

/** The student filter's options (#545), any status, inactive marked. */
export async function searchStudentOptions(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<FilterOption>> {
  const page = await fetchStudentOptions({ q, status: 'all', signal });
  return {
    items: page.items.map((student) => ({
      id: student.id,
      name: `${student.given_names} ${student.family_names}`,
      inactive: !student.is_active,
    })),
    total: page.total,
  };
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

/** Move one session (#58): the drop, the resize and the Reschedule dialog. */
export function updateSession(
  sessionId: string,
  body: ClassSessionUpdate,
): Promise<ClassSessionUpdated> {
  return apiCall(() =>
    api.PATCH('/api/v1/class-sessions/{class_session_id}', {
      params: { path: { class_session_id: sessionId } },
      body,
    }),
  );
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
  /** Moves a session (#58); none: the calendar is read-only. */
  updateSession?: typeof updateSession;
}

export const API_LOADERS: CalendarLoaders = {
  fetchCalendar,
  searchTutors: searchTutorOptions,
  searchStudents: searchStudentOptions,
  fetchRooms: fetchRoomOptions,
  fetchSeriesId: fetchSessionSeriesId,
  updateSession,
};
