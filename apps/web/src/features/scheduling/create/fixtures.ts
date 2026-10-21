import type {
  ClassSessionCreated,
  Option,
  SchedulingConflict,
  SchedulingWarning,
  SlotSuggestions,
  SubjectOption,
  TutorSuggestionList,
} from './api';
import type { ScheduleValues } from './rules';

// Sample data for the scheduling form's tests and stories (#48 F4, #49 F5,
// #50 C3, #51 G3).

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
    tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
    respond_by: '2026-10-23T12:00:00Z',
  },
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
    },
    {
      rank: 2,
      tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
      weekly_minutes: 150,
      daily_minutes: 60,
      weekly_limit_minutes: 900,
      daily_limit_minutes: 240,
      prefers_classroom: false,
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
    },
  ],
  eligible_count: 4,
};

export const NO_TUTOR_SUGGESTIONS: TutorSuggestionList = {
  items: [],
  eligible_count: 0,
};
