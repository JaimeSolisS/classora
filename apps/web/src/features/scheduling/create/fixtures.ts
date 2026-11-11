import type {
  ClassSessionCreated,
  Option,
  SchedulingConflict,
  SchedulingWarning,
  SlotSuggestions,
  SeriesTutorSuggestionList,
  SubjectOption,
  TutorSuggestionList,
} from './api';
import type { TutorAvailability } from '@/features/tutors/api';
import type { OwnerOption, OwnerSummary, ScheduleValues } from './rules';

// Sample data for the scheduling form's tests and stories (#48 F4, #49 F5,
// #50 C3, #51 G3; group and course sessions, HF-11b).

export const ANA: Option = {
  id: '00000000-0000-4000-8000-0000000004a1',
  name: 'Ana López',
};

export const SUBJECTS: SubjectOption[] = [
  {
    id: '00000000-0000-4000-8000-0000000004b1',
    name: 'Álgebra',
    levelName: 'Secundaria',
  },
  {
    id: '00000000-0000-4000-8000-0000000004b2',
    name: 'Física',
    levelName: 'Secundaria',
  },
  {
    id: '00000000-0000-4000-8000-0000000004b3',
    name: 'Cálculo',
    levelName: 'Preparatoria',
  },
];

/** The built-in General subject (HF-12): preselected on a new session. */
export const GENERAL_SUBJECT: SubjectOption = {
  id: '00000000-0000-4000-8000-0000000004b0',
  name: 'General',
  levelName: 'General',
  isGeneral: true,
};

export const TUTORS: Option[] = [
  { id: '00000000-0000-4000-8000-0000000004c1', name: 'Carmen Ruiz' },
  { id: '00000000-0000-4000-8000-0000000004c2', name: 'Diego Torres' },
];

export const CLASSROOMS: Option[] = [
  { id: '00000000-0000-4000-8000-0000000004d1', name: 'Aula 1' },
];

export const FILLED: ScheduleValues = {
  student: ANA,
  subjectId: SUBJECTS[0]!.id,
  tutorId: TUTORS[0]!.id,
  classroomId: CLASSROOMS[0]!.id,
  date: '2031-01-06',
  start: '10:00',
  end: '11:00',
};

export const CREATED: ClassSessionCreated = {
  id: '00000000-0000-4000-8000-0000000004e1',
  kind: 'individual',
  state: 'offered',
  subject: {
    id: SUBJECTS[0]!.id,
    name: 'Álgebra',
    education_level_name: 'Secundaria',
    is_active: true,
  },
  tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
  classroom: { id: CLASSROOMS[0]!.id, name: 'Aula 1', is_active: true },
  participants: [
    { student_id: ANA.id, display_name: 'Ana López', is_active: true },
  ],
  starts_at: '2031-01-06T16:00:00Z',
  ends_at: '2031-01-06T17:00:00Z',
  scheduled_minutes: 60,
  version: 1,
  created_at: '2026-10-21T12:00:00Z',
  updated_at: '2026-10-21T12:00:00Z',
  overrides: [],
  series_id: null,
  original_start: null,
  is_exception: false,
  conflict_codes: [],
  pending_offer: {
    id: '00000000-0000-4000-8000-0000000005f1',
    scope: 'session',
    tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
    respond_by: '2026-10-23T12:00:00Z',
  },
  pending_substitution: null,
  cancellation: null,
  group_id: null,
  cohort_id: null,
  pending_change_request_id: null,
  reassignment: null,
  makeup_of_session_id: null,
  makeup: null,
  warnings: [],
};

export const UNASSIGNED: ClassSessionCreated = {
  ...CREATED,
  state: 'unassigned',
  tutor: null,
  classroom: null,
  pending_offer: null,
};

/** One hard and two soft conflicts, as the server sorts them. */
export const CONFLICTS: SchedulingConflict[] = [
  {
    code: 'student_overlap',
    severity: 'hard',
    details: { student_id: ANA.id, class_session_ids: [] },
  },
  {
    code: 'tutor_unavailable',
    severity: 'soft',
    details: { reasons: ['time_off'] },
  },
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { date: '2031-01-11', opens_at: '09:00', closes_at: '14:00' },
  },
];

/** Two soft conflicts only: a holder of schedule.override can override them. */
export const SOFT_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { date: '2031-01-12', opens_at: null, closes_at: null },
  },
  {
    code: 'tutor_not_qualified',
    severity: 'soft',
    details: { subject_id: SUBJECTS[0]!.id },
  },
];

export const OVERRIDE_REASON = 'La familia lo pidió; el tutor está de acuerdo.';

/** Created with an override of both SOFT_CONFLICTS. */
export const OVERRIDDEN: ClassSessionCreated = {
  ...CREATED,
  starts_at: '2031-01-12T16:00:00Z',
  ends_at: '2031-01-12T17:00:00Z',
  overrides: [
    {
      id: '00000000-0000-4000-8000-0000000004e2',
      codes: ['outside_operating_hours', 'tutor_not_qualified'],
      reason: OVERRIDE_REASON,
      actor: {
        id: '00000000-0000-4000-8000-0000000004e3',
        display_name: 'Olga Dueña',
      },
      created_at: '2026-10-21T12:00:00Z',
    },
  ],
};

// --- Workload warnings and suggested times (#50 C) ---------------------------

/** One of each workload warning, in the server's order. */
export const WARNINGS: SchedulingWarning[] = [
  {
    code: 'daily_limit_exceeded',
    details: { date: '2031-01-06', limit_minutes: 240, scheduled_minutes: 300 },
  },
  {
    code: 'weekly_limit_exceeded',
    details: {
      week_start: '2031-01-06',
      limit_minutes: 1200,
      scheduled_minutes: 1260,
    },
  },
  {
    code: 'break_too_short',
    details: {
      class_session_id: '00000000-0000-4000-8000-0000000004e9',
      gap_minutes: 1,
      required_break_minutes: 15,
    },
  },
];

/** Created with all three warnings. */
export const CREATED_WITH_WARNINGS: ClassSessionCreated = {
  ...CREATED,
  warnings: WARNINGS,
};

/** Three free times on Monday 2031-01-06 (Mexico City, UTC-6). */
export const SUGGESTIONS: SlotSuggestions = {
  search_from: '2031-01-06',
  search_to: '2031-01-12',
  limit: 5,
  slots: [
    {
      starts_at: '2031-01-06T17:00:00Z',
      ends_at: '2031-01-06T18:00:00Z',
      warnings: [],
    },
    {
      starts_at: '2031-01-06T17:15:00Z',
      ends_at: '2031-01-06T18:15:00Z',
      warnings: [WARNINGS[0]!],
    },
    {
      starts_at: '2031-01-06T15:00:00Z',
      ends_at: '2031-01-06T16:00:00Z',
      warnings: [],
    },
  ],
};

/** No free time in the default window. */
export const NO_SUGGESTIONS: SlotSuggestions = { ...SUGGESTIONS, slots: [] };

/** Three eligible tutors (#51 G3): one prefers the room, one has limits. */
export const TUTOR_SUGGESTIONS: TutorSuggestionList = {
  items: [
    {
      rank: 1,
      tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
      weekly_minutes: 0,
      daily_minutes: 0,
      weekly_limit_minutes: null,
      daily_limit_minutes: null,
      prefers_classroom: true,
      warnings: [],
    },
    {
      rank: 2,
      tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
      weekly_minutes: 150,
      daily_minutes: 60,
      weekly_limit_minutes: 900,
      daily_limit_minutes: 240,
      prefers_classroom: false,
      warnings: [],
    },
    {
      rank: 3,
      tutor: {
        id: '00000000-0000-4000-8000-0000000004c3',
        display_name: 'Elena Vega',
      },
      weekly_minutes: 300,
      daily_minutes: 0,
      weekly_limit_minutes: null,
      daily_limit_minutes: null,
      prefers_classroom: false,
      warnings: [],
    },
  ],
  eligible_count: 4,
  qualified_count: 6,
  busy_count: 1,
  unavailable_count: 1,
};

/** Nobody free: 3 qualified, 2 busy and 1 unavailable (HF-05b B5). */
export const NO_TUTOR_SUGGESTIONS: TutorSuggestionList = {
  items: [],
  eligible_count: 0,
  qualified_count: 3,
  busy_count: 2,
  unavailable_count: 1,
};

/** Nobody holds the subject (HF-05b B5). */
export const NO_QUALIFIED_TUTORS: TutorSuggestionList = {
  items: [],
  eligible_count: 0,
  qualified_count: 0,
  busy_count: 0,
  unavailable_count: 0,
};

/** One tutor over the daily limit and with a short break (HF-05b B3). */
export const TUTOR_SUGGESTIONS_WITH_WARNINGS: TutorSuggestionList = {
  items: [
    {
      ...TUTOR_SUGGESTIONS.items[1]!,
      rank: 1,
      daily_minutes: 210,
      warnings: [
        {
          code: 'daily_limit_exceeded',
          details: {
            date: '2031-01-06',
            limit_minutes: 240,
            scheduled_minutes: 270,
          },
        },
        {
          code: 'break_too_short',
          details: { gap_minutes: 5, required_break_minutes: 10 },
        },
      ],
    },
  ],
  eligible_count: 1,
  qualified_count: 1,
  busy_count: 0,
  unavailable_count: 0,
};

/** Seven eligible tutors: five shown, then "View all" (HF-05b B3). */
export const MANY_TUTOR_SUGGESTIONS: TutorSuggestionList = {
  items: Array.from({ length: 7 }, (_, index) => ({
    rank: index + 1,
    tutor: {
      id: `00000000-0000-4000-8000-0000000005${String(index).padStart(2, '0')}`,
      display_name: `Tutor ${index + 1}`,
    },
    weekly_minutes: index * 60,
    daily_minutes: 0,
    weekly_limit_minutes: null,
    daily_limit_minutes: null,
    prefers_classroom: false,
    warnings: [],
  })),
  eligible_count: 7,
  qualified_count: 7,
  busy_count: 0,
  unavailable_count: 0,
};

/** A weekly series: one tutor free for all 12 sessions (HF-05b B2). */
export const SERIES_TUTOR_SUGGESTIONS: SeriesTutorSuggestionList = {
  items: [{ ...TUTOR_SUGGESTIONS.items[0]!, warnings: [] }],
  eligible_count: 1,
  qualified_count: 2,
  busy_count: 0,
  unavailable_count: 1,
  occurrence_count: 12,
};

/** A series with nobody free: 1 qualified, unavailable once (HF-05b B5). */
export const NO_SERIES_TUTOR_SUGGESTIONS: SeriesTutorSuggestionList = {
  items: [],
  eligible_count: 0,
  qualified_count: 1,
  busy_count: 0,
  unavailable_count: 1,
  occurrence_count: 2,
};

/** Carmen's weekly windows (HF-05b C1), out of order on purpose. */
export const CARMEN_AVAILABILITY: TutorAvailability = {
  weekly: [
    { weekday: 3, starts_at: '09:00', ends_at: '13:00' },
    { weekday: 1, starts_at: '16:00', ends_at: '20:00' },
  ],
  exceptions: [],
  limits: {
    daily_limit_minutes: null,
    weekly_limit_minutes: null,
    required_break_minutes: null,
  },
  preferred_classrooms: [],
  version: 1,
  pending_changes: [],
  recent_changes: [],
};

// --- Group and course sessions (HF-11b) ---------------------------------------

export const BRUNO: Option = {
  id: '00000000-0000-4000-8000-0000000004a2',
  name: 'Bruno Díaz Hernández de la Fuente',
};

export const GROUP_OPTION: OwnerOption = {
  id: '00000000-0000-4000-8000-0000000006a1',
  name: 'Club de Matemáticas',
  subjectName: 'Álgebra',
};

export const COHORT_OPTION: OwnerOption = {
  id: '00000000-0000-4000-8000-0000000006b1',
  name: 'Olimpiada de física 2031',
  subjectName: 'Física',
};

export const GROUP_SUMMARY: OwnerSummary = {
  id: GROUP_OPTION.id,
  name: GROUP_OPTION.name,
  subject: SUBJECTS[0]!,
  firstSessionOn: null,
  lastSessionOn: null,
  students: [ANA, BRUNO],
};

export const EMPTY_GROUP_SUMMARY: OwnerSummary = {
  ...GROUP_SUMMARY,
  students: [],
};

export const COHORT_SUMMARY: OwnerSummary = {
  id: COHORT_OPTION.id,
  name: COHORT_OPTION.name,
  subject: SUBJECTS[1]!,
  firstSessionOn: '2031-01-06',
  lastSessionOn: '2031-03-28',
  students: [ANA],
};

/** The date and times of a group or course session (no student or subject). */
export const OWNER_FILLED: ScheduleValues = {
  ...FILLED,
  student: null,
  subjectId: '',
  tutorId: '',
  classroomId: '',
};

export const GROUP_CREATED: ClassSessionCreated = {
  ...UNASSIGNED,
  kind: 'group',
  group_id: GROUP_OPTION.id,
  participants: [
    { student_id: ANA.id, display_name: ANA.name, is_active: true },
    { student_id: BRUNO.id, display_name: BRUNO.name, is_active: true },
  ],
};

export const COURSE_CREATED: ClassSessionCreated = {
  ...CREATED,
  kind: 'course',
  cohort_id: COHORT_OPTION.id,
  subject: {
    id: SUBJECTS[1]!.id,
    name: 'Física',
    education_level_name: 'Secundaria',
    is_active: true,
  },
};

/** A `student_overlap` for Bruno (hard) and a soft one. */
export const GROUP_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'student_overlap',
    severity: 'hard',
    details: {
      student_id: BRUNO.id,
      class_session_ids: ['00000000-0000-4000-8000-0000000007a1'],
    },
  },
  {
    code: 'tutor_not_qualified',
    severity: 'soft',
    details: {},
  },
];
