import type { ReportSchedule, ReportScheduleList } from './api';
import { MATHS, PHYSICS, STUDENT_ID } from './fixtures';

// Fixtures of the report schedule panel (#316 F7, F8): S1 bodies with every
// latest-period status, each ineligible reason, an inactive schedule, no
// eligible tutor, and the tutors who could own a new one.

export const CARMEN = {
  user_id: '00000000-0000-4000-8000-000000316c01',
  display_name: 'Carmen Ruiz',
};
export const DIEGO = {
  user_id: '00000000-0000-4000-8000-000000316c02',
  display_name: 'Diego Mora',
};
export const SUBMITTED_REPORT_ID = '00000000-0000-4000-8000-000000316r01';

let next = 0;

/** A schedule (an active weekly one of Carmen for all subjects by
 * default). */
export function scheduleOf(
  changes: Partial<ReportSchedule> = {},
): ReportSchedule {
  next += 1;
  return {
    id: `00000000-0000-4000-8000-000000316${String(next).padStart(3, '0')}`,
    version: 1,
    student_id: STUDENT_ID,
    author: CARMEN,
    cadence: 'weekly',
    subject: null,
    starts_on: '2026-10-05',
    is_active: true,
    created_at: '2026-10-05T15:00:00Z',
    created_by: { display_name: 'Coordinadora' },
    ineligible_reason: null,
    latest_period: null,
    ...changes,
  };
}

export const WEEKLY_DRAFT = scheduleOf({
  latest_period: {
    period_start: '2026-10-12',
    period_end: '2026-10-18',
    status: 'draft',
    report_id: null,
  },
});

export const MONTHLY_SUBMITTED = scheduleOf({
  author: DIEGO,
  cadence: 'monthly',
  subject: MATHS,
  starts_on: '2026-09-01',
  latest_period: {
    period_start: '2026-09-01',
    period_end: '2026-09-30',
    status: 'submitted',
    report_id: SUBMITTED_REPORT_ID,
  },
});

export const WEEKLY_NOT_CURRENT = scheduleOf({
  subject: PHYSICS,
  ineligible_reason: 'author_not_current',
  latest_period: {
    period_start: '2026-10-12',
    period_end: '2026-10-18',
    status: 'not_created',
    report_id: null,
  },
});

export const INACTIVE = scheduleOf({
  author: DIEGO,
  cadence: 'monthly',
  is_active: false,
  starts_on: '2026-06-01',
});

export const AUTHORS = [
  { ...CARMEN, subjects: [PHYSICS, MATHS] },
  { ...DIEGO, subjects: [MATHS] },
];

export const SCHEDULES: ReportScheduleList = {
  items: [WEEKLY_DRAFT, MONTHLY_SUBMITTED, WEEKLY_NOT_CURRENT, INACTIVE],
  eligible_authors: AUTHORS,
};

export const NO_SCHEDULES: ReportScheduleList = {
  items: [],
  eligible_authors: AUTHORS,
};

export const NO_TUTOR: ReportScheduleList = {
  items: [],
  eligible_authors: [],
};

/** One schedule per latest-period status (S1 B3). */
export const EVERY_STATUS: ReportScheduleList = {
  items: (
    [
      'not_created',
      'draft',
      'deleted',
      'submitted',
      'changes_requested',
      'approved_for_family',
      'retracted',
    ] as const
  ).map((status, index) =>
    scheduleOf({
      author: { ...CARMEN, display_name: `Tutor ${index + 1}` },
      latest_period: {
        period_start: '2026-10-12',
        period_end: '2026-10-18',
        status,
        report_id: [
          'submitted',
          'changes_requested',
          'approved_for_family',
          'retracted',
        ].includes(status)
          ? `00000000-0000-4000-8000-000000316s0${index}`
          : null,
      },
    }),
  ),
  eligible_authors: AUTHORS,
};

/** One schedule per ineligible reason. */
export const EVERY_REASON: ReportScheduleList = {
  items: (
    [
      'student_inactive',
      'author_not_tutor',
      'author_not_current',
      'subject_inactive',
      'subject_not_assigned',
    ] as const
  ).map((reason) =>
    scheduleOf({ subject: PHYSICS, ineligible_reason: reason }),
  ),
  eligible_authors: AUTHORS,
};
