import {
  addDays,
  addMonths,
  localDateTimeToInstant,
  parseLocalDate,
  type Instant,
  type LocalDate,
} from '@/lib/time';
import { isCalendarView, type CalendarView } from './views';

// The calendar's state in the URL (#57 D3): view, date (a LocalDate in the
// center zone) and filters live in the search params, so reload,
// back/forward and shared links restore the same calendar. Invalid values
// fall back to the defaults without an error. Pure: no zone maths here
// (lib/time does it).

export const SESSION_KINDS = ['individual', 'group', 'course'] as const;
export type SessionKindFilter = (typeof SESSION_KINDS)[number];

export interface CalendarFilters {
  tutor: string | null;
  student: string | null;
  room: string | null;
  kind: SessionKindFilter | null;
  cancelled: boolean;
}

export interface CalendarState {
  view: CalendarView;
  date: LocalDate;
  filters: CalendarFilters;
}

export const NO_FILTERS: CalendarFilters = {
  tutor: null,
  student: null,
  room: null,
  kind: null,
  cancelled: false,
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function idParam(value: string | null): string | null {
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function dateParam(value: string | null): LocalDate | null {
  if (value === null) {
    return null;
  }
  try {
    return parseLocalDate(value);
  } catch {
    return null;
  }
}

function kindParam(value: string | null): SessionKindFilter | null {
  return SESSION_KINDS.find((kind) => kind === value) ?? null;
}

/** The state in `params`; anything missing or invalid takes its default. */
export function parseCalendarState(
  params: URLSearchParams,
  defaults: { view: CalendarView; date: LocalDate },
): CalendarState {
  const view = params.get('view');
  return {
    view: isCalendarView(view) ? view : defaults.view,
    date: dateParam(params.get('date')) ?? defaults.date,
    filters: {
      tutor: idParam(params.get('tutor')),
      student: idParam(params.get('student')),
      room: idParam(params.get('room')),
      kind: kindParam(params.get('kind')),
      cancelled: params.get('cancelled') === '1',
    },
  };
}

/** The search params of a state (filters only when set). */
export function calendarSearchParams(state: CalendarState): URLSearchParams {
  const params = new URLSearchParams({ view: state.view, date: state.date });
  const { tutor, student, room, kind, cancelled } = state.filters;
  if (tutor) params.set('tutor', tutor);
  if (student) params.set('student', student);
  if (room) params.set('room', room);
  if (kind) params.set('kind', kind);
  if (cancelled) params.set('cancelled', '1');
  return params;
}

export function hasFilters(filters: CalendarFilters): boolean {
  return (
    filters.tutor !== null ||
    filters.student !== null ||
    filters.room !== null ||
    filters.kind !== null ||
    filters.cancelled
  );
}

/** The filter part of the range read's query (#57 A1). */
export interface FilterQuery {
  tutor_user_id?: string;
  student_id?: string;
  classroom_id?: string;
  kind?: SessionKindFilter;
  include_cancelled?: boolean;
}

export function filterQuery(filters: CalendarFilters): FilterQuery {
  const query: FilterQuery = {};
  if (filters.tutor) query.tutor_user_id = filters.tutor;
  if (filters.student) query.student_id = filters.student;
  if (filters.room) query.classroom_id = filters.room;
  if (filters.kind) query.kind = filters.kind;
  if (filters.cancelled) query.include_cancelled = true;
  return query;
}

/** The visible local dates; `end` is exclusive. */
export interface LocalRange {
  start: LocalDate;
  end: LocalDate;
}

/**
 * The `from`/`to` instants of a visible range in the center zone: the
 * start of its first day and of the day after its last (so 23- and 25-hour
 * days come out right).
 */
export function rangeInstants(
  range: LocalRange,
  timeZone: string,
): { from: Instant; to: Instant } {
  return {
    from: localDateTimeToInstant(range.start, '00:00', timeZone),
    to: localDateTimeToInstant(range.end, '00:00', timeZone),
  };
}

/** The date shown after Previous (-1) or Next (+1) in `view`. */
export function shiftDate(
  view: CalendarView,
  date: LocalDate,
  step: -1 | 1,
): LocalDate {
  switch (view) {
    case 'day':
      return addDays(date, step);
    case 'week':
    case 'list':
      return addDays(date, 7 * step);
    case 'month':
      return addMonths(date, step);
  }
}
