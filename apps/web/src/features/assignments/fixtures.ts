import type { SessionUserBody } from '@/features/auth/session';
import { JOSE_ID } from '@/features/students/fixtures';
import { TEST_USER } from '@/test/api-stub';
import type {
  AssignmentList,
  AssignmentSummary,
  AssignmentView,
  RosterEntry,
  SessionAttendance,
  StudentAssignment,
  StudentAssignmentList,
  StudentListPage,
  StudentSubmission,
} from './api';

// Sample homework for the tests and stories (#313 G1, G2): a roster with
// every state (pending, missing, handed in late with a link, recorded by
// the tutor, graded and updated with the internal reason), its read-only
// and feedback-only variants, the working list, one student's homework
// (pending, missing, handed in, closed, graded), a class's attendance and
// the tutor's students. The deadlines of open items are in 2030 and the
// closed ones in 2026, so "now" never changes what a test sees.

export const ASSIGNMENT_ID = '00000000-0000-4000-8000-000000313a01';
export const OTHER_ASSIGNMENT_ID = '00000000-0000-4000-8000-000000313a02';
export const STUDENT_ID = JOSE_ID;
export const STUDENT_NAME = 'José Ñúñez Pérez';
export const ANA_ID = '00000000-0000-4000-8000-000000313b02';
export const LUIS_ID = '00000000-0000-4000-8000-000000313b03';
export const MARTA_ID = '00000000-0000-4000-8000-000000313b04';
export const PEDRO_ID = '00000000-0000-4000-8000-000000313b05';
export const SESSION_ID = '00000000-0000-4000-8000-000000313c01';
export const MATHS = {
  id: '00000000-0000-4000-8000-000000313d01',
  name: 'Matemáticas',
};
export const PHYSICS = {
  id: '00000000-0000-4000-8000-000000313d02',
  name: 'Física',
};
export const SCRIPT = '<script>alert("x")</script>';
export const LONG_URL = `https://example.com/${'a'.repeat(180)}`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
export const REVIEWER = user(
  ['academic_coordinator'],
  ['student.view', 'assignment.review', 'evaluation.manage'],
);
export const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'schedule.view', 'course.view'],
);
export const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
export const STUDENT = user(['student'], ['student.view']);
export const TUTOR_GUARDIAN = user(
  ['tutor', 'guardian'],
  ['student.view', 'tutor.view', 'guardian.view'],
);

export function rosterRow(overrides: Partial<RosterEntry> = {}): RosterEntry {
  return {
    student: { id: STUDENT_ID, display_name: STUDENT_NAME },
    version: 1,
    state: 'pending',
    is_late: false,
    submitted_at: null,
    submitted_via: null,
    text: null,
    url: null,
    score: null,
    score_percent: null,
    feedback: null,
    graded_at: null,
    grade_amended_at: null,
    last_change_reason: null,
    can_record_submission: true,
    can_grade: false,
    ...overrides,
  };
}

export const PENDING_ROW = rosterRow();
export const LATE_ROW = rosterRow({
  student: { id: ANA_ID, display_name: 'Ana García' },
  version: 2,
  state: 'submitted',
  is_late: true,
  submitted_at: '2030-11-13T01:00:00Z',
  submitted_via: 'student',
  text: 'Mis respuestas:\n1) 3/4\n2) 5/6',
  url: 'https://example.com/fracciones',
  can_record_submission: false,
  can_grade: true,
});
export const GRADED_ROW = rosterRow({
  student: { id: LUIS_ID, display_name: 'Luis Ortega' },
  version: 4,
  state: 'graded',
  submitted_at: '2030-11-11T18:00:00Z',
  submitted_via: 'student',
  text: 'Listo.',
  score: '8.00',
  score_percent: '80.00',
  feedback: 'Buen trabajo.',
  graded_at: '2030-11-12T18:00:00Z',
  grade_amended_at: '2030-11-13T18:00:00Z',
  last_change_reason: 'Revisé la pregunta 2.',
  can_record_submission: false,
  can_grade: true,
});
export const MISSING_ROW = rosterRow({
  student: { id: MARTA_ID, display_name: 'Marta Ruiz' },
  state: 'missing',
});
export const RECORDED_ROW = rosterRow({
  student: { id: PEDRO_ID, display_name: 'Pedro Gómez' },
  version: 2,
  state: 'submitted',
  submitted_at: '2030-11-12T15:00:00Z',
  submitted_via: 'tutor',
  text: 'Entregó en papel.',
  can_record_submission: false,
  can_grade: true,
});

export const ROSTER: RosterEntry[] = [
  LATE_ROW,
  PENDING_ROW,
  GRADED_ROW,
  MISSING_ROW,
  RECORDED_ROW,
];

export const ASSIGNMENT: AssignmentView = {
  id: ASSIGNMENT_ID,
  version: 3,
  subject: MATHS,
  title: 'Fracciones equivalentes',
  instructions: 'Resuelve los ejercicios 1 a 5.\nMuestra tu procedimiento.',
  due_on: '2030-11-12',
  due_time: '16:00:00',
  deadline_at: '2030-11-12T22:00:00Z',
  max_score: '10.00',
  class_session: { id: SESSION_ID, starts_at: '2030-11-10T22:00:00Z' },
  creator: { display_name: 'Carmen Ruiz' },
  is_own: true,
  created_at: '2030-11-10T23:00:00Z',
  updated_at: '2030-11-10T23:00:00Z',
  counts: {
    recipients: 5,
    pending: 1,
    missing: 1,
    submitted: 2,
    late: 1,
    graded: 1,
  },
  can_edit: true,
  can_delete: false,
  roster: ROSTER,
};

/** A fresh one: nothing handed in, so it can be deleted. */
export const FRESH: AssignmentView = {
  ...ASSIGNMENT,
  class_session: null,
  due_time: null,
  deadline_at: '2030-11-13T05:59:59.999999Z',
  counts: {
    recipients: 2,
    pending: 2,
    missing: 0,
    submitted: 0,
    late: 0,
    graded: 0,
  },
  can_delete: true,
  roster: [
    PENDING_ROW,
    rosterRow({ student: { id: ANA_ID, display_name: 'Ana García' } }),
  ],
};

const READ_ONLY_FLAGS = {
  can_record_submission: false,
  can_grade: false,
};

/** An assignment.review holder (or a former creator): no action at all. */
export const READ_ONLY: AssignmentView = {
  ...ASSIGNMENT,
  is_own: false,
  can_edit: false,
  can_delete: false,
  roster: ROSTER.map((row) => ({ ...row, ...READ_ONLY_FLAGS })),
};

/** Feedback only: no max score, a feedback-only grade. */
export const FEEDBACK_ONLY: AssignmentView = {
  ...ASSIGNMENT,
  max_score: null,
  counts: { ...ASSIGNMENT.counts, submitted: 1, graded: 1, pending: 3 },
  roster: [
    { ...LATE_ROW, is_late: false },
    {
      ...GRADED_ROW,
      score: null,
      score_percent: null,
      grade_amended_at: null,
      last_change_reason: null,
    },
  ],
};

/** Free texts with markup and an unsafe stored link (shown as text). */
export const SCRIPTED: AssignmentView = {
  ...ASSIGNMENT,
  title: SCRIPT,
  instructions: SCRIPT,
  roster: [
    {
      ...LATE_ROW,
      text: SCRIPT,
      url: 'javascript:alert(1)',
    },
    { ...GRADED_ROW, feedback: SCRIPT, last_change_reason: SCRIPT },
  ],
};

export function summaryOf(
  view: AssignmentView,
  overrides: Partial<AssignmentSummary> = {},
): AssignmentSummary {
  const summary: AssignmentSummary & { roster?: unknown } = {
    ...view,
    ...overrides,
  };
  delete summary.roster;
  return summary;
}

export const WORKING_LIST: AssignmentList = {
  items: [
    summaryOf(ASSIGNMENT),
    summaryOf(FRESH, {
      id: OTHER_ASSIGNMENT_ID,
      title: 'Lectura de física',
      subject: PHYSICS,
      is_own: false,
      creator: { display_name: 'Pedro Gómez' },
    }),
  ],
  next_cursor: 'cursor-2',
};

export const SECOND_PAGE: AssignmentList = {
  items: [
    summaryOf(FRESH, {
      id: '00000000-0000-4000-8000-000000313a03',
      title: 'Repaso final',
    }),
  ],
  next_cursor: null,
};

// ---------------------------------------------------- one student's homework

export function submission(
  overrides: Partial<StudentSubmission> = {},
): StudentSubmission {
  return {
    state: 'pending',
    is_late: false,
    submitted_at: null,
    submitted_via: null,
    text: null,
    url: null,
    score: null,
    score_percent: null,
    feedback: null,
    graded_at: null,
    updated: false,
    version: 1,
    ...overrides,
  };
}

export function studentItem(
  overrides: Partial<StudentAssignment> = {},
): StudentAssignment {
  return {
    id: ASSIGNMENT_ID,
    subject: MATHS,
    title: 'Fracciones equivalentes',
    instructions: 'Resuelve los ejercicios 1 a 5.',
    due_on: '2030-11-12',
    due_time: '16:00:00',
    deadline_at: '2030-11-12T22:00:00Z',
    max_score: '10.00',
    creator: { display_name: 'Carmen Ruiz' },
    class_session: { starts_at: '2030-11-10T22:00:00Z' },
    submission: submission(),
    can_submit: true,
    can_grade: false,
    ...overrides,
  };
}

/** Pending, nothing handed in yet. */
export const PENDING_ITEM = studentItem();
/** Handed in before the deadline: may be replaced. */
export const SUBMITTED_ITEM = studentItem({
  submission: submission({
    state: 'submitted',
    submitted_at: '2030-11-11T18:00:00Z',
    submitted_via: 'student',
    text: 'Mi respuesta',
    url: 'https://example.com/mi-trabajo',
    version: 2,
  }),
});
/** Missing: the deadline passed with nothing handed in (late allowed). */
export const MISSING_ITEM = studentItem({
  id: OTHER_ASSIGNMENT_ID,
  title: 'Lectura de física',
  subject: PHYSICS,
  due_on: '2026-10-01',
  due_time: null,
  deadline_at: '2026-10-02T05:59:59.999999Z',
  submission: submission({ state: 'missing' }),
});
/** Handed in late, after the deadline: closed. */
export const CLOSED_ITEM = studentItem({
  due_on: '2026-10-01',
  due_time: '16:00:00',
  deadline_at: '2026-10-01T22:00:00Z',
  submission: submission({
    state: 'submitted',
    is_late: true,
    submitted_at: '2026-10-01T23:00:00Z',
    submitted_via: 'student',
    text: 'Tarde',
    version: 2,
  }),
  can_submit: false,
});
/** Graded and updated (a re-grade: the reason never reaches families). */
export const GRADED_ITEM = studentItem({
  id: '00000000-0000-4000-8000-000000313a03',
  title: 'Repaso final',
  submission: submission({
    state: 'graded',
    submitted_at: '2030-11-11T18:00:00Z',
    submitted_via: 'tutor',
    text: 'Entregó en papel.',
    score: '8.00',
    score_percent: '80.00',
    feedback: 'Buen trabajo.',
    graded_at: '2030-11-12T18:00:00Z',
    updated: true,
    version: 4,
  }),
  can_submit: false,
});
/** Graded with feedback only. */
export const FEEDBACK_ITEM = studentItem({
  max_score: null,
  submission: submission({
    state: 'graded',
    submitted_at: '2030-11-11T18:00:00Z',
    submitted_via: 'student',
    text: 'Listo',
    feedback: 'Sigue así.',
    graded_at: '2030-11-12T18:00:00Z',
    version: 3,
  }),
  can_submit: false,
});

export const STUDENT_LIST: StudentAssignmentList = {
  items: [PENDING_ITEM, MISSING_ITEM, GRADED_ITEM],
  summary: { pending: 1, missing: 1, submitted: 0, graded: 1 },
  next_cursor: null,
};

// -------------------------------------------------------------- the form

export const SESSION: SessionAttendance = {
  class_session_id: SESSION_ID,
  version: 1,
  kind: 'group',
  state: 'confirmed',
  starts_at: '2030-11-10T22:00:00Z',
  ends_at: '2030-11-10T23:00:00Z',
  subject: {
    id: MATHS.id,
    name: MATHS.name,
    education_level_name: 'Secundaria',
  },
  can_mark: false,
  can_correct: false,
  block: null,
  items: [
    {
      student: { id: STUDENT_ID, display_name: STUDENT_NAME },
      status: null,
      marked_by: null,
      marked_at: null,
      corrected: false,
      corrections: [],
    },
    {
      student: { id: ANA_ID, display_name: 'Ana García' },
      status: null,
      marked_by: null,
      marked_at: null,
      corrected: false,
      corrections: [],
    },
  ],
};

const listItem = (id: string, given: string, family: string) => ({
  id,
  given_names: given,
  family_names: family,
  is_active: true,
  is_minor: true,
  school_grade: null,
  education_level: null,
});

export const PICKER_PAGE: StudentListPage = {
  items: [
    listItem(STUDENT_ID, 'José', 'Ñúñez Pérez'),
    listItem(ANA_ID, 'Ana', 'García'),
    listItem(MARTA_ID, 'Marta', 'Ruiz'),
  ],
  total: 3,
  limit: 100,
  offset: 0,
};

export const SUBJECTS_PAGE = {
  items: [
    {
      id: MATHS.id,
      name: MATHS.name,
      education_level_name: 'Secundaria',
      is_active: true,
    },
    {
      id: PHYSICS.id,
      name: PHYSICS.name,
      education_level_name: 'Secundaria',
      is_active: true,
    },
  ],
  total: 2,
  limit: 100,
  offset: 0,
};
