import {
  GROUP_OPTIONS,
  PRICE_GROUP_PER_PERIOD,
  PRICE_PER_PERIOD,
  PRICE_WITH_NEXT,
  SUBJECT_ALGEBRA,
} from '../pricing/fixtures';
import type {
  EligibleEnrollment,
  StudentOption,
  TuitionPlan,
  TuitionPlanDetail,
  TuitionPlanPage,
} from './api';

// Tuition plan fixtures (#374 H1, H2) for the tests and the stories. The
// center's "today" in them is 2026-10-20.

export const STUDENT_ANA: StudentOption = {
  id: '40000000-0000-4000-8000-000000000001',
  name: 'Ana López',
};
export const STUDENT_BRUNO: StudentOption = {
  id: '40000000-0000-4000-8000-000000000002',
  name: 'Bruno Díaz',
};
const FAMILY_LOPEZ = {
  id: '50000000-0000-4000-8000-000000000002',
  name: 'Familia López',
};
const FAMILY_DIAZ = {
  id: '50000000-0000-4000-8000-000000000001',
  name: 'Familia Díaz',
};

/** Every per-period price the create dialog offers, plus a per-session one. */
export const CATALOG_PRICES = [
  PRICE_PER_PERIOD,
  PRICE_GROUP_PER_PERIOD,
  PRICE_WITH_NEXT,
];

export const ENROLLMENT_OPEN: EligibleEnrollment = {
  group_enrollment_id: 'b0000000-0000-4000-8000-000000000001',
  group: { id: GROUP_OPTIONS[0]!.id, name: GROUP_OPTIONS[0]!.name },
  subject: { id: SUBJECT_ALGEBRA.id, name: SUBJECT_ALGEBRA.name },
  enrolled_on: '2026-09-01',
  has_fee_charge: false,
  has_plan: false,
};
export const ENROLLMENT_FEE_CHARGED: EligibleEnrollment = {
  group_enrollment_id: 'b0000000-0000-4000-8000-000000000002',
  group: { id: '40000000-0000-4000-8000-000000000009', name: 'Álgebra lunes' },
  subject: { id: SUBJECT_ALGEBRA.id, name: SUBJECT_ALGEBRA.name },
  enrolled_on: '2026-08-15',
  has_fee_charge: true,
  has_plan: false,
};
export const ENROLLMENT_WITH_PLAN: EligibleEnrollment = {
  ...ENROLLMENT_FEE_CHARGED,
  group_enrollment_id: 'b0000000-0000-4000-8000-000000000003',
  group: { id: '40000000-0000-4000-8000-000000000008', name: 'Álgebra martes' },
  has_fee_charge: false,
  has_plan: true,
};
export const ENROLLMENTS = [
  ENROLLMENT_OPEN,
  ENROLLMENT_FEE_CHARGED,
  ENROLLMENT_WITH_PLAN,
];

/** An open-ended individual monthly plan from 1 Oct, active. */
export const PLAN_ACTIVE: TuitionPlan = {
  id: 'a0000000-0000-4000-8000-000000000001',
  version: 3,
  student: STUDENT_ANA,
  family: FAMILY_LOPEZ,
  service_price: PRICE_PER_PERIOD,
  group: null,
  group_enrollment_id: null,
  frequency: 'monthly',
  starts_on: '2026-10-01',
  ends_on: null,
  due_offset_days: 5,
  status: 'active',
  current_amount: '1500.00',
};

/** A biweekly group plan, paused, ending 27 Dec. */
export const PLAN_PAUSED: TuitionPlan = {
  id: 'a0000000-0000-4000-8000-000000000002',
  version: 2,
  student: STUDENT_BRUNO,
  family: FAMILY_DIAZ,
  service_price: PRICE_GROUP_PER_PERIOD,
  group: { id: GROUP_OPTIONS[0]!.id, name: GROUP_OPTIONS[0]!.name },
  group_enrollment_id: ENROLLMENT_OPEN.group_enrollment_id,
  frequency: 'biweekly',
  starts_on: '2026-09-07',
  ends_on: '2026-12-27',
  due_offset_days: 0,
  status: 'paused',
  current_amount: '700.00',
};

/** A plan that starts next month, its price not offered. */
export const PLAN_SCHEDULED: TuitionPlan = {
  ...PLAN_ACTIVE,
  id: 'a0000000-0000-4000-8000-000000000003',
  version: 1,
  starts_on: '2026-11-01',
  ends_on: '2027-04-30',
  status: 'scheduled',
  current_amount: null,
};

export const PLANS_PAGE: TuitionPlanPage = {
  items: [PLAN_ACTIVE, PLAN_PAUSED, PLAN_SCHEDULED],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_PLANS_PAGE: TuitionPlanPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

const CHARGE_OCT = 'c0000000-0000-4000-8000-000000000001';

/**
 * PLAN_ACTIVE in detail: October charged (pending), November and December
 * upcoming, January not due inside a pause that starts then, February not
 * due outside it.
 */
export const PLAN_ACTIVE_DETAIL: TuitionPlanDetail = {
  ...PLAN_ACTIVE,
  pauses: [
    {
      id: 'd0000000-0000-4000-8000-000000000001',
      version: 1,
      starts_on: '2027-01-01',
      ends_on: '2027-01-31',
    },
  ],
  posted_through: '2026-10-31',
  recent_periods: [
    {
      period_start: '2026-10-01',
      period_end: '2026-10-31',
      charge_id: CHARGE_OCT,
      amount: '1500.00',
      status: 'pending',
    },
  ],
  upcoming_periods: [
    {
      period_start: '2026-10-01',
      period_end: '2026-10-31',
      state: 'due',
      charge_id: CHARGE_OCT,
      reason: null,
    },
    {
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2026-12-01',
      period_end: '2026-12-31',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2027-01-01',
      period_end: '2027-01-31',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2027-02-01',
      period_end: '2027-02-28',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
  ],
};

/** A plan with nothing charged: the current period has no price. */
export const PLAN_UNCHARGED_DETAIL: TuitionPlanDetail = {
  ...PLAN_ACTIVE,
  id: 'a0000000-0000-4000-8000-000000000004',
  version: 1,
  ends_on: '2027-01-31',
  pauses: [],
  posted_through: null,
  recent_periods: [],
  upcoming_periods: [
    {
      period_start: '2026-10-01',
      period_end: '2026-10-31',
      state: 'due',
      charge_id: null,
      reason: 'no_price',
    },
    {
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2026-12-01',
      period_end: '2026-12-31',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2027-01-01',
      period_end: '2027-01-31',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
  ],
};

/** PLAN_PAUSED in detail: paused from 19 Oct until resumed. */
export const PLAN_PAUSED_DETAIL: TuitionPlanDetail = {
  ...PLAN_PAUSED,
  pauses: [
    {
      id: 'd0000000-0000-4000-8000-000000000002',
      version: 1,
      starts_on: '2026-10-19',
      ends_on: null,
    },
  ],
  posted_through: '2026-10-18',
  recent_periods: [
    {
      period_start: '2026-10-05',
      period_end: '2026-10-18',
      charge_id: 'c0000000-0000-4000-8000-000000000002',
      amount: '700.00',
      status: 'paid',
    },
    {
      period_start: '2026-09-21',
      period_end: '2026-10-04',
      charge_id: 'c0000000-0000-4000-8000-000000000003',
      amount: '700.00',
      status: 'overdue',
    },
  ],
  upcoming_periods: [
    {
      period_start: '2026-10-19',
      period_end: '2026-11-01',
      state: 'paused',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2026-11-02',
      period_end: '2026-11-15',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
    {
      period_start: '2026-11-16',
      period_end: '2026-11-29',
      state: 'not_due',
      charge_id: null,
      reason: null,
    },
  ],
};

/**
 * An ended group plan (withdrawn on the first day of October): its October
 * period is charged although the API reports enrollment_ended.
 */
export const PLAN_ENDED_DETAIL: TuitionPlanDetail = {
  ...PLAN_PAUSED,
  id: 'a0000000-0000-4000-8000-000000000005',
  status: 'ended',
  ends_on: null,
  pauses: [],
  posted_through: '2026-10-18',
  recent_periods: [
    {
      period_start: '2026-10-05',
      period_end: '2026-10-18',
      charge_id: 'c0000000-0000-4000-8000-000000000004',
      amount: '700.00',
      status: 'void',
    },
  ],
  upcoming_periods: [
    {
      period_start: '2026-10-19',
      period_end: '2026-11-01',
      state: 'enrollment_ended',
      charge_id: 'c0000000-0000-4000-8000-000000000005',
      reason: null,
    },
  ],
};

/** The student search of the stories: every fixture student matching q. */
export function searchFixtureStudents(q = ''): Promise<StudentOption[]> {
  const text = q.toLowerCase();
  return Promise.resolve(
    [STUDENT_ANA, STUDENT_BRUNO].filter((student) =>
      student.name.toLowerCase().includes(text),
    ),
  );
}
