import { CHILD_ID } from '@/features/academics/history/fixtures';
import type {
  ProgressGradeMonth,
  SessionLogItem,
  SessionLogPage,
  StudentProgress,
} from './api';

// Sample progress for the progress pages' tests and stories (#322 G1, G2),
// in the merged #84 shape: decimals as 2-dp strings, plan progress as a
// whole-number string, every month of the range in attendance_by_month,
// grades.by_month sparse (month, then subject name).

export const STUDENT = CHILD_ID;
export const MATH = {
  id: '00000000-0000-4000-8000-000000322a01',
  name: 'Matemáticas',
};
export const PHYSICS = {
  id: '00000000-0000-4000-8000-000000322a02',
  name: 'Física',
};
export const ENGLISH = {
  id: '00000000-0000-4000-8000-000000322a03',
  name: 'Inglés',
};
const CHEMISTRY = {
  id: '00000000-0000-4000-8000-000000322a04',
  name: 'Química',
};
const HISTORY = {
  id: '00000000-0000-4000-8000-000000322a05',
  name: 'Historia',
};
/** A subject with classes only (no grades, homework or plans): AM1. */
export const MUSIC = {
  id: '00000000-0000-4000-8000-000000322a06',
  name: 'Música',
};

export const SCRIPT = '<script>alert(1)</script>';

function grade(
  month: string,
  subject: { id: string; name: string },
  overrides: Partial<ProgressGradeMonth> = {},
): ProgressGradeMonth {
  return {
    month,
    subject,
    evaluation_count: 1,
    evaluation_average_percent: '87.50',
    assignment_count: 0,
    assignment_average_percent: null,
    ...overrides,
  };
}

/** Three months (2026-08 … 2026-10), two graded subjects, two plans. */
export const PROGRESS: StudentProgress = {
  student_id: STUDENT,
  from: '2026-08-01',
  to: '2026-10-26',
  subject: null,
  subjects: [PHYSICS, MATH, MUSIC],
  attendance: {
    sessions: 9,
    present: 5,
    late: 1,
    absent: 2,
    not_recorded: 1,
    attendance_percent: '66.67',
  },
  attendance_by_month: [
    {
      month: '2026-08',
      sessions: 2,
      present: 1,
      late: 0,
      absent: 1,
      not_recorded: 0,
    },
    {
      month: '2026-09',
      sessions: 3,
      present: 2,
      late: 1,
      absent: 0,
      not_recorded: 0,
    },
    {
      month: '2026-10',
      sessions: 4,
      present: 2,
      late: 0,
      absent: 1,
      not_recorded: 1,
    },
  ],
  grades: {
    evaluation_count: 3,
    evaluation_average_percent: '84.17',
    assignment_count: 2,
    assignment_average_percent: '75.00',
    by_subject: [],
    by_month: [
      grade('2026-08', PHYSICS),
      grade('2026-09', MATH, {
        evaluation_average_percent: '70.00',
        assignment_count: 1,
        assignment_average_percent: '70.00',
      }),
      grade('2026-10', MATH, {
        evaluation_count: 0,
        evaluation_average_percent: null,
        assignment_count: 1,
        assignment_average_percent: '80.00',
      }),
      grade('2026-10', PHYSICS, { evaluation_average_percent: '95.00' }),
    ],
  },
  homework: {
    pending: 1,
    missing: 1,
    submitted: 1,
    graded: 2,
    late: 1,
    completion_percent: '75.00',
  },
  plans: {
    active: 1,
    completed: 1,
    objectives_complete: 3,
    objectives_total: 5,
    milestones_overdue: 1,
    items: [
      {
        id: '00000000-0000-4000-8000-000000322b01',
        title: 'Fracciones y decimales',
        subject: MATH,
        status: 'active',
        progress_percent: '50',
        objectives_complete: 1,
        objectives_total: 3,
        milestones_done: 2,
        milestones_total: 4,
        milestones_overdue: 1,
        target_on: '2026-12-15',
      },
      {
        id: '00000000-0000-4000-8000-000000322b02',
        title: 'Cinemática',
        subject: PHYSICS,
        status: 'completed',
        progress_percent: '100',
        objectives_complete: 2,
        objectives_total: 2,
        milestones_done: 3,
        milestones_total: 3,
        milestones_overdue: 0,
        target_on: null,
      },
    ],
  },
};

/** Nothing in the range: every percentage null, every count 0. */
export const EMPTY_PROGRESS: StudentProgress = {
  ...PROGRESS,
  subjects: [],
  attendance: {
    sessions: 0,
    present: 0,
    late: 0,
    absent: 0,
    not_recorded: 0,
    attendance_percent: null,
  },
  attendance_by_month: PROGRESS.attendance_by_month.map((month) => ({
    ...month,
    sessions: 0,
    present: 0,
    late: 0,
    absent: 0,
    not_recorded: 0,
  })),
  grades: {
    evaluation_count: 0,
    evaluation_average_percent: null,
    assignment_count: 0,
    assignment_average_percent: null,
    by_subject: [],
    by_month: [],
  },
  homework: {
    pending: 0,
    missing: 0,
    submitted: 0,
    graded: 0,
    late: 0,
    completion_percent: null,
  },
  plans: {
    active: 0,
    completed: 0,
    objectives_complete: 0,
    objectives_total: 0,
    milestones_overdue: 0,
    items: [],
  },
};

/** One month, one class, one grade. */
export const SINGLE_POINT: StudentProgress = {
  ...PROGRESS,
  from: '2026-10-01',
  attendance: {
    sessions: 1,
    present: 1,
    late: 0,
    absent: 0,
    not_recorded: 0,
    attendance_percent: '100.00',
  },
  attendance_by_month: [
    {
      month: '2026-10',
      sessions: 1,
      present: 1,
      late: 0,
      absent: 0,
      not_recorded: 0,
    },
  ],
  grades: {
    evaluation_count: 1,
    evaluation_average_percent: '90.00',
    assignment_count: 0,
    assignment_average_percent: null,
    by_subject: [],
    by_month: [grade('2026-10', MATH, { evaluation_average_percent: '90.00' })],
  },
};

/** Five graded subjects: the chart asks for one (more than 4). */
export const MANY_SUBJECTS: StudentProgress = {
  ...PROGRESS,
  subjects: [PHYSICS, HISTORY, ENGLISH, MATH, CHEMISTRY],
  grades: {
    ...PROGRESS.grades,
    by_month: [PHYSICS, HISTORY, ENGLISH, MATH, CHEMISTRY].map((subject) =>
      grade('2026-10', subject),
    ),
  },
};

export function logItem(
  overrides: Partial<SessionLogItem> = {},
): SessionLogItem {
  return {
    class_session_id: '00000000-0000-4000-8000-000000322c01',
    starts_at: '2026-10-20T22:00:00Z',
    subject: MATH,
    session_kind: 'individual',
    tutor: { display_name: 'Carmen Ruiz' },
    attendance: 'present',
    summary: {
      topics: 'Fracciones equivalentes',
      performance: 'Participó y resolvió los ejercicios.',
      homework: null,
      next_steps: 'Suma de fracciones.',
    },
    published_note_count: 2,
    ...overrides,
  };
}

export const LOG_ITEMS: SessionLogItem[] = [
  logItem(),
  logItem({
    class_session_id: '00000000-0000-4000-8000-000000322c02',
    starts_at: '2026-10-13T22:00:00Z',
    session_kind: 'group',
    attendance: 'late',
    summary: null,
    published_note_count: 0,
  }),
  logItem({
    class_session_id: '00000000-0000-4000-8000-000000322c03',
    starts_at: '2026-10-06T22:00:00Z',
    session_kind: 'course',
    attendance: 'absent',
    tutor: null,
    summary: {
      topics: SCRIPT,
      performance: null,
      homework: null,
      next_steps: null,
    },
    published_note_count: 0,
  }),
  logItem({
    class_session_id: '00000000-0000-4000-8000-000000322c04',
    starts_at: '2026-09-29T22:00:00Z',
    attendance: null,
    summary: null,
    published_note_count: 0,
  }),
];

export const LOG_PAGE: SessionLogPage = { items: LOG_ITEMS, next_cursor: null };

export const LOG_FIRST_PAGE: SessionLogPage = {
  items: LOG_ITEMS.slice(0, 2),
  next_cursor: 'cursor-2',
};
export const LOG_SECOND_PAGE: SessionLogPage = {
  items: LOG_ITEMS.slice(2),
  next_cursor: null,
};

export const EMPTY_LOG: SessionLogPage = { items: [], next_cursor: null };

export const NOTES_PAGE = {
  items: [
    {
      id: '00000000-0000-4000-8000-000000322d01',
      class_session_id: LOG_ITEMS[0]!.class_session_id,
      session_starts_at: LOG_ITEMS[0]!.starts_at,
      subject_name: MATH.name,
      author: { display_name: 'Carmen Ruiz' },
      body: `Muy buena participación. ${SCRIPT}`,
      published_at: '2026-10-21T01:00:00Z',
    },
  ],
  next_cursor: null,
};
