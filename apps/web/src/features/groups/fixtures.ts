import type { SchedulingConflict } from '@/features/scheduling/create/api';
import type { ClassroomCapacityOption } from './api';
import type {
  GroupCreated,
  GroupDetail,
  GroupEnrollmentCreated,
  GroupEnrollmentItem,
  GroupListItem,
  GroupSessionItem,
} from './api';

// Sample data for the group pages' tests and stories (#234 G1, G2). Every
// instant is in 2031 (after "now"), in America/Mexico_City (UTC-6).

export const GROUP_ID = '00000000-0000-4000-8000-000000000b01';
export const ENDED_GROUP_ID = '00000000-0000-4000-8000-000000000b02';
export const SERIES_ID = '00000000-0000-4000-8000-000000000b11';
export const SUBJECT_ID = '00000000-0000-4000-8000-0000000004b1';
export const ROOM_ID = '00000000-0000-4000-8000-0000000004d1';
export const OWNER = {
  id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Demo Owner',
};

export const STUDENT_ANA = {
  id: '00000000-0000-4000-8000-0000000004a1',
  display_name: 'Ana López',
  is_active: true,
};
export const STUDENT_BRUNO = {
  id: '00000000-0000-4000-8000-0000000004a2',
  display_name: 'Bruno Díaz',
  is_active: false,
};
export const STUDENT_CARLA = {
  id: '00000000-0000-4000-8000-0000000004a3',
  display_name: 'Carla Méndez',
  is_active: true,
};

const SUBJECT = {
  id: SUBJECT_ID,
  name: 'Álgebra',
  level: { id: '00000000-0000-4000-8000-0000000004f1', name: 'Secundaria' },
};

export const ACTIVE_ANA: GroupEnrollmentItem = {
  id: '00000000-0000-4000-8000-000000000c01',
  student: STUDENT_ANA,
  enrolled_at: '2030-11-02T16:00:00Z',
  enrolled_by: OWNER,
  withdrawn_at: null,
  withdrawn_by: null,
  withdrawal_reason: null,
  overridden: false,
  version: 1,
};

export const ACTIVE_BRUNO: GroupEnrollmentItem = {
  id: '00000000-0000-4000-8000-000000000c02',
  student: STUDENT_BRUNO,
  enrolled_at: '2030-11-03T16:00:00Z',
  enrolled_by: OWNER,
  withdrawn_at: null,
  withdrawn_by: null,
  withdrawal_reason: null,
  overridden: true,
  version: 2,
};

export const WITHDRAWN_CARLA: GroupEnrollmentItem = {
  id: '00000000-0000-4000-8000-000000000c03',
  student: STUDENT_CARLA,
  enrolled_at: '2030-10-01T16:00:00Z',
  enrolled_by: OWNER,
  withdrawn_at: '2030-11-10T18:00:00Z',
  withdrawn_by: OWNER,
  withdrawal_reason: 'Se mudó de ciudad.',
  overridden: false,
  version: 3,
};

export const WITHDRAWN_CARLA_EARLIER: GroupEnrollmentItem = {
  ...WITHDRAWN_CARLA,
  id: '00000000-0000-4000-8000-000000000c04',
  withdrawn_at: '2030-10-20T18:00:00Z',
  withdrawal_reason: null,
};

export const GROUP: GroupDetail = {
  id: GROUP_ID,
  name: 'Álgebra sábado',
  subject: SUBJECT,
  max_students: 6,
  enrolled_count: 2,
  effective_capacity: 6,
  current_series: {
    id: SERIES_ID,
    tutor: {
      id: '00000000-0000-4000-8000-0000000004c1',
      display_name: 'Carmen Ruiz',
    },
    classroom: { id: ROOM_ID, name: 'Aula 1', is_active: true },
    starts_on: '2031-01-04',
    start_time: '10:00',
    duration_minutes: 90,
    time_zone: 'America/Mexico_City',
    recurrence: { weekdays: [6], interval: 1, until: null, count: null },
    last_occurrence_on: null,
    version: 4,
  },
  next_occurrences: [
    {
      id: '00000000-0000-4000-8000-000000000d01',
      state: 'confirmed',
      starts_at: '2031-01-04T16:00:00Z',
      ends_at: '2031-01-04T17:30:00Z',
    },
    {
      id: '00000000-0000-4000-8000-000000000d02',
      state: 'offered',
      starts_at: '2031-01-11T16:00:00Z',
      ends_at: '2031-01-11T17:30:00Z',
    },
  ],
  version: 5,
  created_at: '2030-10-01T16:00:00Z',
  updated_at: '2030-11-10T18:00:00Z',
  roster: [ACTIVE_ANA, ACTIVE_BRUNO, WITHDRAWN_CARLA_EARLIER, WITHDRAWN_CARLA],
};

/** The room holds 4: at most 4 can attend (C2). */
export const ROOM_LIMITED_GROUP: GroupDetail = {
  ...GROUP,
  max_students: 8,
  effective_capacity: 4,
};

export const ENDED_GROUP: GroupDetail = {
  ...GROUP,
  id: ENDED_GROUP_ID,
  name: 'Física verano',
  current_series: null,
  next_occurrences: [],
};

/**
 * What a student reads (#56 D7): no count, no roster. The defence-in-depth
 * fixture below carries them anyway.
 */
export const STUDENT_GROUP: GroupDetail = {
  ...GROUP,
  enrolled_count: null,
  roster: null,
  next_occurrences: [GROUP.next_occurrences[0]!],
};

export const LEAKY_STUDENT_GROUP: GroupDetail = { ...GROUP };

export const LIST_ITEMS: GroupListItem[] = [
  {
    id: GROUP_ID,
    name: 'Álgebra sábado',
    subject: SUBJECT,
    max_students: 6,
    enrolled_count: 2,
    tutor: {
      id: '00000000-0000-4000-8000-0000000004c1',
      display_name: 'Carmen Ruiz',
    },
    next_starts_at: '2031-01-04T16:00:00Z',
  },
  {
    id: ENDED_GROUP_ID,
    name: 'Física verano',
    subject: { ...SUBJECT, name: 'Física' },
    max_students: 4,
    enrolled_count: 0,
    tutor: null,
    next_starts_at: null,
  },
];

export const STUDENT_LIST_ITEMS: GroupListItem[] = [
  { ...LIST_ITEMS[0]!, enrolled_count: null },
];

export const SESSIONS: GroupSessionItem[] = [
  {
    id: '00000000-0000-4000-8000-000000000d01',
    state: 'confirmed',
    starts_at: '2031-01-04T16:00:00Z',
    ends_at: '2031-01-04T17:30:00Z',
    tutor: {
      id: '00000000-0000-4000-8000-0000000004c1',
      display_name: 'Carmen Ruiz',
    },
    classroom: { id: ROOM_ID, name: 'Aula 1' },
  },
  {
    id: '00000000-0000-4000-8000-000000000d00',
    state: 'completed',
    starts_at: '2030-12-28T16:00:00Z',
    ends_at: '2030-12-28T17:30:00Z',
    tutor: null,
    classroom: null,
  },
];

export const ROOMS: ClassroomCapacityOption[] = [
  { id: ROOM_ID, name: 'Aula 1', capacity: 10 },
  { id: '00000000-0000-4000-8000-0000000004d2', name: 'Sala 4', capacity: 4 },
];

// --- 409s ---------------------------------------------------------------------

/** The enroll's group-level soft conflict (#56 E1 details). */
export const CAPACITY_CONFLICT: SchedulingConflict = {
  code: 'group_capacity_exceeded',
  severity: 'soft',
  details: { group_id: GROUP_ID, max_students: 2, enrolled_count: 2 },
};

/** An enroll's room conflict (the B2 details shape). */
export const ENROLL_ROOM_CONFLICT: SchedulingConflict = {
  code: 'room_capacity_exceeded',
  severity: 'hard',
  details: {
    class_session_id: '00000000-0000-4000-8000-000000000d01',
    occurrence_start: '2031-01-04T16:00:00Z',
    date: '2031-01-04',
    classroom_id: ROOM_ID,
    room_capacity: 2,
    participant_count: 3,
  },
};

export const ENROLL_OVERLAP_CONFLICT: SchedulingConflict = {
  code: 'student_overlap',
  severity: 'hard',
  details: {
    occurrence_start: '2031-01-11T16:00:00Z',
    date: '2031-01-11',
    student_ids: [STUDENT_CARLA.id],
  },
};

/** The create's room conflict (the collect_conflicts details shape). */
export const CREATE_ROOM_CONFLICT: SchedulingConflict = {
  code: 'room_capacity_exceeded',
  severity: 'hard',
  details: {
    participants: 3,
    room_capacity: 2,
    occurrence_start: '2031-01-11T16:00:00Z',
    date: '2031-01-11',
  },
};

/** A Sunday occurrence outside the opening hours (soft, a pair). */
export const CLOSED_SUNDAY: SchedulingConflict = {
  code: 'outside_operating_hours',
  severity: 'soft',
  details: {
    occurrence_start: '2031-01-05T16:00:00Z',
    date: '2031-01-05',
  },
};

export const CLOSED_SUNDAY_LATER: SchedulingConflict = {
  ...CLOSED_SUNDAY,
  details: {
    occurrence_start: '2031-01-12T16:00:00Z',
    date: '2031-01-12',
  },
};

/** The create's soft-only 409: the group level, then two dates. */
export const CREATE_SOFT_CONFLICTS: SchedulingConflict[] = [
  CAPACITY_CONFLICT,
  CLOSED_SUNDAY,
  CLOSED_SUNDAY_LATER,
];

/** A soft group-level code the create doesn't accept (E3). */
export const UNACCEPTED_GROUP_CONFLICT: SchedulingConflict = {
  code: 'notice_window',
  severity: 'soft',
  details: { booking_notice_hours: 24 },
};

export const ENROLLED: GroupEnrollmentCreated = {
  ...ACTIVE_ANA,
  id: '00000000-0000-4000-8000-000000000c09',
  student: STUDENT_CARLA,
  warnings: [],
};

export const CREATED_GROUP: GroupCreated = { ...GROUP, warnings: [] };

// --- Tutors and guardians (#246) ------------------------------------------------

/** What the group's tutor reads (#246 A4, E2): names only, no roster. */
export const TUTOR_GROUP: GroupDetail = {
  ...STUDENT_GROUP,
  tutor_roster: [
    { student: { id: STUDENT_ANA.id, display_name: STUDENT_ANA.display_name } },
    {
      student: {
        id: STUDENT_BRUNO.id,
        display_name: STUDENT_BRUNO.display_name,
      },
    },
  ],
};

/** What a guardian reads (#246 A4): the tutor's name only. */
export const FAMILY_GROUP: GroupDetail = {
  ...STUDENT_GROUP,
  current_series: {
    ...GROUP.current_series!,
    tutor: { display_name: 'Carmen Ruiz' },
  },
};

const ANA_REF = { id: STUDENT_ANA.id, display_name: STUDENT_ANA.display_name };
const CARLA_REF = {
  id: STUDENT_CARLA.id,
  display_name: STUDENT_CARLA.display_name,
};

/**
 * A guardian of Ana and Carla (#246 A5, B3): a confirmed session of Ana, a
 * once-confirmed cancelled one of both, a closure of Carla.
 */
export const FAMILY_SESSIONS: GroupSessionItem[] = [
  {
    id: '00000000-0000-4000-8000-000000000e01',
    state: 'confirmed',
    starts_at: '2031-01-04T16:00:00Z',
    ends_at: '2031-01-04T17:30:00Z',
    tutor: { display_name: 'Carmen Ruiz' },
    classroom: { id: ROOM_ID, name: 'Aula 1' },
    students: [ANA_REF],
  },
  {
    id: '00000000-0000-4000-8000-000000000e02',
    state: 'cancelled',
    starts_at: '2031-01-11T16:00:00Z',
    ends_at: '2031-01-11T17:30:00Z',
    tutor: { display_name: 'Carmen Ruiz' },
    classroom: null,
    students: [ANA_REF, CARLA_REF],
  },
  {
    id: '00000000-0000-4000-8000-000000000e03',
    state: 'skipped_closure',
    starts_at: '2031-01-18T16:00:00Z',
    ends_at: '2031-01-18T17:30:00Z',
    tutor: null,
    classroom: null,
    students: [CARLA_REF],
  },
];

/** The same sessions for a guardian of Ana only (no "For: …" labels). */
export const ONE_CHILD_SESSIONS: GroupSessionItem[] = FAMILY_SESSIONS.slice(
  0,
  2,
).map((item) => ({ ...item, students: [ANA_REF] }));

/** The tutor's sessions (#246 A5): with the participants. */
export const TUTOR_SESSIONS: GroupSessionItem[] = [
  {
    ...SESSIONS[0]!,
    state: 'offered',
    participants: [
      { student_id: STUDENT_ANA.id, display_name: STUDENT_ANA.display_name },
      {
        student_id: STUDENT_BRUNO.id,
        display_name: STUDENT_BRUNO.display_name,
      },
    ],
  },
];
