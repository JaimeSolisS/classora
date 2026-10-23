import type { CalendarItem, CalendarPage, FilterOption } from './api';
import type { CalendarState } from './urlState';
import { NO_FILTERS } from './urlState';
import type { LocalDate } from '@/lib/time';

// Sample data for the calendar's tests and stories (#57 E5, E6). The week of
// Monday 2026-11-09 in America/Mexico_City (UTC-6, no DST) holds every state
// and both markers; the week of Monday 2026-10-26 in America/Tijuana holds
// the fall-back day (Sunday 2026-11-01: 01:00-02:00 happens twice).

export const ZONE = 'America/Mexico_City';
export const TIJUANA = 'America/Tijuana';
export const WEEK: LocalDate = '2026-11-09' as LocalDate;
export const DST_WEEK: LocalDate = '2026-10-26' as LocalDate;

const MATH = {
  id: '00000000-0000-4000-8000-000000057a01',
  name: 'Matemáticas',
  education_level_name: 'Secundaria',
};
const PHYSICS = {
  id: '00000000-0000-4000-8000-000000057a02',
  name: 'Física',
  education_level_name: 'Preparatoria',
};
export const CARMEN = {
  id: '00000000-0000-4000-8000-000000057b01',
  display_name: 'Carmen Ruiz',
};
export const ANA = {
  student_id: '00000000-0000-4000-8000-000000057c01',
  display_name: 'Ana López',
};
const LUIS = {
  student_id: '00000000-0000-4000-8000-000000057c02',
  display_name: 'Luis Pérez',
};
export const ROOM = {
  id: '00000000-0000-4000-8000-000000057d01',
  name: 'Aula 2',
};

let counter = 0;

/** A calendar item; times are instants (Mexico City is UTC-6). */
export function item(overrides: Partial<CalendarItem> = {}): CalendarItem {
  counter += 1;
  return {
    id: `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`,
    kind: 'individual',
    state: 'confirmed',
    version: 1,
    series_id: null,
    starts_at: '2026-11-09T16:00:00Z',
    ends_at: '2026-11-09T17:00:00Z',
    subject: MATH,
    tutor: CARMEN,
    classroom: ROOM,
    participants: [ANA],
    markers: [],
    ...overrides,
  };
}

/** One week with every state and both markers. */
export const WEEK_ITEMS: CalendarItem[] = [
  item({
    state: 'unassigned',
    tutor: null,
    starts_at: '2026-11-09T16:00:00Z',
    ends_at: '2026-11-09T17:00:00Z',
    markers: ['center_closed'],
  }),
  item({
    state: 'offered',
    starts_at: '2026-11-10T15:00:00Z',
    ends_at: '2026-11-10T16:00:00Z',
    markers: ['tutor_not_qualified'],
  }),
  item({
    state: 'confirmed',
    subject: PHYSICS,
    participants: [ANA, LUIS],
    kind: 'group',
    starts_at: '2026-11-10T18:00:00Z',
    ends_at: '2026-11-10T19:30:00Z',
  }),
  item({
    state: 'completed',
    starts_at: '2026-11-11T15:00:00Z',
    ends_at: '2026-11-11T16:00:00Z',
  }),
  item({
    state: 'cancelled',
    classroom: null,
    starts_at: '2026-11-11T18:00:00Z',
    ends_at: '2026-11-11T19:00:00Z',
  }),
  item({
    state: 'skipped_closure',
    starts_at: '2026-11-12T15:00:00Z',
    ends_at: '2026-11-12T16:00:00Z',
  }),
  item({
    state: 'conflict',
    starts_at: '2026-11-13T15:00:00Z',
    ends_at: '2026-11-13T16:00:00Z',
    markers: ['center_closed', 'tutor_not_qualified'],
  }),
];

/** The Tijuana fall-back day: 01:30 PDT (08:30Z) and 01:30 PST (09:30Z). */
export const DST_ITEMS: CalendarItem[] = [
  item({
    state: 'confirmed',
    starts_at: '2026-11-01T08:30:00Z',
    ends_at: '2026-11-01T09:00:00Z',
  }),
  item({
    state: 'offered',
    subject: PHYSICS,
    starts_at: '2026-11-01T09:30:00Z',
    ends_at: '2026-11-01T10:00:00Z',
  }),
];

export function page(items: CalendarItem[], truncated = false): CalendarPage {
  return { items, truncated };
}

export const TUTOR_OPTIONS: FilterOption[] = [
  { id: CARMEN.id, name: CARMEN.display_name, inactive: false },
  {
    id: '00000000-0000-4000-8000-000000057b02',
    name: 'Diego Torres',
    inactive: true,
  },
];

export const STUDENT_OPTIONS: FilterOption[] = [
  { id: ANA.student_id, name: ANA.display_name, inactive: false },
];

export const ROOM_OPTIONS: FilterOption[] = [
  { id: ROOM.id, name: ROOM.name, inactive: false },
  {
    id: '00000000-0000-4000-8000-000000057d02',
    name: 'Aula 9',
    inactive: true,
  },
];

export function calendarState(
  overrides: Partial<CalendarState> = {},
): CalendarState {
  return { view: 'week', date: WEEK, filters: NO_FILTERS, ...overrides };
}

/** Short sessions (15, 30 and 60 minutes) with both markers (#57 QA D1). */
export const SHORT_ITEMS: CalendarItem[] = [15, 30, 60].map((minutes, index) =>
  item({
    state: (['unassigned', 'offered', 'confirmed'] as const)[index],
    tutor: index === 0 ? null : CARMEN,
    starts_at: `2026-11-1${index}T16:00:00Z`,
    ends_at: new Date(
      Date.parse(`2026-11-1${index}T16:00:00Z`) + minutes * 60_000,
    ).toISOString(),
    markers: ['center_closed', 'tutor_not_qualified'],
  }),
);
