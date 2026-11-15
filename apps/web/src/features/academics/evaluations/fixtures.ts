import type { Evaluation, EvaluationContext } from './api';

// Sample evaluations for the evaluation pages' tests and stories (#308 G1,
// G2). The student is José (students/fixtures); the class is on Monday
// 2026-11-09, 10:00 in America/Mexico_City.

export const STUDENT_ID = '00000000-0000-4000-8000-0000000e0001';
export const STUDENT_NAME = 'José Ñúñez Pérez';
export const SESSION_ID = '00000000-0000-4000-8000-000000308c01';
export const EVALUATION_ID = '00000000-0000-4000-8000-000000308d01';
export const ZONE = 'America/Mexico_City';
/** "Today" in the tests and stories. */
export const TODAY = '2026-11-12';
export const SCRIPT = '<script>alert("x")</script>';

const PHYSICS = {
  id: '00000000-0000-4000-8000-000000308a01',
  name: 'Física',
};

export const CONTEXT: EvaluationContext = {
  class_session_id: SESSION_ID,
  kind: 'individual',
  state: 'confirmed',
  starts_at: '2026-11-09T16:00:00Z',
  subject: PHYSICS,
  evaluable: true,
  participants: [{ student_id: STUDENT_ID, display_name: STUDENT_NAME }],
  course: null,
};

export const COURSE_CONTEXT: EvaluationContext = {
  ...CONTEXT,
  kind: 'course',
  course: {
    cohort_id: '00000000-0000-4000-8000-000000308e01',
    cohort_name: 'Física intensiva',
    assessments: [
      {
        id: '00000000-0000-4000-8000-000000308e02',
        title: 'Examen parcial',
        is_required: true,
        min_score_percent: 60,
      },
      {
        id: '00000000-0000-4000-8000-000000308e03',
        title: 'Práctica de laboratorio',
        is_required: false,
        min_score_percent: null,
      },
    ],
  },
};

export const DRAFT: Evaluation = {
  id: EVALUATION_ID,
  version: 1,
  status: 'draft',
  student: { id: STUDENT_ID, display_name: STUDENT_NAME },
  subject: PHYSICS,
  author: { display_name: 'Carmen Ruiz' },
  is_own: true,
  template: {
    id: '00000000-0000-4000-8000-000000308b01',
    name: 'Diagnóstico de física',
    version: 3,
  },
  kind: 'diagnostic',
  evaluated_on: '2026-11-09',
  class_session: { id: SESSION_ID, starts_at: '2026-11-09T16:00:00Z' },
  course: null,
  scores: [
    {
      position: 1,
      title: 'Comprensión',
      description: 'Explica los conceptos.',
      max_score: '10.00',
      score: '8.00',
      comment: null,
    },
    {
      position: 2,
      title: 'Resolución',
      description: null,
      max_score: '10.00',
      score: null,
      comment: null,
    },
  ],
  feedback: null,
  score_total: null,
  max_total: null,
  score_percent: null,
  finalized_at: null,
  amended_at: null,
  last_change_reason: null,
  voided: null,
  created_at: '2026-11-09T17:00:00Z',
  can_edit: true,
  can_finalize: true,
  can_amend: false,
  can_delete: true,
  can_void: false,
};

/** Every criterion scored (Finalize opens the dialog). */
export const COMPLETE_DRAFT: Evaluation = {
  ...DRAFT,
  scores: DRAFT.scores.map((score) => ({ ...score, score: '9.50' })),
  feedback: 'Buen avance.',
};

const FINAL_FIELDS = {
  status: 'final' as const,
  scores: [
    { ...DRAFT.scores[0]!, score: '8.00', comment: 'Bien explicado.' },
    { ...DRAFT.scores[1]!, score: '9.50', comment: null },
  ],
  feedback: 'Muy buen trabajo.',
  score_total: '17.50',
  max_total: '20.00',
  score_percent: '87.50',
  finalized_at: '2026-11-10T18:00:00Z',
  can_edit: false,
  can_finalize: false,
  can_delete: false,
};

/** The author's final evaluation: Amend. */
export const FINAL: Evaluation = {
  ...DRAFT,
  ...FINAL_FIELDS,
  version: 3,
  can_amend: true,
};

/** Amended once (the author sees the internal reason). */
export const AMENDED: Evaluation = {
  ...FINAL,
  version: 4,
  amended_at: '2026-11-11T18:00:00Z',
  last_change_reason: 'Corregí una suma.',
};

/** Another tutor's final evaluation: read-only. */
export const OTHER_TUTORS: Evaluation = {
  ...DRAFT,
  ...FINAL_FIELDS,
  version: 3,
  is_own: false,
  author: { display_name: 'Pedro Gómez' },
};

/** A former author (the student is no longer assigned): read-only. */
export const FORMER_AUTHOR: Evaluation = { ...FINAL, can_amend: false };

/** Staff with evaluation.manage: Void. */
export const STAFF_FINAL: Evaluation = {
  ...OTHER_TUTORS,
  can_void: true,
  last_change_reason: 'Corregí una suma.',
  amended_at: '2026-11-11T18:00:00Z',
};

export const VOIDED: Evaluation = {
  ...STAFF_FINAL,
  version: 5,
  can_void: false,
  voided: {
    at: '2026-11-12T15:00:00Z',
    by: { display_name: 'Demo Coordinator' },
    reason: 'Se registró al estudiante equivocado.',
  },
};

/** Free text with markup in every field (it shows literally). */
export const SCRIPTED: Evaluation = {
  ...STAFF_FINAL,
  feedback: SCRIPT,
  last_change_reason: SCRIPT,
  scores: [
    { ...FINAL_FIELDS.scores[0]!, comment: SCRIPT },
    FINAL_FIELDS.scores[1]!,
  ],
};
