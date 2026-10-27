import { COHORT_ID, STUDENT_ANA, STUDENT_BRUNO } from '../fixtures';
import type {
  AssessmentCompletionItem,
  CohortCompletion,
  CompletionCriteria,
  StudentCompletion,
} from './api';

// Course completion responses (#323) in the merged #85 shape: decimals as
// 2-dp strings, minimums as integers.

const MODULE_INTRO = '01900000-0000-7000-8000-00000000c101';
const MODULE_ALGEBRA = '01900000-0000-7000-8000-00000000c102';
const EXAM_MIDTERM = '01900000-0000-7000-8000-00000000c201';
const EXAM_FINAL = '01900000-0000-7000-8000-00000000c202';
const QUIZ = '01900000-0000-7000-8000-00000000c203';

export const STUDENT_CELIA = {
  id: '01900000-0000-7000-8000-00000000c301',
  display_name: 'Celia Domínguez',
};
export const STUDENT_DARIO = {
  id: '01900000-0000-7000-8000-00000000c302',
  display_name: 'Darío Fernández de la Cruz y Montenegro Villaseñor',
};

export const CRITERIA: CompletionCriteria = {
  min_attendance_percent: 80,
  required_modules: [
    { id: MODULE_INTRO, title: 'Introducción' },
    { id: MODULE_ALGEBRA, title: 'Álgebra' },
  ],
  assessments: [
    {
      id: EXAM_MIDTERM,
      title: 'Parcial',
      module_id: MODULE_ALGEBRA,
      is_required: true,
      min_score_percent: 60,
    },
    {
      id: EXAM_FINAL,
      title: 'Examen final',
      module_id: null,
      is_required: true,
      min_score_percent: null,
    },
    {
      id: QUIZ,
      title: 'Cuestionario',
      module_id: null,
      is_required: false,
      min_score_percent: null,
    },
  ],
};

const NO_CRITERIA: CompletionCriteria = {
  min_attendance_percent: null,
  required_modules: [],
  assessments: [],
};

function item(
  id: string,
  overrides: Partial<AssessmentCompletionItem>,
): AssessmentCompletionItem {
  const criterion = CRITERIA.assessments.find((a) => a.id === id)!;
  return {
    assessment_id: id,
    title: criterion.title,
    is_required: criterion.is_required,
    min_score_percent: criterion.min_score_percent,
    status: 'not_evaluated',
    score_percent: null,
    evaluated_on: null,
    attempts: 0,
    ...overrides,
  };
}

/** Ana: in progress, 2 of 3 held (66.67 %), 1 awaiting closeout. */
export const ANA_COMPLETION: StudentCompletion = {
  student: { id: STUDENT_ANA.id, display_name: STUDENT_ANA.display_name },
  enrolled_at: '2030-12-10T05:30:00Z',
  status: 'in_progress',
  final: false,
  attendance: {
    status: 'pending',
    min_percent: 80,
    held: 3,
    attended: 2,
    absent: 1,
    remaining: 5,
    awaiting_closeout: 1,
    attendance_percent: '66.67',
  },
  assessments: {
    status: 'pending',
    items: [
      item(EXAM_MIDTERM, {
        status: 'passed',
        score_percent: '72.50',
        evaluated_on: '2031-01-20',
        attempts: 2,
      }),
      item(EXAM_FINAL, {}),
      item(QUIZ, {
        status: 'failed',
        score_percent: '40.00',
        evaluated_on: '2031-01-13',
        attempts: 1,
      }),
    ],
  },
  modules: {
    status: 'pending',
    items: [
      {
        module_id: MODULE_INTRO,
        title: 'Introducción',
        status: 'pending',
        basis: 'course_covered',
      },
      {
        module_id: MODULE_ALGEBRA,
        title: 'Álgebra',
        status: 'met',
        basis: 'assessments',
      },
    ],
  },
};

/** Bruno: completed. */
export const BRUNO_COMPLETION: StudentCompletion = {
  student: { id: STUDENT_BRUNO.id, display_name: STUDENT_BRUNO.display_name },
  enrolled_at: '2030-12-10T05:30:00Z',
  status: 'completed',
  final: true,
  attendance: {
    status: 'met',
    min_percent: 80,
    held: 8,
    attended: 8,
    absent: 0,
    remaining: 0,
    awaiting_closeout: 0,
    attendance_percent: '100.00',
  },
  assessments: {
    status: 'met',
    items: [
      item(EXAM_MIDTERM, {
        status: 'passed',
        score_percent: '90.00',
        evaluated_on: '2031-01-20',
        attempts: 1,
      }),
      item(EXAM_FINAL, {
        status: 'passed',
        score_percent: '85.00',
        evaluated_on: '2031-02-24',
        attempts: 1,
      }),
      item(QUIZ, {}),
    ],
  },
  modules: {
    status: 'met',
    items: [
      {
        module_id: MODULE_INTRO,
        title: 'Introducción',
        status: 'met',
        basis: 'course_covered',
      },
      {
        module_id: MODULE_ALGEBRA,
        title: 'Álgebra',
        status: 'met',
        basis: 'assessments',
      },
    ],
  },
};

/**
 * Celia: not completed. Her counts would read as complete (both required
 * assessments passed, both modules met), but the API's status says
 * otherwise (the attendance): the page shows the API's status, never one
 * of its own.
 */
export const CELIA_COMPLETION: StudentCompletion = {
  ...BRUNO_COMPLETION,
  student: STUDENT_CELIA,
  status: 'not_completed',
  attendance: {
    ...BRUNO_COMPLETION.attendance,
    status: 'not_met',
    attended: 5,
    absent: 3,
    attendance_percent: '62.50',
  },
};

/** Darío (a long name): nothing held yet, so no percentage. */
export const DARIO_COMPLETION: StudentCompletion = {
  ...ANA_COMPLETION,
  student: STUDENT_DARIO,
  attendance: {
    ...ANA_COMPLETION.attendance,
    held: 0,
    attended: 0,
    absent: 0,
    remaining: 8,
    awaiting_closeout: 0,
    attendance_percent: null,
  },
  assessments: {
    status: 'pending',
    items: ANA_COMPLETION.assessments.items.map((entry) =>
      item(entry.assessment_id, {}),
    ),
  },
};

export const COMPLETION: CohortCompletion = {
  cohort_id: COHORT_ID,
  cohort_name: 'Álgebra I, grupo A',
  cohort_status: 'in_progress',
  template: { name: 'Álgebra I', version: 2 },
  criteria: CRITERIA,
  students: [
    ANA_COMPLETION,
    BRUNO_COMPLETION,
    CELIA_COMPLETION,
    DARIO_COMPLETION,
  ],
};

export const ENDED_COMPLETION: CohortCompletion = {
  ...COMPLETION,
  cohort_status: 'ended',
};

export const EMPTY_COMPLETION: CohortCompletion = {
  ...COMPLETION,
  students: [],
};

/** A template with no criteria: attendance not required, nothing listed. */
export const NO_CRITERIA_COMPLETION: CohortCompletion = {
  ...COMPLETION,
  criteria: NO_CRITERIA,
  students: [
    {
      ...ANA_COMPLETION,
      attendance: {
        ...ANA_COMPLETION.attendance,
        status: 'not_required',
        min_percent: null,
        awaiting_closeout: 0,
      },
      assessments: { status: 'not_required', items: [] },
      modules: { status: 'not_required', items: [] },
    },
  ],
};

/** A student's own row. */
export const OWN_COMPLETION: CohortCompletion = {
  ...COMPLETION,
  students: [ANA_COMPLETION],
};

/** A guardian of two enrolled children. */
export const TWO_CHILDREN_COMPLETION: CohortCompletion = {
  ...COMPLETION,
  students: [ANA_COMPLETION, BRUNO_COMPLETION],
};
