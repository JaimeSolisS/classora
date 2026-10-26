import type { FamilyStudents } from '@/features/my-schedule/api';
import type { Evaluation } from '../evaluations/api';
import { DRAFT, FINAL, STUDENT_ID, VOIDED } from '../evaluations/fixtures';
import type {
  AcademicHistory,
  AssignmentGradeEntry,
  HistoryEntry,
} from './api';

// Sample histories for the history pages' tests and stories (#308 G1, G2):
// the school year 2026-2027 with Física (two entries, two authors, one
// updated, one of a course) and Matemáticas, then "No period"; an entry of
// a kind this view doesn't know; and the family picker.

export { STUDENT_ID };
export const CHILD_ID = '00000000-0000-4000-8000-000000308f01';
export const SIBLING_ID = '00000000-0000-4000-8000-000000308f02';
export const ADULT_ID = '00000000-0000-4000-8000-000000308f03';

const PHYSICS = { id: '00000000-0000-4000-8000-000000308a01', name: 'Física' };
const MATH = {
  id: '00000000-0000-4000-8000-000000308a02',
  name: 'Matemáticas',
};

export function entry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    kind: 'evaluation',
    id: '00000000-0000-4000-8000-000000308901',
    evaluated_on: '2026-11-09',
    subject: PHYSICS,
    evaluation_kind: 'diagnostic',
    template_name: 'Diagnóstico de física',
    author: { display_name: 'Carmen Ruiz' },
    criteria: [
      {
        title: 'Comprensión',
        max_score: '10.00',
        score: '8.00',
        comment: 'Bien explicado.',
      },
      { title: 'Resolución', max_score: '10.00', score: '9.50', comment: null },
    ],
    score_total: '17.50',
    max_total: '20.00',
    score_percent: '87.50',
    feedback: 'Muy buen trabajo.',
    course: null,
    finalized_at: '2026-11-10T18:00:00Z',
    updated: false,
    ...overrides,
  };
}

export const FIRST = entry();
export const SECOND = entry({
  id: '00000000-0000-4000-8000-000000308902',
  evaluated_on: '2026-10-02',
  evaluation_kind: 'ongoing',
  template_name: 'Seguimiento de laboratorio',
  author: { display_name: 'Pedro Gómez' },
  updated: true,
  course: {
    cohort_name: 'Física intensiva',
    assessment_title: 'Práctica de laboratorio',
  },
  score_total: '4.00',
  max_total: '5.00',
  score_percent: '80.00',
  criteria: [
    { title: 'Orden', max_score: '5.00', score: '4.00', comment: null },
  ],
  feedback:
    'Un-comentario-muy-largo-sin-espacios-que-debe-partirse-en-pantallas-angostas-sin-desbordar-la-pagina-de-ninguna-manera',
});
export const THIRD = entry({
  id: '00000000-0000-4000-8000-000000308903',
  evaluated_on: '2026-09-15',
  subject: MATH,
  template_name: 'Diagnóstico de matemáticas',
  score_total: '6.00',
  max_total: '10.00',
  score_percent: '60.00',
  criteria: [
    { title: 'Fracciones', max_score: '10.00', score: '6.00', comment: null },
  ],
  feedback: null,
});
export const OUTSIDE = entry({
  id: '00000000-0000-4000-8000-000000308904',
  evaluated_on: '2026-07-20',
  template_name: 'Curso de verano',
  score_total: '9.00',
  max_total: '10.00',
  score_percent: '90.00',
  criteria: [
    { title: 'Constancia', max_score: '10.00', score: '9.00', comment: null },
  ],
});

/** An entry of a kind a later issue may add: skipped by this view. */
export const UNKNOWN_KIND = {
  ...entry({ id: '00000000-0000-4000-8000-000000308905' }),
  kind: 'progress_report',
  template_name: 'Informe desconocido',
} as unknown as HistoryEntry;

export const GROUPED: AcademicHistory = {
  student_id: STUDENT_ID,
  group_by: 'school_year',
  truncated: false,
  entries: [FIRST, SECOND, THIRD, OUTSIDE, UNKNOWN_KIND],
  groups: [
    {
      period: {
        id: '00000000-0000-4000-8000-000000308701',
        name: 'Ciclo 2026-2027',
        period_type: 'school_year',
        starts_on: '2026-08-01',
        ends_on: '2027-07-31',
      },
      subjects: [
        {
          subject: PHYSICS,
          count: 2,
          average_percent: '83.75',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [FIRST.id, SECOND.id],
        },
        {
          subject: MATH,
          count: 1,
          average_percent: '60.00',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [THIRD.id],
        },
      ],
    },
    {
      period: null,
      subjects: [
        {
          subject: PHYSICS,
          count: 1,
          average_percent: '90.00',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [OUTSIDE.id],
        },
      ],
    },
  ],
};

export const UNGROUPED: AcademicHistory = {
  ...GROUPED,
  group_by: 'none',
  groups: [
    {
      period: null,
      subjects: [
        {
          subject: PHYSICS,
          count: 3,
          average_percent: '85.83',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [FIRST.id, SECOND.id, OUTSIDE.id],
        },
        {
          subject: MATH,
          count: 1,
          average_percent: '60.00',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [THIRD.id],
        },
      ],
    },
  ],
};

export const EMPTY: AcademicHistory = {
  student_id: STUDENT_ID,
  group_by: 'school_year',
  truncated: false,
  entries: [],
  groups: [],
};

export const TRUNCATED: AcademicHistory = { ...GROUPED, truncated: true };

/** Física only (a subject filter). */
export const PHYSICS_ONLY: AcademicHistory = {
  ...GROUPED,
  entries: [FIRST, SECOND, OUTSIDE],
  groups: [
    { ...GROUPED.groups[0]!, subjects: [GROUPED.groups[0]!.subjects[0]!] },
    GROUPED.groups[1]!,
  ],
};

export const FAMILY_STUDENTS: FamilyStudents = {
  items: [
    {
      student_id: CHILD_ID,
      display_name: 'Lucía Pérez',
      relation: 'guardian',
      is_minor: true,
      schedule_access: 'allowed',
    },
    {
      student_id: SIBLING_ID,
      display_name: 'Andrés Pérez',
      relation: 'guardian',
      is_minor: true,
      schedule_access: 'allowed',
    },
    {
      student_id: ADULT_ID,
      display_name: 'Mateo Pérez',
      relation: 'guardian',
      is_minor: false,
      schedule_access: 'consent_required',
    },
  ],
};

export const SELF_ONLY: FamilyStudents = {
  items: [
    {
      student_id: STUDENT_ID,
      display_name: 'José Ñúñez Pérez',
      relation: 'self',
      is_minor: false,
      schedule_access: 'allowed',
    },
  ],
};

/** The working list: a draft, a final and a voided evaluation. */
export const WORKING_ITEMS: Evaluation[] = [DRAFT, FINAL, VOIDED];

// #313 F1: scored homework grades (#82) next to evaluations: Física has
// both, Química homework only.
const CHEMISTRY = {
  id: '00000000-0000-4000-8000-000000313a03',
  name: 'Química',
};

export function homework(
  overrides: Partial<AssignmentGradeEntry> = {},
): AssignmentGradeEntry {
  return {
    kind: 'assignment_grade',
    id: '00000000-0000-4000-8000-000000313901',
    graded_on: '2026-11-05',
    subject: PHYSICS,
    title: 'Problemas de cinemática',
    author: { display_name: 'Carmen Ruiz' },
    max_score: '10.00',
    score: '8.00',
    score_percent: '80.00',
    feedback: 'Revisa las unidades.',
    due_on: '2026-11-04',
    is_late: true,
    graded_at: '2026-11-05T18:00:00Z',
    updated: true,
    ...overrides,
  };
}

export const HOMEWORK = homework();
export const HOMEWORK_ONLY = homework({
  id: '00000000-0000-4000-8000-000000313902',
  graded_on: '2026-10-20',
  subject: CHEMISTRY,
  title: 'Tabla periódica',
  score: '9.50',
  score_percent: '95.00',
  feedback: null,
  due_on: '2026-10-19',
  is_late: false,
  updated: false,
});

export const MIXED: AcademicHistory = {
  student_id: STUDENT_ID,
  group_by: 'school_year',
  truncated: false,
  entries: [FIRST, HOMEWORK, SECOND, HOMEWORK_ONLY, THIRD, UNKNOWN_KIND],
  groups: [
    {
      period: GROUPED.groups[0]!.period,
      subjects: [
        {
          subject: PHYSICS,
          count: 2,
          average_percent: '83.75',
          assignment_count: 1,
          assignment_average_percent: '80.00',
          entry_ids: [FIRST.id, HOMEWORK.id, SECOND.id],
        },
        {
          subject: CHEMISTRY,
          count: 0,
          average_percent: null,
          assignment_count: 1,
          assignment_average_percent: '95.00',
          entry_ids: [HOMEWORK_ONLY.id],
        },
        {
          subject: MATH,
          count: 1,
          average_percent: '60.00',
          assignment_count: 0,
          assignment_average_percent: null,
          entry_ids: [THIRD.id],
        },
      ],
    },
  ],
};
