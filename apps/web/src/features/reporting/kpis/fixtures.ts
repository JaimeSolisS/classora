import type { Session } from '@/features/auth/session';
import type { Balances } from '../api';
import type {
  CourseProfitability,
  RevenueTrend,
  RoomUtilization,
  StudentRetention,
  TutorPerformance,
} from './api';

// KPI fixtures (M8-09b E1, E2): #126's worked examples B1–B5, the same
// figures as _docs/kpis.md and the API's integration tests, so the UI and
// the API agree. One center "today" for every response (2031-04-02), so
// the page's latched today doesn't move between them.

export const KPI_TODAY = '2031-04-02';

const ZERO = '0.00';

/** B1 accrual: 2031-01-15..2031-03-10. */
export const TREND_ACCRUAL: RevenueTrend = {
  from: '2031-01-15',
  to: '2031-03-10',
  basis: 'accrual',
  today: KPI_TODAY,
  months: [
    {
      month: '2031-01',
      from: '2031-01-15',
      to: '2031-01-31',
      revenue_total: '400.00',
      direct_tutor_cost_total: ZERO,
      gross_profit: '400.00',
      operating_expenses_total: ZERO,
      net_result: '400.00',
    },
    {
      month: '2031-02',
      from: '2031-02-01',
      to: '2031-02-28',
      revenue_total: '400.00',
      direct_tutor_cost_total: '250.00',
      gross_profit: '150.00',
      operating_expenses_total: '1000.00',
      net_result: '-850.00',
    },
    {
      month: '2031-03',
      from: '2031-03-01',
      to: '2031-03-10',
      revenue_total: ZERO,
      direct_tutor_cost_total: ZERO,
      gross_profit: ZERO,
      operating_expenses_total: ZERO,
      net_result: ZERO,
    },
  ],
  totals: {
    revenue_total: '800.00',
    direct_tutor_cost_total: '250.00',
    gross_profit: '550.00',
    operating_expenses_total: '1000.00',
    net_result: '-450.00',
  },
};

/** B1 cash: the same range. */
export const TREND_CASH: RevenueTrend = {
  ...TREND_ACCRUAL,
  basis: 'cash',
  months: [
    {
      month: '2031-01',
      from: '2031-01-15',
      to: '2031-01-31',
      revenue_total: '400.00',
      direct_tutor_cost_total: ZERO,
      gross_profit: '400.00',
      operating_expenses_total: ZERO,
      net_result: '400.00',
    },
    {
      month: '2031-02',
      from: '2031-02-01',
      to: '2031-02-28',
      revenue_total: ZERO,
      direct_tutor_cost_total: ZERO,
      gross_profit: ZERO,
      operating_expenses_total: '1000.00',
      net_result: '-1000.00',
    },
    {
      month: '2031-03',
      from: '2031-03-01',
      to: '2031-03-10',
      revenue_total: '300.00',
      direct_tutor_cost_total: '250.00',
      gross_profit: '50.00',
      operating_expenses_total: ZERO,
      net_result: '50.00',
    },
  ],
  totals: {
    revenue_total: '700.00',
    direct_tutor_cost_total: '250.00',
    gross_profit: '450.00',
    operating_expenses_total: '1000.00',
    net_result: '-550.00',
  },
};

/** No data (#126 edge cases): every month at zero. */
export const TREND_EMPTY: RevenueTrend = {
  ...TREND_ACCRUAL,
  months: TREND_ACCRUAL.months.map((month) => ({
    ...month,
    revenue_total: ZERO,
    direct_tutor_cost_total: ZERO,
    gross_profit: ZERO,
    operating_expenses_total: ZERO,
    net_result: ZERO,
  })),
  totals: {
    revenue_total: ZERO,
    direct_tutor_cost_total: ZERO,
    gross_profit: ZERO,
    operating_expenses_total: ZERO,
    net_result: ZERO,
  },
};

/** B2: 2031-03-01..2031-03-31. */
export const COURSES: CourseProfitability = {
  from: '2031-03-01',
  to: '2031-03-31',
  today: KPI_TODAY,
  cohorts: [
    {
      cohort: { id: '12600000-0000-4000-8000-000000000002', name: 'C2' },
      revenue: '666.67',
      direct_tutor_cost: ZERO,
      gross_profit: '666.67',
    },
    {
      cohort: { id: '12600000-0000-4000-8000-000000000001', name: 'C1' },
      revenue: '1000.00',
      direct_tutor_cost: '750.00',
      gross_profit: '250.00',
    },
  ],
  not_attributed: {
    revenue: '800.00',
    direct_tutor_cost: ZERO,
    gross_profit: '800.00',
  },
  total: {
    revenue: '2466.67',
    direct_tutor_cost: '750.00',
    gross_profit: '1716.67',
  },
};

export const COURSES_EMPTY: CourseProfitability = {
  ...COURSES,
  cohorts: [],
  not_attributed: {
    revenue: ZERO,
    direct_tutor_cost: ZERO,
    gross_profit: ZERO,
  },
  total: { revenue: ZERO, direct_tutor_cost: ZERO, gross_profit: ZERO },
};

const ROOM_A = { id: '12600000-0000-4000-8000-00000000000a', name: 'A' };
const ROOM_B = { id: '12600000-0000-4000-8000-00000000000b', name: 'B' };

/** B3: Mon 2031-03-03..Sun 2031-03-09. */
export const ROOMS: RoomUtilization = {
  from: '2031-03-03',
  to: '2031-03-09',
  rooms: [
    {
      room: ROOM_A,
      available_minutes: 3180,
      used_minutes: 210,
      utilization_percent: '6.60',
    },
    {
      room: ROOM_B,
      available_minutes: 300,
      used_minutes: 120,
      utilization_percent: '40.00',
    },
  ],
  total: {
    available_minutes: 3480,
    used_minutes: 330,
    utilization_percent: '9.48',
  },
};

/** B3's edge cases: no available minutes (null) and an over-window booking. */
export const ROOMS_EDGES: RoomUtilization = {
  ...ROOMS,
  rooms: [
    {
      room: ROOM_A,
      available_minutes: 60,
      used_minutes: 120,
      utilization_percent: '200.00',
    },
    {
      room: ROOM_B,
      available_minutes: 0,
      used_minutes: 60,
      utilization_percent: null,
    },
  ],
  total: {
    available_minutes: 60,
    used_minutes: 180,
    utilization_percent: '300.00',
  },
};

export const ROOMS_EMPTY: RoomUtilization = {
  ...ROOMS,
  rooms: [],
  total: { available_minutes: 0, used_minutes: 0, utilization_percent: null },
};

/** B4: 2031-03-01..2031-03-31, previous window 2031-01-29..2031-02-28. */
export const RETENTION: StudentRetention = {
  from: '2031-03-01',
  to: '2031-03-31',
  previous_from: '2031-01-29',
  previous_to: '2031-02-28',
  active_previous: 5,
  active_current: 5,
  retained: 2,
  new: 3,
  lost: 3,
  retention_percent: '40.00',
  months: [
    {
      month: '2031-03',
      from: '2031-03-01',
      to: '2031-03-31',
      active_students: 5,
    },
  ],
};

export const RETENTION_EMPTY: StudentRetention = {
  ...RETENTION,
  active_previous: 0,
  active_current: 0,
  retained: 0,
  new: 0,
  lost: 0,
  retention_percent: null,
  months: RETENTION.months.map((month) => ({ ...month, active_students: 0 })),
};

/** B5: 2031-03-01..2031-03-31. */
export const TUTORS: TutorPerformance = {
  from: '2031-03-01',
  to: '2031-03-31',
  tutors: [
    {
      tutor: {
        id: '12600000-0000-4000-8000-000000000101',
        display_name: 'T1',
      },
      sessions: 3,
      completed: 2,
      pending_closeout: 1,
      cancelled: 1,
      scheduled_minutes: 210,
      students: 4,
      attendance: { present: 2, late: 1, absent: 1, not_recorded: 1 },
      attendance_percent: '75.00',
    },
    {
      tutor: {
        id: '12600000-0000-4000-8000-000000000102',
        display_name: 'T2',
      },
      sessions: 1,
      completed: 1,
      pending_closeout: 0,
      cancelled: 0,
      scheduled_minutes: 60,
      students: 1,
      attendance: { present: 1, late: 0, absent: 0, not_recorded: 0 },
      attendance_percent: '100.00',
    },
  ],
  totals: {
    sessions: 4,
    completed: 3,
    pending_closeout: 1,
    cancelled: 1,
    scheduled_minutes: 270,
    students: 5,
    attendance: { present: 3, late: 1, absent: 1, not_recorded: 1 },
    attendance_percent: '80.00',
  },
};

export const TUTORS_EMPTY: TutorPerformance = {
  ...TUTORS,
  tutors: [],
  totals: {
    sessions: 0,
    completed: 0,
    pending_closeout: 0,
    cancelled: 0,
    scheduled_minutes: 0,
    students: 0,
    attendance: { present: 0, late: 0, absent: 0, not_recorded: 0 },
    attendance_percent: null,
  },
};

/** K3: the M7-14b balances, as of the KPI fixtures' today. */
export const KPI_BALANCES: Balances = {
  today: KPI_TODAY,
  receivables: {
    outstanding: '200.00',
    overdue: '0.00',
    account_credit: '50.00',
    accounts_with_outstanding: 1,
  },
  payables: {
    expenses_open: '1250.00',
    expenses_overdue: '500.00',
    payroll_unpaid: '3830.00',
    payroll_periods_unpaid: 2,
    total: '5080.00',
  },
  deferred_revenue: {
    total: '950.00',
    packages: '600.00',
    courses: '0.00',
    scheduled_sessions: '350.00',
    other: '0.00',
  },
};

export const KPI_BALANCES_EMPTY: Balances = {
  today: KPI_TODAY,
  receivables: {
    outstanding: ZERO,
    overdue: ZERO,
    account_credit: ZERO,
    accounts_with_outstanding: 0,
  },
  payables: {
    expenses_open: ZERO,
    expenses_overdue: ZERO,
    payroll_unpaid: ZERO,
    payroll_periods_unpaid: 0,
    total: ZERO,
  },
  deferred_revenue: {
    total: ZERO,
    packages: ZERO,
    courses: ZERO,
    scheduled_sessions: ZERO,
    other: ZERO,
  },
};

/** The Owner's session for the stories (every section shown). */
export const KPI_OWNER_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.owner',
    display_name: 'Demo Owner',
    roles: ['owner'],
    permissions: [
      'finance.report.view',
      'schedule.view',
      'tutor.view',
      'billing.view',
      'expense.view',
    ],
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
  },
};
