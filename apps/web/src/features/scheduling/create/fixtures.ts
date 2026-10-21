import type {
  ClassSessionCreated,
  Option,
  SchedulingConflict,
  SubjectOption,
} from './api';
import type { ScheduleValues } from './rules';

// Sample data for the scheduling form's tests and stories (#48 F4).

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
  warnings: [],
};

export const UNASSIGNED: ClassSessionCreated = {
  ...CREATED,
  state: 'unassigned',
  tutor: null,
  classroom: null,
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
