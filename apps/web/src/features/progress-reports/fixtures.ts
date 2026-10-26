import type { SessionUserBody } from '@/features/auth/session';
import { JOSE_ID } from '@/features/students/fixtures';
import { TEST_USER } from '@/test/api-stub';
import type {
  ApprovedReport,
  ApprovedReportItem,
  ApprovedReportPage,
  ProgressReport,
  ProgressReportListItem,
  ProgressReportPage,
  ReportFacts,
} from './api';

// Fixtures of the progress report pages (#315 H1, H2): reports in every
// status with the flags each audience gets, facts (full, empty, truncated,
// with markup), the list bodies (with the AM2 hints) and the users by role.

export const STUDENT_ID = JOSE_ID;
export const STUDENT_NAME = 'José Ñúñez Pérez';
export const REPORT_ID = '00000000-0000-4000-8000-000000315a01';
export const OTHER_REPORT_ID = '00000000-0000-4000-8000-000000315a02';
export const PHYSICS = {
  id: '00000000-0000-4000-8000-000000315501',
  name: 'Física',
};
export const MATHS = {
  id: '00000000-0000-4000-8000-000000315502',
  name: 'Matemáticas',
};
export const SCRIPT = '<script>alert("x")</script>';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
export const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'progress_report.review'],
);
export const ADMIN_TUTOR = user(
  ['administrator', 'tutor'],
  ['student.view', 'tutor.view', 'progress_report.review'],
);
export const OWNER_TUTOR = user(
  ['owner', 'tutor'],
  ['student.view', 'tutor.view', 'progress_report.review'],
);
export const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'schedule.view'],
);
export const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
export const STUDENT = user(['student'], ['student.view']);
export const TUTOR_GUARDIAN = user(
  ['tutor', 'guardian'],
  ['student.view', 'tutor.view', 'guardian.view'],
);

export const FACTS: ReportFacts = {
  schema_version: 1,
  period: { start: '2026-10-01', end: '2026-10-24' },
  subject: null,
  attendance: { sessions: 6, present: 4, late: 1, absent: 1, not_recorded: 0 },
  evaluations: {
    count: 2,
    average_percent: '80.25',
    by_subject: [
      { subject: PHYSICS, count: 1, average_percent: '87.5' },
      { subject: MATHS, count: 1, average_percent: null },
    ],
    entries: [
      {
        evaluated_on: '2026-10-20',
        subject: PHYSICS,
        evaluation_kind: 'diagnostic',
        template_name: 'Diagnóstico inicial',
        score_percent: '87.50',
      },
      {
        evaluated_on: '2026-10-06',
        subject: MATHS,
        evaluation_kind: 'ongoing',
        template_name: 'Seguimiento',
        score_percent: '73.00',
      },
    ],
    truncated: false,
  },
  assignments: {
    count: 4,
    pending: 1,
    missing: 1,
    submitted: 1,
    graded: 1,
    late: 1,
    average_percent: '90.00',
  },
  learning_plans: [
    {
      title: 'Lectura comprensiva',
      subject: PHYSICS,
      status: 'active',
      progress_percent: '67',
      objectives_completed: 1,
      objectives_total: 2,
      milestones_done: 2,
      milestones_total: 3,
    },
  ],
  learning_plans_truncated: false,
};

export const EMPTY_FACTS: ReportFacts = {
  ...FACTS,
  attendance: { sessions: 0, present: 0, late: 0, absent: 0, not_recorded: 0 },
  evaluations: {
    count: 0,
    average_percent: null,
    by_subject: [],
    entries: [],
    truncated: false,
  },
  assignments: {
    count: 0,
    pending: 0,
    missing: 0,
    submitted: 0,
    graded: 0,
    late: 0,
    average_percent: null,
  },
  learning_plans: [],
};

/** Averages unknown although there is data: never shown as 0 %. */
export const NULL_AVERAGE_FACTS: ReportFacts = {
  ...FACTS,
  evaluations: { ...FACTS.evaluations, average_percent: null },
  assignments: { ...FACTS.assignments, average_percent: null },
};

export const TRUNCATED_FACTS: ReportFacts = {
  ...FACTS,
  evaluations: { ...FACTS.evaluations, truncated: true },
  learning_plans_truncated: true,
};

export const SCRIPT_FACTS: ReportFacts = {
  ...FACTS,
  evaluations: {
    ...FACTS.evaluations,
    entries: [{ ...FACTS.evaluations.entries[0]!, template_name: SCRIPT }],
  },
  learning_plans: [{ ...FACTS.learning_plans[0]!, title: SCRIPT }],
};

const NO_FLAGS = {
  can_edit: false,
  can_submit: false,
  can_delete: false,
  can_approve: false,
  can_request_changes: false,
  can_retract: false,
};

const BASE: ProgressReport = {
  id: REPORT_ID,
  version: 1,
  status: 'draft',
  student: { id: STUDENT_ID, display_name: STUDENT_NAME },
  author: { display_name: 'Carmen Ruiz' },
  is_own: true,
  kind: 'on_demand',
  period_start: '2026-10-01',
  period_end: '2026-10-24',
  subject: null,
  summary: null,
  strengths: null,
  areas_for_improvement: null,
  next_steps: null,
  facts: null,
  facts_computed_at: null,
  facts_preview: FACTS,
  submitted_at: null,
  changes_requested: null,
  approved: null,
  retracted: null,
  created_at: '2026-10-24T15:00:00Z',
  updated_at: '2026-10-24T15:00:00Z',
  ...NO_FLAGS,
};

/** The author's draft: edit, submit, delete. */
export const DRAFT: ProgressReport = {
  ...BASE,
  summary: 'Buen avance en física.',
  can_edit: true,
  can_submit: true,
  can_delete: true,
};

export const EMPTY_DRAFT: ProgressReport = {
  ...DRAFT,
  summary: null,
};

const SUBMITTED_AT = '2026-10-24T17:00:00Z';

/** The author's submitted report: read only. */
export const SUBMITTED: ProgressReport = {
  ...BASE,
  version: 2,
  status: 'submitted',
  summary: 'Buen avance en física.',
  strengths: 'Constancia.',
  facts: FACTS,
  facts_computed_at: SUBMITTED_AT,
  facts_preview: null,
  submitted_at: SUBMITTED_AT,
};

/** The same report as a reviewer (not the author) sees it. */
export const REVIEW: ProgressReport = {
  ...SUBMITTED,
  is_own: false,
  can_approve: true,
  can_request_changes: true,
  can_retract: true,
};

const CHANGES = {
  at: '2026-10-25T15:00:00Z',
  by: { display_name: 'Laura Méndez' },
  reason: 'Añade los próximos pasos.',
};

/** Sent back to the author: edit with the banner, resubmit. */
export const CHANGES_REQUESTED: ProgressReport = {
  ...SUBMITTED,
  version: 3,
  status: 'changes_requested',
  facts_preview: FACTS,
  changes_requested: CHANGES,
  can_edit: true,
  can_submit: true,
  can_delete: true,
};

const APPROVAL = {
  at: '2026-10-26T15:00:00Z',
  by: { display_name: 'Laura Méndez' },
};

/** Approved, as the author reads it. */
export const APPROVED: ProgressReport = {
  ...SUBMITTED,
  version: 4,
  status: 'approved_for_family',
  approved: APPROVAL,
};

/** Approved, as a reviewer sees it (retract only). */
export const APPROVED_REVIEW: ProgressReport = {
  ...APPROVED,
  is_own: false,
  can_retract: true,
};

/** Retracted, as the author reads it. */
export const RETRACTED: ProgressReport = {
  ...APPROVED,
  version: 5,
  status: 'retracted',
  retracted: {
    at: '2026-10-27T15:00:00Z',
    by: { display_name: 'Laura Méndez' },
    reason: 'Datos de otra alumna.',
  },
};

/** The author no longer teaches the student: every flag false. */
export const FORMER_AUTHOR: ProgressReport = {
  ...DRAFT,
  ...NO_FLAGS,
};

/** A reviewer's own submitted report (the API refuses self-review). */
export const OWN_SUBMITTED_REVIEWER: ProgressReport = {
  ...SUBMITTED,
  is_own: true,
};

export const LONG_TEXT = `${'Palabra'.repeat(120)} ${'a'.repeat(300)}`;

export function workingItem(
  report: ProgressReport,
  overrides: Partial<ProgressReportListItem> = {},
): ProgressReportListItem {
  return {
    id: report.id,
    version: report.version,
    status: report.status,
    student: report.student,
    author: report.author,
    kind: report.kind,
    period_start: report.period_start,
    period_end: report.period_end,
    subject: report.subject,
    submitted_at: report.submitted_at,
    approved_at: report.approved?.at ?? null,
    ...overrides,
  };
}

export const QUEUE_PAGE: ProgressReportPage = {
  items: [
    workingItem(REVIEW),
    workingItem(REVIEW, {
      id: OTHER_REPORT_ID,
      kind: 'weekly',
      period_start: '2026-10-12',
      period_end: '2026-10-18',
      subject: PHYSICS,
      student: { id: STUDENT_ID, display_name: 'Ana García' },
    }),
  ],
  next_cursor: null,
};

export const TUTOR_PAGE: ProgressReportPage = {
  items: [
    workingItem(DRAFT),
    workingItem(APPROVED, {
      id: OTHER_REPORT_ID,
      kind: 'monthly',
      period_start: '2026-09-01',
      period_end: '2026-09-30',
    }),
  ],
  next_cursor: null,
};

export const EMPTY_WORKING: ProgressReportPage = {
  items: [],
  next_cursor: null,
};

export const APPROVED_ITEM: ApprovedReportItem = {
  id: REPORT_ID,
  kind: 'monthly',
  period_start: '2026-09-01',
  period_end: '2026-09-30',
  subject: PHYSICS,
  author: { display_name: 'Carmen Ruiz' },
  approved_at: '2026-10-02T15:00:00Z',
};

export const APPROVED_PAGE: ApprovedReportPage = {
  items: [APPROVED_ITEM],
  next_cursor: null,
  can_create: true,
  creatable_subjects: [PHYSICS, MATHS],
};

export const APPROVED_PAGE_NO_CREATE: ApprovedReportPage = {
  ...APPROVED_PAGE,
  can_create: false,
  creatable_subjects: [],
};

export const EMPTY_APPROVED: ApprovedReportPage = {
  items: [],
  next_cursor: null,
  can_create: true,
  creatable_subjects: [PHYSICS],
};

export const APPROVED_REPORT: ApprovedReport = {
  id: REPORT_ID,
  student_id: STUDENT_ID,
  kind: 'monthly',
  period_start: '2026-09-01',
  period_end: '2026-09-30',
  subject: PHYSICS,
  author: { display_name: 'Carmen Ruiz' },
  summary: 'Buen avance en física.',
  strengths: 'Constancia.',
  areas_for_improvement: null,
  next_steps: 'Practicar problemas.',
  facts: FACTS,
  approved_at: '2026-10-02T15:00:00Z',
};
