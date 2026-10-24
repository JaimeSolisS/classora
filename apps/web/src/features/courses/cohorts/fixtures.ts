import type { SchedulingConflict } from '@/features/scheduling/create/api';
import { ALGEBRA, PHYSICS_LAB, summaryOf } from '../fixtures';
import type {
  CohortCreated,
  CohortDetail,
  CohortEnrollmentCreated,
  CohortEnrollmentItem,
  CohortListItem,
  CohortSessionItem,
  TemplateOption,
} from './api';

// Sample data for the cohort pages' tests and stories (#243). Every
// instant is in 2031 (after "now"), in America/Mexico_City (UTC-6).

export const COHORT_ID = '01900000-0000-7000-8000-00000000e001';
export const SERIES_ID = '01900000-0000-7000-8000-00000000e011';
export const ROOM_ID = '01900000-0000-7000-8000-00000000e021';
export const TUTOR = {
  id: '01900000-0000-7000-8000-00000000e031',
  display_name: 'Carmen Ruiz',
};
export const MATH = ALGEBRA.subjects[0]!;
const SUBJECT = {
  id: MATH.id,
  name: MATH.name,
  level: { id: '01900000-0000-7000-8000-00000000e041', name: 'Secundaria' },
};

/** The active templates the create form offers (one subject, then two). */
export const PHYSICS_ACTIVE: TemplateOption = {
  ...PHYSICS_LAB,
  is_active: true,
};
export const TEMPLATE_OPTIONS: TemplateOption[] = [
  summaryOf(ALGEBRA),
  PHYSICS_ACTIVE,
];

const SERIES: NonNullable<CohortDetail['series']> = {
  id: SERIES_ID,
  kind: 'course',
  subject: {
    id: MATH.id,
    name: MATH.name,
    education_level_name: 'Secundaria',
    is_active: true,
  },
  tutor: null,
  classroom: null,
  participants: [],
  starts_on: '2031-01-06',
  start_time: '10:00',
  duration_minutes: 90,
  time_zone: 'America/Mexico_City',
  recurrence: { weekdays: [1, 3], interval: 1, until: null, count: 11 },
  rrule: 'FREQ=WEEKLY;BYDAY=MO,WE;COUNT=11',
  last_occurrence_on: '2031-02-05',
  ended: false,
  split_from_series_id: null,
  split_into_series_ids: [],
  assignment: null,
  group_id: null,
  cohort_id: COHORT_ID,
  version: 2,
  created_at: '2030-12-01T16:00:00Z',
  updated_at: '2030-12-01T16:00:00Z',
};

/** An upcoming cohort without a tutor (the offer panel shows). */
export const COHORT: CohortDetail = {
  id: COHORT_ID,
  name: 'Álgebra enero',
  version: 3,
  created_at: '2030-12-01T16:00:00Z',
  updated_at: '2030-12-01T16:00:00Z',
  template: { id: ALGEBRA.id, name: ALGEBRA.name, version: 1 },
  template_snapshot_id: '01900000-0000-7000-8000-00000000e051',
  subject: SUBJECT,
  max_students: 12,
  effective_capacity: 12,
  status: 'upcoming',
  planned_session_count: 10,
  session_minutes: 90,
  first_session_on: '2031-01-06',
  last_session_on: '2031-02-05',
  series: SERIES,
  next_occurrences: [
    {
      id: '01900000-0000-7000-8000-00000000f001',
      state: 'unassigned',
      starts_at: '2031-01-06T16:00:00Z',
      ends_at: '2031-01-06T17:30:00Z',
    },
    {
      id: '01900000-0000-7000-8000-00000000f002',
      state: 'skipped_closure',
      starts_at: '2031-01-08T16:00:00Z',
      ends_at: '2031-01-08T17:30:00Z',
    },
  ],
  syllabus: {
    modules: ALGEBRA.modules.map((module, index) =>
      index === 0
        ? { ...module, title: 'Ecuaciones (copia congelada)' }
        : module,
    ),
    assessments: ALGEBRA.assessments,
    completion: ALGEBRA.completion,
  },
};

/** With an accepted tutor and a room that holds fewer than the maximum. */
export const TUTORED_COHORT: CohortDetail = {
  ...COHORT,
  status: 'in_progress',
  effective_capacity: 8,
  series: {
    ...SERIES,
    tutor: TUTOR,
    classroom: { id: ROOM_ID, name: 'Aula 2', is_active: true },
    assignment: {
      status: 'accepted',
      tutor: TUTOR,
      from_date: '2031-01-06',
      offer_id: '01900000-0000-7000-8000-00000000e061',
      respond_by: null,
    },
  },
};

/** A pending series offer with a deadline. */
export const PENDING_COHORT: CohortDetail = {
  ...COHORT,
  series: {
    ...SERIES,
    assignment: {
      status: 'pending',
      tutor: TUTOR,
      from_date: '2031-01-06',
      offer_id: '01900000-0000-7000-8000-00000000e061',
      respond_by: '2031-01-03T18:00:00Z',
    },
  },
};

export const ENDED_COHORT: CohortDetail = {
  ...TUTORED_COHORT,
  status: 'ended',
  next_occurrences: [],
  series: { ...TUTORED_COHORT.series!, ended: true },
};

export const CREATED_COHORT: CohortCreated = { ...COHORT, warnings: [] };

export const LIST_ITEMS: CohortListItem[] = [
  {
    id: COHORT_ID,
    name: 'Álgebra enero',
    template: { id: ALGEBRA.id, name: ALGEBRA.name },
    subject: SUBJECT,
    max_students: 12,
    tutor: null,
    assignment_status: null,
    first_session_on: '2031-01-06',
    last_session_on: '2031-02-05',
    next_starts_at: '2031-01-06T16:00:00Z',
    status: 'upcoming',
    enrolled_count: 0,
  },
  {
    id: '01900000-0000-7000-8000-00000000e002',
    name: 'Álgebra febrero',
    template: { id: ALGEBRA.id, name: ALGEBRA.name },
    subject: SUBJECT,
    max_students: 10,
    tutor: TUTOR,
    assignment_status: 'pending',
    first_session_on: '2031-02-03',
    last_session_on: '2031-03-05',
    next_starts_at: '2031-02-03T16:00:00Z',
    status: 'upcoming',
    enrolled_count: 0,
  },
  {
    id: '01900000-0000-7000-8000-00000000e003',
    name: 'Física otoño',
    template: { id: PHYSICS_LAB.id, name: PHYSICS_LAB.name },
    subject: SUBJECT,
    max_students: 8,
    tutor: { id: TUTOR.id, display_name: 'Diego Torres' },
    assignment_status: 'accepted',
    first_session_on: '2030-09-02',
    last_session_on: '2030-10-30',
    next_starts_at: null,
    status: 'ended',
    enrolled_count: 0,
  },
  {
    id: '01900000-0000-7000-8000-00000000e004',
    name: 'Álgebra en curso',
    template: { id: ALGEBRA.id, name: ALGEBRA.name },
    subject: SUBJECT,
    max_students: 10,
    tutor: TUTOR,
    assignment_status: 'accepted',
    first_session_on: '2030-12-02',
    last_session_on: '2031-01-15',
    next_starts_at: '2031-01-06T16:00:00Z',
    status: 'in_progress',
    enrolled_count: 0,
  },
];

export const SESSIONS: CohortSessionItem[] = [
  {
    id: '01900000-0000-7000-8000-00000000f001',
    state: 'unassigned',
    starts_at: '2031-01-06T16:00:00Z',
    ends_at: '2031-01-06T17:30:00Z',
    original_start: '2031-01-06T16:00:00Z',
    is_exception: false,
    tutor: null,
    classroom: null,
  },
  {
    id: '01900000-0000-7000-8000-00000000f003',
    state: 'unassigned',
    starts_at: '2031-01-13T17:00:00Z',
    ends_at: '2031-01-13T18:30:00Z',
    original_start: '2031-01-13T16:00:00Z',
    is_exception: true,
    tutor: null,
    classroom: { id: ROOM_ID, name: 'Aula 2' },
  },
];

/** Two soft conflicts of single occurrences (overridable as pairs). */
export const SOFT_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { occurrence_start: '2031-01-06T16:00:00Z', date: '2031-01-06' },
  },
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { occurrence_start: '2031-01-08T16:00:00Z', date: '2031-01-08' },
  },
];

export const HARD_CONFLICT: SchedulingConflict = {
  code: 'room_overlap',
  severity: 'hard',
  details: { occurrence_start: '2031-01-13T16:00:00Z', date: '2031-01-13' },
};

/** A soft conflict without an occurrence (the create can't take it). */
export const COHORT_LEVEL_CONFLICT: SchedulingConflict = {
  code: 'notice_window',
  severity: 'soft',
  details: { booking_notice_hours: 24 },
};

// --- Enrollment (#245) ---------------------------------------------------------

const STAFF = {
  id: '01900000-0000-7000-8000-00000000e071',
  display_name: 'Demo Owner',
};

export const STUDENT_ANA = {
  id: '01900000-0000-7000-8000-00000000e081',
  display_name: 'Ana López',
  is_active: true,
};
export const STUDENT_BRUNO = {
  id: '01900000-0000-7000-8000-00000000e082',
  display_name: 'Bruno Díaz',
  is_active: false,
};
export const STUDENT_CARLA = {
  id: '01900000-0000-7000-8000-00000000e083',
  display_name: 'Carla Méndez',
  is_active: true,
};
export const STUDENT_DIEGO = {
  id: '01900000-0000-7000-8000-00000000e084',
  display_name: 'Diego Ruiz',
  is_active: true,
};

/**
 * Active: Ana, enrolled at 05:30Z, the evening before in Mexico City
 * (a foreign host zone would show the next day).
 */
export const ENROLLED_ANA: CohortEnrollmentItem = {
  id: '01900000-0000-7000-8000-00000000e091',
  student: STUDENT_ANA,
  enrolled_at: '2030-12-10T05:30:00Z',
  enrolled_by: STAFF,
  withdrawn_at: null,
  withdrawn_by: null,
  withdrawal_reason: null,
  overridden: false,
  version: 1,
};

/** Active, above the maximum by an override, and an inactive student. */
export const ENROLLED_BRUNO: CohortEnrollmentItem = {
  id: '01900000-0000-7000-8000-00000000e092',
  student: STUDENT_BRUNO,
  enrolled_at: '2030-12-11T18:00:00Z',
  enrolled_by: STAFF,
  withdrawn_at: null,
  withdrawn_by: null,
  withdrawal_reason: null,
  overridden: true,
  version: 1,
};

export const WITHDRAWN_CARLA: CohortEnrollmentItem = {
  id: '01900000-0000-7000-8000-00000000e093',
  student: STUDENT_CARLA,
  enrolled_at: '2030-12-01T18:00:00Z',
  enrolled_by: STAFF,
  withdrawn_at: '2030-12-20T18:00:00Z',
  withdrawn_by: STAFF,
  withdrawal_reason: 'Cambió de horario escolar.',
  overridden: false,
  version: 2,
};

export const WITHDRAWN_DIEGO: CohortEnrollmentItem = {
  id: '01900000-0000-7000-8000-00000000e094',
  student: STUDENT_DIEGO,
  enrolled_at: '2030-12-01T18:00:00Z',
  enrolled_by: STAFF,
  withdrawn_at: '2030-12-05T18:00:00Z',
  withdrawn_by: STAFF,
  withdrawal_reason: null,
  overridden: false,
  version: 2,
};

/** Upcoming, 2 of 12 enrolled, two withdrawn. */
export const ROSTER_COHORT: CohortDetail = {
  ...COHORT,
  enrolled_count: 2,
  roster: [ENROLLED_ANA, ENROLLED_BRUNO, WITHDRAWN_DIEGO, WITHDRAWN_CARLA],
};

/** Upcoming, nobody enrolled. */
export const EMPTY_ROSTER_COHORT: CohortDetail = {
  ...COHORT,
  enrolled_count: 0,
  roster: [],
};

/** In progress, the room (8) below the maximum (12), and 8 enrolled: full. */
export const FULL_COHORT: CohortDetail = {
  ...TUTORED_COHORT,
  enrolled_count: 8,
  roster: [ENROLLED_ANA, ENROLLED_BRUNO],
};

/** Ended: no "Enroll student". */
export const ENDED_ROSTER_COHORT: CohortDetail = {
  ...ENDED_COHORT,
  enrolled_count: 1,
  roster: [ENROLLED_ANA],
};

export const ENROLLED_CARLA: CohortEnrollmentCreated = {
  ...WITHDRAWN_CARLA,
  id: '01900000-0000-7000-8000-00000000e095',
  enrolled_at: '2031-01-02T18:00:00Z',
  withdrawn_at: null,
  withdrawn_by: null,
  withdrawal_reason: null,
  version: 1,
  warnings: [],
};

/** The cohort's maximum (#66: its details name the cohort). */
export const COHORT_CAPACITY_CONFLICT: SchedulingConflict = {
  code: 'group_capacity_exceeded',
  severity: 'soft',
  details: { cohort_id: COHORT_ID, max_students: 12, enrolled_count: 12 },
};

/** A soft conflict of one occurrence (the enroll overrides it by code). */
export const ENROLL_SOFT_OCCURRENCE: SchedulingConflict = {
  code: 'outside_operating_hours',
  severity: 'soft',
  details: { occurrence_start: '2031-01-13T16:00:00Z', date: '2031-01-13' },
};

/** The room is too small for one occurrence (hard, no date-less shape). */
export const ENROLL_ROOM_CONFLICT: SchedulingConflict = {
  code: 'room_capacity_exceeded',
  severity: 'hard',
  details: {
    occurrence_start: '2031-01-06T16:00:00Z',
    date: '2031-01-06',
    classroom_id: ROOM_ID,
    room_capacity: 8,
  },
};

export const ENROLL_OVERLAP_CONFLICT: SchedulingConflict = {
  code: 'student_overlap',
  severity: 'hard',
  details: {
    occurrence_start: '2031-01-08T16:00:00Z',
    date: '2031-01-08',
    student_ids: [STUDENT_CARLA.id],
  },
};
