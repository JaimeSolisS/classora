import { CREATED, TUTORS, WARNINGS } from '../create/fixtures';
import type { SchedulingConflict } from '../create/api';
import type {
  AssignmentOfferCreated,
  AssignmentOfferItem,
  ClassSeriesDetail,
  ClassSessionDetail,
} from './api';

// The staff session page's sample data (#52 H4): an unassigned session
// after a decline, its offer history, and the offer a 201 returns.

// The create's 201 as a detail (its `warnings` member is left out).
const DETAIL: ClassSessionDetail = (() => {
  const detail: Partial<typeof CREATED> = { ...CREATED };
  delete detail.warnings;
  return detail as ClassSessionDetail;
})();

export const OWNER = {
  id: '00000000-0000-4000-8000-0000000004a9',
  display_name: 'Demo Owner',
};

export const UNASSIGNED_SESSION: ClassSessionDetail = {
  ...DETAIL,
  state: 'unassigned',
  tutor: null,
  version: 3,
  pending_offer: null,
};

export const OFFERED_SESSION: ClassSessionDetail = {
  ...DETAIL,
  state: 'offered',
  version: 4,
};

export const DECLINED: AssignmentOfferItem = {
  id: '00000000-0000-4000-8000-0000000006f1',
  scope: 'session',
  status: 'declined',
  tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
  offered_at: '2026-10-21T12:00:00Z',
  offered_by: OWNER,
  respond_by: '2026-10-23T12:00:00Z',
  responded_at: '2026-10-21T15:30:00Z',
  decline_note: 'Tengo otra clase a esa hora.',
  series: null,
  session_count: 1,
  sessions_truncated: false,
  sessions: [
    {
      id: CREATED.id,
      state: 'unassigned',
      starts_at: CREATED.starts_at,
      ends_at: CREATED.ends_at,
      subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
      classroom: { name: 'Aula 1' },
      participants: [{ display_name: 'Ana López' }],
    },
  ],
};

export const NEW_OFFER: AssignmentOfferCreated = {
  offer: {
    ...DECLINED,
    id: '00000000-0000-4000-8000-0000000006f2',
    status: 'pending',
    tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
    offered_at: '2026-10-22T09:00:00Z',
    respond_by: '2026-10-24T09:00:00Z',
    responded_at: null,
    decline_note: null,
  },
  warnings: WARNINGS,
};

// A weekly series (#233 G2): an unassigned occurrence of a tutorless
// series, the series, its pending and accepted assignments, a series offer
// and a series 409 (conflicts on two dates).

export const SERIES_ID = '00000000-0000-4000-8000-0000000006c1';

export const SERIES_ROW: ClassSessionDetail = {
  ...UNASSIGNED_SESSION,
  series_id: SERIES_ID,
  original_start: UNASSIGNED_SESSION.starts_at,
};

export const SERIES: ClassSeriesDetail = {
  id: SERIES_ID,
  kind: 'individual',
  subject: UNASSIGNED_SESSION.subject,
  tutor: null,
  classroom: UNASSIGNED_SESSION.classroom,
  participants: UNASSIGNED_SESSION.participants,
  starts_on: '2031-01-06',
  start_time: '10:00',
  duration_minutes: 60,
  time_zone: 'America/Mexico_City',
  recurrence: { weekdays: [1, 3], interval: 1, until: null, count: null },
  rrule: 'FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE',
  last_occurrence_on: null,
  assignment: null,
  version: 5,
  created_at: '2026-10-21T12:00:00Z',
  updated_at: '2026-10-21T12:00:00Z',
  occurrences: [],
};

export const PENDING_SERIES: ClassSeriesDetail = {
  ...SERIES,
  tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
  assignment: {
    status: 'pending',
    tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
    from_date: '2031-01-06',
    offer_id: '00000000-0000-4000-8000-0000000006f3',
    respond_by: '2031-01-04T09:00:00Z',
  },
};

export const ACCEPTED_SERIES: ClassSeriesDetail = {
  ...PENDING_SERIES,
  assignment: {
    ...PENDING_SERIES.assignment!,
    status: 'accepted',
    respond_by: null,
  },
};

export const NEW_SERIES_OFFER: AssignmentOfferCreated = {
  offer: {
    ...NEW_OFFER.offer,
    id: '00000000-0000-4000-8000-0000000006f3',
    scope: 'series',
    series: {
      id: SERIES_ID,
      kind: 'individual',
      subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
      weekdays: [1, 3],
      interval: 1,
      start_time: '10:00',
      duration_minutes: 60,
      time_zone: 'America/Mexico_City',
      from_date: '2031-01-06',
      until: null,
      count: null,
      last_occurrence_on: null,
    },
    session_count: 24,
    sessions_truncated: true,
  },
  warnings: [],
};

export const SERIES_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'tutor_overlap',
    severity: 'hard',
    details: {
      class_session_ids: ['00000000-0000-4000-8000-0000000006e9'],
      occurrence_start: '2031-01-15T16:00:00Z',
      date: '2031-01-15',
    },
  },
  {
    code: 'tutor_unavailable',
    severity: 'soft',
    details: { occurrence_start: '2031-01-13T16:00:00Z', date: '2031-01-13' },
  },
  {
    code: 'tutor_not_qualified',
    severity: 'soft',
    details: {
      subject_id: '00000000-0000-4000-8000-0000000006d1',
      occurrence_start: '2031-01-13T16:00:00Z',
      date: '2031-01-13',
    },
  },
];
