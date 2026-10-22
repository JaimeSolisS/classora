import { ANA, CLASSROOMS, SUBJECTS } from '../create/fixtures';
import type { SchedulingConflict } from '../create/api';
import type { ClassSeriesDetail, ClassSessionDetail } from '../session/api';
import type {
  ClassSeriesCancelled,
  ClassSeriesCreated,
  ClassSeriesEdited,
  SeriesOccurrence,
} from './api';

// Sample data for the series tests and stories (#54b E2, E3): a Monday and
// Wednesday series at 10:00-11:00 in Mexico City (UTC-6), 6 sessions from
// Monday 2031-01-06, with an edited, a skipped, a cancelled and a conflict
// occurrence; the same series ended, and split (both lineage links).

export const SERIES_ID = '00000000-0000-4000-8000-0000000007a1';
export const EARLIER_SERIES_ID = '00000000-0000-4000-8000-0000000007a0';
export const LATER_SERIES_ID = '00000000-0000-4000-8000-0000000007a2';

function row(
  n: number,
  date: string,
  state: SeriesOccurrence['state'],
  hours: [string, string] = ['16', '17'],
  isException = false,
): SeriesOccurrence {
  return {
    id: `00000000-0000-4000-8000-0000000007b${n}`,
    state,
    starts_at: `${date}T${hours[0]}:00:00Z`,
    ends_at: `${date}T${hours[1]}:00:00Z`,
    original_start: `${date}T16:00:00Z`,
    is_exception: isException,
  };
}

export const OCCURRENCES: SeriesOccurrence[] = [
  row(1, '2031-01-06', 'unassigned'),
  row(2, '2031-01-08', 'unassigned', ['18', '19'], true),
  row(3, '2031-01-13', 'skipped_closure'),
  row(4, '2031-01-15', 'cancelled'),
  row(5, '2031-01-20', 'conflict'),
  row(6, '2031-01-22', 'unassigned'),
];

export const SERIES_DETAIL: ClassSeriesDetail = {
  id: SERIES_ID,
  kind: 'individual',
  subject: {
    id: SUBJECTS[0]!.id,
    name: 'Álgebra',
    education_level_name: 'Secundaria',
    is_active: true,
  },
  tutor: null,
  classroom: { id: CLASSROOMS[0]!.id, name: 'Aula 1', is_active: true },
  participants: [
    { student_id: ANA.id, display_name: 'Ana López', is_active: true },
  ],
  starts_on: '2031-01-06',
  start_time: '10:00',
  duration_minutes: 60,
  time_zone: 'America/Mexico_City',
  recurrence: { weekdays: [1, 3], interval: 1, until: null, count: 6 },
  rrule: 'FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE;COUNT=6',
  last_occurrence_on: '2031-01-22',
  ended: false,
  split_from_series_id: null,
  split_into_series_ids: [],
  assignment: null,
  version: 4,
  created_at: '2026-10-21T12:00:00Z',
  updated_at: '2026-10-21T12:00:00Z',
  occurrences: OCCURRENCES,
};

export const ENDED_SERIES: ClassSeriesDetail = {
  ...SERIES_DETAIL,
  last_occurrence_on: '2026-10-01',
  ended: true,
  occurrences: [],
};

/** Cancelled before its first class: `ended` is still false (#54 A3). */
export const CANCELLED_SERIES: ClassSeriesDetail = {
  ...SERIES_DETAIL,
  recurrence: {
    weekdays: [1, 3],
    interval: 1,
    until: '2031-01-05',
    count: null,
  },
  last_occurrence_on: '2031-01-05',
  version: 5,
  occurrences: OCCURRENCES.map((o) =>
    o.state === 'skipped_closure' ? o : { ...o, state: 'cancelled' },
  ),
};

export const SPLIT_SERIES: ClassSeriesDetail = {
  ...SERIES_DETAIL,
  id: LATER_SERIES_ID,
  starts_on: '2031-01-13',
  start_time: '11:00',
  recurrence: {
    weekdays: [1, 3],
    interval: 1,
    until: '2031-01-22',
    count: null,
  },
  split_from_series_id: SERIES_ID,
  split_into_series_ids: [],
  version: 1,
  occurrences: OCCURRENCES.slice(2).map((o) => ({
    ...o,
    starts_at: o.starts_at.replace('T16:', 'T17:'),
    ends_at: o.ends_at.replace('T17:', 'T18:'),
    is_exception: false,
  })),
};

/** In the middle of a lineage: both links. */
export const LINEAGE_SERIES: ClassSeriesDetail = {
  ...SERIES_DETAIL,
  split_from_series_id: EARLIER_SERIES_ID,
  split_into_series_ids: [LATER_SERIES_ID],
};

export const SERIES_CREATED: ClassSeriesCreated = {
  ...SERIES_DETAIL,
  recurrence: {
    weekdays: [1, 3],
    interval: 2,
    until: '2031-03-31',
    count: null,
  },
  occurrences: [
    row(1, '2031-01-06', 'unassigned'),
    row(2, '2031-01-08', 'unassigned'),
    row(3, '2031-01-20', 'skipped_closure'),
  ],
  warnings: [],
};

/** A series 409: two dates, a hard and a soft conflict on the later one. */
export const SERIES_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'tutor_unavailable',
    severity: 'soft',
    details: {
      reasons: ['time_off'],
      occurrence_start: '2031-01-15T16:00:00Z',
      date: '2031-01-15',
    },
  },
  {
    code: 'student_overlap',
    severity: 'hard',
    details: {
      class_session_ids: ['00000000-0000-4000-8000-0000000007c1'],
      occurrence_start: '2031-01-15T16:00:00Z',
      date: '2031-01-15',
    },
  },
  {
    code: 'room_overlap',
    severity: 'hard',
    details: {
      class_session_ids: ['00000000-0000-4000-8000-0000000007c2'],
      occurrence_start: '2031-01-06T16:00:00Z',
      date: '2031-01-06',
    },
  },
];

/** The session of the first occurrence (its version and room). */
export const OCCURRENCE_SESSION: ClassSessionDetail = {
  id: OCCURRENCES[0]!.id,
  kind: 'individual',
  state: 'unassigned',
  subject: SERIES_DETAIL.subject,
  tutor: null,
  classroom: SERIES_DETAIL.classroom,
  participants: SERIES_DETAIL.participants,
  starts_at: OCCURRENCES[0]!.starts_at,
  ends_at: OCCURRENCES[0]!.ends_at,
  scheduled_minutes: 60,
  version: 2,
  created_at: '2026-10-21T12:00:00Z',
  updated_at: '2026-10-21T12:00:00Z',
  overrides: [],
  series_id: SERIES_ID,
  original_start: OCCURRENCES[0]!.original_start,
  is_exception: false,
  conflict_codes: [],
  pending_offer: null,
};

export const SERIES_EDITED: ClassSeriesEdited = {
  series: SPLIT_SERIES,
  previous_series_id: SERIES_ID,
  split: true,
  summary: {
    moved: 4,
    created: 6,
    cancelled: 2,
    skipped_closure: 0,
    exceptions_reset: 1,
  },
  warnings: [],
};

export const SERIES_CANCELLED: ClassSeriesCancelled = {
  series: CANCELLED_SERIES,
  summary: { cancelled: 4, skipped_closure_kept: 1 },
};
