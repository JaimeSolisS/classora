import type { ApprovalRequestItem } from './api';

// Approval items for stories (#40 F6): one per status, two types.

export const PENDING_REGISTRATION: ApprovalRequestItem = {
  id: '00000000-0000-4000-8000-0000000000c1',
  request_type: 'identity.registration',
  status: 'pending',
  priority: 'normal',
  requester: {
    id: '00000000-0000-4000-8000-0000000000a1',
    display_name: 'Rosa Díaz',
  },
  assignee: null,
  created_at: '2026-10-19T15:00:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    username: 'rosa.diaz',
    display_name: 'Rosa Díaz',
    requested_role: 'guardian',
  },
  allowed_actions: ['approve', 'reject'],
  version: 1,
};

const OWNER = {
  id: '00000000-0000-4000-8000-0000000000a9',
  display_name: 'Demo Owner',
};

export const MIXED_ITEMS: ApprovalRequestItem[] = [
  { ...PENDING_REGISTRATION, priority: 'high' },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c2',
    status: 'approved',
    requester: {
      id: '00000000-0000-4000-8000-0000000000a2',
      display_name: 'Luis S',
    },
    subject: {
      username: 'luis.s',
      display_name: 'Luis S',
      requested_role: 'student',
    },
    resolved_at: '2026-10-19T17:00:00Z',
    resolved_by: OWNER,
    allowed_actions: [],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c3',
    status: 'rejected',
    priority: 'low',
    subject: {
      username: 'ana.p',
      display_name: 'Ana P',
      requested_role: 'guardian',
    },
    resolved_at: '2026-10-19T17:05:00Z',
    resolved_by: OWNER,
    reason: 'Unknown family.',
    allowed_actions: [],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c4',
    request_type: 'test.sample',
    status: 'changes_requested',
    requester: null,
    subject: {},
    reason: 'Add the date.',
    allowed_actions: ['reject'],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c5',
    status: 'cancelled',
    subject: {
      username: 'mia.r',
      display_name: 'Mía R',
      requested_role: 'student',
    },
    resolved_at: '2026-10-19T18:00:00Z',
    allowed_actions: [],
    version: 2,
  },
];

/** A pending availability change of a tutor (#63 H2): time off over three
 * classes; the summary carries the first 20 of the snapshot. */
export const AVAILABILITY_CHANGE: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-0000000000e1',
  request_type: 'staff.availability_change',
  priority: 'high',
  requester: {
    id: '00000000-0000-4000-8000-0000000000b1',
    display_name: 'Carmen Ruiz',
  },
  subject: {
    tutor: {
      id: '00000000-0000-4000-8000-0000000000b1',
      display_name: 'Carmen Ruiz',
    },
    kind: 'exception_create',
    change: {
      windows: null,
      exception: {
        id: null,
        kind: 'unavailable',
        starts_on: '2031-11-12',
        ends_on: '2031-11-16',
        starts_at: null,
        ends_at: null,
        note: null,
      },
    },
    affected_count: 3,
    affected_sessions: [
      {
        id: '00000000-0000-4000-8000-0000000000e2',
        starts_at: '2031-11-12T22:00:00Z',
        ends_at: '2031-11-12T23:00:00Z',
        subject_name: 'Física',
        state: 'confirmed',
      },
      {
        id: '00000000-0000-4000-8000-0000000000e3',
        starts_at: '2031-11-13T22:00:00Z',
        ends_at: '2031-11-13T23:00:00Z',
        subject_name: 'Química',
        state: 'offered',
      },
    ],
    change_request_status: 'pending',
  },
};

/** A tutor's session closeout (#77 G4, #78 G2): reviewed on the session
 * page, so no inbox decisions. */
export const SESSION_CLOSEOUT: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-0000000000f1',
  request_type: 'academics.session_closeout',
  requester: {
    id: '00000000-0000-4000-8000-0000000000b1',
    display_name: 'Carmen Ruiz',
  },
  subject: {
    class_session_id: '00000000-0000-4000-8000-0000000000f2',
    // 10:00 in Mexico City.
    starts_at: '2031-04-07T16:00:00Z',
    subject_name: 'Matemáticas',
    tutor_display_name: 'Carmen Ruiz',
    kind: 'group',
    scheduled_minutes: 60,
    actual_minutes: 55,
    participant_count: 3,
    kind_of_review: 'first',
    approved_minutes: null,
  },
  allowed_actions: [],
};

/** #78 G2: a tutor's correction request of an approved closeout. */
export const SESSION_CLOSEOUT_CORRECTION: ApprovalRequestItem = {
  ...SESSION_CLOSEOUT,
  id: '00000000-0000-4000-8000-0000000000f3',
  subject: {
    ...(SESSION_CLOSEOUT.subject as Record<string, unknown>),
    actual_minutes: 50,
    kind_of_review: 'correction',
    approved_minutes: 55,
  },
};

/** #301b E4: a closeout a reviewer submitted on the tutor's behalf. */
export const SESSION_CLOSEOUT_ON_BEHALF: ApprovalRequestItem = {
  ...SESSION_CLOSEOUT,
  id: '00000000-0000-4000-8000-0000000000f4',
  subject: {
    ...(SESSION_CLOSEOUT.subject as Record<string, unknown>),
    on_behalf_by_display_name: 'Laura Méndez',
  },
};

// #83 H2, #315 A5: a progress report waiting for review (with AM1's
// report_id).
export const PROGRESS_REPORT: ApprovalRequestItem = {
  id: '00000000-0000-4000-8000-000000000831',
  request_type: 'academics.progress_report',
  status: 'pending',
  priority: 'normal',
  requester: {
    id: '00000000-0000-4000-8000-000000000832',
    display_name: 'Carmen Ruiz',
  },
  assignee: null,
  created_at: '2031-03-05T18:00:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    report_id: '00000000-0000-4000-8000-000000000833',
    student: {
      id: '00000000-0000-4000-8000-000000000834',
      display_name: 'Ana López',
    },
    author: { display_name: 'Carmen Ruiz' },
    kind: 'weekly',
    period_start: '2031-03-03',
    period_end: '2031-03-09',
    subject: {
      id: '00000000-0000-4000-8000-000000000835',
      name: 'Matemáticas',
    },
    submitted_at: '2031-03-05T18:00:00Z',
  },
  allowed_actions: [],
  version: 1,
};

// M8-01b G1: one item of each remaining type, as the API summarizes it, for
// the detail panel's per-type fields.

/** #44: a tutor's qualification request. */
export const QUALIFICATION: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b01',
  request_type: 'staff.qualification',
  requester: {
    id: '00000000-0000-4000-8000-0000000000b1',
    display_name: 'Carmen Ruiz',
  },
  subject: {
    tutor_display_name: 'Carmen Ruiz',
    subject_name: 'Física',
    education_level_name: 'Preparatoria',
  },
};

/** #52, #55: a substitute declined an offer. */
export const OFFER_REASSIGNMENT: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b02',
  request_type: 'scheduling.offer_reassignment',
  requester: null,
  subject: {
    class_session_id: '00000000-0000-4000-8000-000000000b12',
    subject_name: 'Química',
    starts_at: '2031-04-07T16:00:00Z',
    tutor_name: 'Luis Gómez',
    outcome: 'declined',
    decline_note: 'Tengo otra clase.',
    session_state: 'confirmed',
    class_series_id: null,
    from_date: null,
    occurrence_count: null,
    kind: 'substitution',
    original_tutor_name: 'Carmen Ruiz',
  },
  allowed_actions: ['approve', 'reject'],
};

/** #53: a generated series occurrence in conflict. */
export const OCCURRENCE_CONFLICT: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b03',
  request_type: 'scheduling.occurrence_conflict',
  requester: null,
  subject: {
    class_session_id: '00000000-0000-4000-8000-000000000b13',
    series_id: '00000000-0000-4000-8000-000000000b23',
    starts_at: '2031-04-08T16:00:00Z',
    subject_name: 'Matemáticas',
    student_display_names: ['Ana López', 'Beto Ruiz'],
    tutor_display_name: 'Carmen Ruiz',
    conflict_codes: ['tutor_inactive'],
  },
  allowed_actions: ['approve', 'reject'],
};

/** #59, #60: a family's session request, reviewed on its page. */
export const SESSION_REQUEST: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b04',
  request_type: 'scheduling.session_request',
  requester: {
    id: '00000000-0000-4000-8000-000000000b14',
    display_name: 'Rosa Díaz',
  },
  subject: {
    student_name: 'Ana López',
    subject_name: 'Matemáticas',
    education_level_name: 'Secundaria',
    starts_at: '2031-04-09T16:00:00Z',
    ends_at: '2031-04-09T17:00:00Z',
    duration_minutes: 60,
    requested_by: 'Rosa Díaz',
    note: 'Por la tarde, por favor.',
    status: 'pending',
    session_request_id: '00000000-0000-4000-8000-000000000b24',
    class_session_id: null,
  },
  allowed_actions: [],
};

/** #62: a family's reschedule request, reviewed on its page. */
export const CHANGE_REQUEST: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b05',
  request_type: 'scheduling.change_request',
  requester: {
    id: '00000000-0000-4000-8000-000000000b14',
    display_name: 'Rosa Díaz',
  },
  subject: {
    change_request_id: '00000000-0000-4000-8000-000000000b25',
    kind: 'reschedule',
    initiated_by: 'family',
    student_name: 'Ana López',
    subject_name: 'Matemáticas',
    education_level_name: 'Secundaria',
    starts_at: '2031-04-10T16:00:00Z',
    proposed_starts_at: '2031-04-11T16:00:00Z',
    classification: 'on_time',
    status: 'pending',
    class_session_id: '00000000-0000-4000-8000-000000000b15',
  },
  allowed_actions: [],
};

/** #110: a prepared payroll period, decided on its page. */
export const PAYROLL_PERIOD: ApprovalRequestItem = {
  ...PENDING_REGISTRATION,
  id: '00000000-0000-4000-8000-000000000b06',
  request_type: 'payroll.period',
  requester: {
    id: '00000000-0000-4000-8000-000000000b16',
    display_name: 'Laura Méndez',
  },
  subject: {
    period_id: '00000000-0000-4000-8000-000000000b26',
    tutor: {
      id: '00000000-0000-4000-8000-0000000000b1',
      display_name: 'Carmen Ruiz',
    },
    starts_on: '2031-03-31',
    ends_on: '2031-04-06',
    prepared_by: { display_name: 'Laura Méndez' },
    prepared_at: '2031-04-07T15:00:00Z',
  },
  allowed_actions: [],
};

/** One item of each known type (M8-01b G1), by type. */
export const ITEMS_BY_TYPE: Readonly<Record<string, ApprovalRequestItem>> = {
  'identity.registration': PENDING_REGISTRATION,
  'staff.qualification': QUALIFICATION,
  'scheduling.offer_reassignment': OFFER_REASSIGNMENT,
  'scheduling.occurrence_conflict': OCCURRENCE_CONFLICT,
  'scheduling.session_request': SESSION_REQUEST,
  'scheduling.change_request': CHANGE_REQUEST,
  'staff.availability_change': AVAILABILITY_CHANGE,
  'academics.session_closeout': SESSION_CLOSEOUT,
  'academics.progress_report': PROGRESS_REPORT,
  'payroll.period': PAYROLL_PERIOD,
};
