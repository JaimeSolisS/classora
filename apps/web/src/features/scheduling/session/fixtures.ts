import { CREATED, TUTORS, WARNINGS } from '../create/fixtures';
import type { SchedulingConflict } from '../create/api';
import type {
  AssignmentOfferCreated,
  AssignmentOfferItem,
  ClassSeriesDetail,
  ClassSessionDetail,
  SubstitutionCreated,
  SubstitutionItem,
} from './api';
import type { SessionCloseoutView } from '@/features/tutor-portal/closeout/api';
import {
  ATTENDANCE_UNMARKED,
  CHANGES_REQUESTED_RECORD,
  CORRECTION_SUBMITTED,
  ON_BEHALF,
  REVIEWABLE,
  STAFF_APPROVED,
  STAFF_CHANGES_REQUESTED,
  STAFF_SUBMITTED,
  WAITING,
} from '@/features/tutor-portal/closeout/fixtures';

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
  kind: 'assignment',
  substitution: null,
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
  ended: false,
  split_from_series_id: null,
  split_into_series_ids: [],
  assignment: null,
  group_id: null,
  cohort_id: null,
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
      cohort: null,
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

// Substitutions and cancellations (#55 H4): a confirmed session, a pending
// substitution and its history, the 201 of a new one, and a cancelled
// session.

export const CONFIRMED_SESSION: ClassSessionDetail = {
  ...DETAIL,
  state: 'confirmed',
  version: 5,
  pending_offer: null,
};

/** A confirmed session flagged for reassignment (#63 H3). */
export const FLAGGED_SESSION: ClassSessionDetail = {
  ...CONFIRMED_SESSION,
  reassignment: {
    reason: 'availability_change',
    tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
    flagged_at: '2026-10-22T16:00:00Z',
    change_request_id: '00000000-0000-4000-8000-0000000009c1',
  },
};

export const PENDING_SUBSTITUTION: SubstitutionItem = {
  id: '00000000-0000-4000-8000-0000000007a1',
  class_session_id: CREATED.id,
  status: 'pending',
  offer_id: '00000000-0000-4000-8000-0000000007b1',
  original_tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
  substitute_tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
  reason: 'Carmen está enferma.',
  requested_by: OWNER,
  requested_at: '2026-10-22T09:00:00Z',
  respond_by: '2026-10-24T09:00:00Z',
  responded_at: null,
  decline_note: null,
};

export const DECLINED_SUBSTITUTION: SubstitutionItem = {
  ...PENDING_SUBSTITUTION,
  id: '00000000-0000-4000-8000-0000000007a2',
  offer_id: '00000000-0000-4000-8000-0000000007b2',
  status: 'declined',
  requested_at: '2026-10-21T09:00:00Z',
  responded_at: '2026-10-21T11:00:00Z',
  decline_note: 'No puedo ese día.',
};

export const SUBSTITUTED_SESSION: ClassSessionDetail = {
  ...CONFIRMED_SESSION,
  pending_substitution: {
    id: PENDING_SUBSTITUTION.id,
    substitute: PENDING_SUBSTITUTION.substitute_tutor,
    respond_by: PENDING_SUBSTITUTION.respond_by,
  },
};

export const NEW_SUBSTITUTION: SubstitutionCreated = {
  substitution: PENDING_SUBSTITUTION,
  warnings: WARNINGS,
};

export const CANCELLED_SESSION: ClassSessionDetail = {
  ...CONFIRMED_SESSION,
  state: 'cancelled',
  version: 6,
  cancellation: {
    cancelled_at: '2026-10-22T10:00:00Z',
    reason: 'Cierre por mantenimiento.',
  },
};

// #312 A4/A5: a tutor's note left over from an earlier correction request,
// after a decision (the API keeps `tutor_note`; the section hides it).
const LEFTOVER_REVIEW = {
  last_submitted_at: CORRECTION_SUBMITTED.closeout!.review.last_submitted_at,
  tutor_note: CORRECTION_SUBMITTED.closeout!.review.tutor_note,
};

export const APPROVED_WITH_OLD_NOTE: SessionCloseoutView = {
  ...STAFF_APPROVED,
  closeout: {
    ...STAFF_APPROVED.closeout!,
    review: { ...STAFF_APPROVED.closeout!.review, ...LEFTOVER_REVIEW },
  },
};

export const CHANGES_REQUESTED_WITH_OLD_NOTE: SessionCloseoutView = {
  ...STAFF_CHANGES_REQUESTED,
  closeout: {
    ...STAFF_CHANGES_REQUESTED.closeout!,
    review: { ...STAFF_CHANGES_REQUESTED.closeout!.review, ...LEFTOVER_REVIEW },
  },
};

export const SUBMITTED_WITHOUT_NOTE: SessionCloseoutView = {
  ...CORRECTION_SUBMITTED,
  closeout: {
    ...CORRECTION_SUBMITTED.closeout!,
    review: { ...CORRECTION_SUBMITTED.closeout!.review, tutor_note: null },
  },
};

// --- #301b on behalf ------------------------------------------------------

/** A reviewer on an ended class without a closeout: "Close out on behalf". */
export const WAITING_CAN_SUBMIT_ON_BEHALF: SessionCloseoutView = {
  ...WAITING,
  can_submit_on_behalf: true,
};

/** The same with one of two students unmarked (Bruno Gómez). */
export const WAITING_INCOMPLETE: SessionCloseoutView = {
  ...WAITING_CAN_SUBMIT_ON_BEHALF,
  attendance: {
    total: 2,
    marked: 1,
    all_absent: false,
    missing_student_ids: [ATTENDANCE_UNMARKED.items[1]!.student.id],
  },
};

/** Everyone absent: the summary is optional. */
export const WAITING_ALL_ABSENT: SessionCloseoutView = {
  ...WAITING_CAN_SUBMIT_ON_BEHALF,
  attendance: {
    total: 2,
    marked: 2,
    all_absent: true,
    missing_student_ids: [],
  },
};

const ON_BEHALF_RECORD = {
  ...STAFF_SUBMITTED.closeout!,
  submitted_at: ON_BEHALF.submitted_at,
  review: {
    ...STAFF_SUBMITTED.closeout!.review,
    last_submitted_at: ON_BEHALF.submitted_at,
  },
  on_behalf: ON_BEHALF,
};

/** Another reviewer on an on-behalf submission: Approve and Correct only. */
export const ON_BEHALF_REVIEWER: SessionCloseoutView = {
  ...REVIEWABLE,
  can_request_changes: false,
  closeout: ON_BEHALF_RECORD,
};

/** Finance: the marker without the reason. */
export const ON_BEHALF_FINANCE: SessionCloseoutView = {
  ...STAFF_SUBMITTED,
  closeout: {
    ...ON_BEHALF_RECORD,
    summary: null,
    summary_hidden: true,
    on_behalf: { ...ON_BEHALF, reason: null },
  },
};

/**
 * A takeover's result while under review: the reviewer's old reason is
 * still in the read (unchanged from #78), and the section hides it.
 */
export const ON_BEHALF_AFTER_TAKEOVER: SessionCloseoutView = {
  ...ON_BEHALF_REVIEWER,
  closeout: {
    ...ON_BEHALF_RECORD,
    version: 3,
    review: {
      ...ON_BEHALF_RECORD.review,
      reason: CHANGES_REQUESTED_RECORD.review.reason,
    },
  },
};

/** A closeout sent back to a tutor who can't act: "Take over". */
export const CHANGES_REQUESTED_CAN_TAKE_OVER: SessionCloseoutView = {
  ...STAFF_CHANGES_REQUESTED,
  can_take_over: true,
};
