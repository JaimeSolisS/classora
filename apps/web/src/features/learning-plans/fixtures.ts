import type { SessionUserBody } from '@/features/auth/session';
import { JOSE_ID } from '@/features/students/fixtures';
import { TEST_USER } from '@/test/api-stub';
import type {
  LearningPlan,
  LearningPlanList,
  LearningPlanMilestone,
  LearningPlanObjective,
  LearningPlanSummary,
} from './api';

// Fixtures of the learning plan pages (#310 G1, G2): plans in every status
// with the flags each audience gets, the list bodies (with the AM1 hints),
// a plan at the limits, markup as text and the users by role.

export const STUDENT_ID = JOSE_ID;
export const STUDENT_NAME = 'José Ñúñez Pérez';
export const PLAN_ID = '00000000-0000-4000-8000-000000310a01';
export const PHYSICS = {
  id: '00000000-0000-4000-8000-000000310501',
  name: 'Física',
};
export const MATHS = {
  id: '00000000-0000-4000-8000-000000310502',
  name: 'Matemáticas',
};
export const SCRIPT = '<script>alert("x")</script>';

const NO_FLAGS = {
  can_edit: false,
  can_activate: false,
  can_complete: false,
  can_discontinue: false,
  can_delete: false,
  can_update_progress: false,
};

const id = (n: number) =>
  `00000000-0000-4000-8000-${String(310_000_000_000 + n).padStart(12, '0')}`;

export function milestone(
  n: number,
  overrides: Partial<LearningPlanMilestone> = {},
): LearningPlanMilestone {
  return {
    id: id(1000 + n),
    version: 1,
    position: 1,
    title: `Hito ${n}`,
    activities: null,
    due_on: null,
    status: 'pending',
    overdue: false,
    completed_at: null,
    completed_by: null,
    ...overrides,
  };
}

export function objective(
  n: number,
  milestones: LearningPlanMilestone[],
  overrides: Partial<LearningPlanObjective> = {},
): LearningPlanObjective {
  const done = milestones.filter((m) => m.status === 'done').length;
  return {
    id: id(100 + n),
    position: n,
    title: `Objetivo ${n}`,
    description: null,
    complete: done === milestones.length,
    progress: {
      done,
      total: milestones.length,
      percent: String(Math.round((done / milestones.length) * 100)),
    },
    milestones: milestones.map((m, index) => ({ ...m, position: index + 1 })),
    ...overrides,
  };
}

function withTotals(
  plan: Omit<
    LearningPlan,
    | 'progress'
    | 'objectives_complete'
    | 'objectives_total'
    | 'overdue_milestones'
  >,
): LearningPlan {
  const milestones = plan.objectives.flatMap((o) => o.milestones);
  const done = milestones.filter((m) => m.status === 'done').length;
  return {
    ...plan,
    progress: {
      done,
      total: milestones.length,
      percent: String(Math.round((done / milestones.length) * 100)),
    },
    objectives_complete: plan.objectives.filter((o) => o.complete).length,
    objectives_total: plan.objectives.length,
    overdue_milestones: milestones.filter((m) => m.overdue).length,
  };
}

const BASE = {
  id: PLAN_ID,
  version: 3,
  student_id: STUDENT_ID,
  subject: PHYSICS,
  title: 'Leer con fluidez',
  status: 'draft' as const,
  starts_on: '2026-11-02',
  target_on: '2027-01-29',
  activated_at: null,
  closed_at: null,
  created_by: { display_name: 'Carmen Ruiz' },
  updated_at: '2026-11-10T15:00:00Z',
  description: 'Mejorar la comprensión lectora.\nY la velocidad.',
};

/** A draft as a holder sees it: Edit, Activate, Discontinue, Delete. */
export const DRAFT_PLAN: LearningPlan = withTotals({
  ...BASE,
  objectives: [
    objective(1, [
      milestone(1, { title: 'Lectura diaria', activities: '10 min al día' }),
      milestone(2, { title: 'Resumen', due_on: '2026-12-01' }),
    ]),
    objective(2, [milestone(3, { title: 'Vocabulario' })]),
  ],
  ...NO_FLAGS,
  can_edit: true,
  can_activate: true,
  can_discontinue: true,
  can_delete: true,
});

/** An active plan with a done milestone and an overdue one; the writer's
 * flags (Edit, Complete, Discontinue, progress). */
export const ACTIVE_PLAN: LearningPlan = withTotals({
  ...BASE,
  version: 5,
  status: 'active',
  activated_at: '2026-11-03T16:00:00Z',
  objectives: [
    objective(1, [
      milestone(1, {
        title: 'Lectura diaria',
        activities: '10 min al día',
        status: 'done',
        version: 2,
        completed_at: '2026-11-08T17:00:00Z',
        completed_by: { display_name: 'Carmen Ruiz' },
      }),
      milestone(2, {
        title: 'Resumen',
        due_on: '2026-11-05',
        overdue: true,
        version: 4,
      }),
    ]),
    objective(2, [
      milestone(3, { title: 'Vocabulario', status: 'in_progress' }),
    ]),
  ],
  ...NO_FLAGS,
  can_edit: true,
  can_complete: true,
  can_discontinue: true,
  can_update_progress: true,
});

/** The same active plan for a tutor who teaches another subject. */
export const ACTIVE_READ_ONLY: LearningPlan = { ...ACTIVE_PLAN, ...NO_FLAGS };

/** The active plan as a family sees it (a fixture that wrongly sets every
 * flag: the family page still renders none). */
export const FAMILY_FLAGGED: LearningPlan = {
  ...ACTIVE_PLAN,
  can_edit: true,
  can_activate: true,
  can_complete: true,
  can_discontinue: true,
  can_delete: true,
  can_update_progress: true,
};

export const COMPLETED_PLAN: LearningPlan = {
  ...ACTIVE_PLAN,
  ...NO_FLAGS,
  status: 'completed',
  closed_at: '2026-11-20T18:00:00Z',
};

export const DISCONTINUED_PLAN: LearningPlan = {
  ...ACTIVE_PLAN,
  ...NO_FLAGS,
  status: 'discontinued',
  closed_at: '2026-11-21T18:00:00Z',
};

/** An active plan whose milestones are all done (Complete shows no count). */
export const ALL_DONE_PLAN: LearningPlan = withTotals({
  ...ACTIVE_PLAN,
  objectives: [
    objective(1, [
      milestone(1, {
        status: 'done',
        completed_at: '2026-11-08T17:00:00Z',
        completed_by: { display_name: 'Carmen Ruiz' },
      }),
    ]),
  ],
});

/** Every free text is markup, to show as text. */
export const SCRIPTED_PLAN: LearningPlan = withTotals({
  ...BASE,
  title: SCRIPT,
  description: SCRIPT,
  objectives: [
    objective(1, [milestone(1, { title: SCRIPT, activities: SCRIPT })], {
      title: SCRIPT,
      description: SCRIPT,
    }),
  ],
  ...NO_FLAGS,
});

/** Long unbroken words (320 px wrap). */
const LONG = 'Superextraordinariamente'.repeat(6);
export const LONG_PLAN: LearningPlan = withTotals({
  ...BASE,
  title: LONG,
  description: LONG,
  objectives: [
    objective(1, [milestone(1, { title: LONG, activities: LONG })], {
      title: LONG,
    }),
  ],
  ...NO_FLAGS,
  can_edit: true,
});

/** 10 objectives of 5 milestones: 50 in the plan (every add disabled). */
export const FULL_PLAN: LearningPlan = withTotals({
  ...BASE,
  objectives: Array.from({ length: 10 }, (_, o) =>
    objective(
      o + 1,
      Array.from({ length: 5 }, (_, m) => milestone(o * 5 + m + 1)),
    ),
  ),
  ...NO_FLAGS,
  can_edit: true,
});

/** One objective of 10 milestones ("Add milestone" disabled there). */
export const TEN_MILESTONES_PLAN: LearningPlan = withTotals({
  ...BASE,
  objectives: [
    objective(
      1,
      Array.from({ length: 10 }, (_, m) => milestone(m + 1)),
    ),
  ],
  ...NO_FLAGS,
  can_edit: true,
});

// ------------------------------------------------------------------ lists

export function summary(plan: LearningPlan): LearningPlanSummary {
  return {
    id: plan.id,
    version: plan.version,
    student_id: plan.student_id,
    subject: plan.subject,
    title: plan.title,
    status: plan.status,
    starts_on: plan.starts_on,
    target_on: plan.target_on,
    activated_at: plan.activated_at,
    closed_at: plan.closed_at,
    created_by: plan.created_by,
    progress: plan.progress,
    objectives_complete: plan.objectives_complete,
    objectives_total: plan.objectives_total,
    overdue_milestones: plan.overdue_milestones,
    updated_at: plan.updated_at,
  };
}

const ACTIVE_SUMMARY = summary(ACTIVE_PLAN);
const DRAFT_SUMMARY = {
  ...summary(DRAFT_PLAN),
  id: '00000000-0000-4000-8000-000000310a02',
  subject: MATHS,
  title: 'Fracciones',
  target_on: null,
};
const COMPLETED_SUMMARY = {
  ...summary(COMPLETED_PLAN),
  id: '00000000-0000-4000-8000-000000310a03',
  title: 'Ortografía',
};

/** A holder's list: every status, any active subject. */
export const MANAGER_LIST: LearningPlanList = {
  items: [ACTIVE_SUMMARY, DRAFT_SUMMARY, COMPLETED_SUMMARY],
  truncated: false,
  can_create: true,
  creatable_subjects: null,
};

/** A tutor teaching the student Física only. */
export const TUTOR_LIST: LearningPlanList = {
  ...MANAGER_LIST,
  creatable_subjects: [PHYSICS],
};

/** A former tutor, or a tutor of no active subject: no create. */
export const READ_ONLY_LIST: LearningPlanList = {
  ...MANAGER_LIST,
  can_create: false,
  creatable_subjects: [],
};

export const TRUNCATED_LIST: LearningPlanList = {
  ...MANAGER_LIST,
  truncated: true,
};

export const EMPTY_LIST: LearningPlanList = {
  items: [],
  truncated: false,
  can_create: true,
  creatable_subjects: null,
};

/** What a family gets: active and closed plans, no create. */
export const FAMILY_LIST: LearningPlanList = {
  items: [ACTIVE_SUMMARY, COMPLETED_SUMMARY],
  truncated: false,
  can_create: false,
  creatable_subjects: [],
};

// ------------------------------------------------------------------ users

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const MANAGER = user(
  ['academic_coordinator'],
  ['student.view', 'learning_plan.manage', 'evaluation.manage'],
);
export const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
export const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'schedule.view', 'course.view'],
);
export const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
export const STUDENT = user(['student'], ['student.view']);
/** A tutor who is also a guardian keeps the staff pages (the union). */
export const TUTOR_GUARDIAN = user(
  ['tutor', 'guardian'],
  ['student.view', 'tutor.view', 'guardian.view'],
);
