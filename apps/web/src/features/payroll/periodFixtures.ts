import type {
  PayrollLine,
  PayrollPeriod,
  PayrollPeriodDetail,
  PayrollPeriodPage,
  PayrollPeriodReview,
} from './api';
import { TUTOR_ID } from './fixtures';

// Payroll period fixtures (#110b H1, H2) for the tests and the stories. The
// ended periods are in October 2026; the one that hasn't ended is in 2099,
// so a test's real clock always reads it as not ended. Every amount is a
// string, as the API sends it.

export const PERIOD_ID = '90000000-0000-4000-8000-000000000001';
export const EARLIER_PERIOD_ID = '90000000-0000-4000-8000-000000000002';
export const FUTURE_PERIOD_ID = '90000000-0000-4000-8000-000000000003';
export const SESSION_ID = '91000000-0000-4000-8000-000000000001';
export const FINANCE_ID = '00000000-0000-4000-8000-0000000000f1';
export const OWNER_ID = '00000000-0000-4000-8000-0000000000a1';
export const ADMIN_ID = '00000000-0000-4000-8000-0000000000d1';

const line = (n: number, overrides: Partial<PayrollLine>): PayrollLine => ({
  id: `92000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  kind: 'session',
  amount: '0.00',
  class_session_id: null,
  approval_id: null,
  service_date: null,
  class_kind: null,
  minutes: null,
  rate_model: null,
  rate: null,
  compensation_rate_id: null,
  session_pay_rule_id: null,
  cumulative_sessions: null,
  cohort_sessions: null,
  tutor_pay_terms_id: null,
  salary_amount: null,
  salary_starts_on: null,
  salary_ends_on: null,
  proration_numerator: null,
  proration_denominator: null,
  proration_basis: null,
  pay_item_id: null,
  pay_item_type_name: null,
  pay_item_description: null,
  payroll_adjustment_id: null,
  adjustment_kind: null,
  adjustment_reason: null,
  adjustment_class_session_id: null,
  adjustment_service_date: null,
  adjustment_class_kind: null,
  adjustment_origin_line_id: null,
  adjustment_origin_period_id: null,
  ...overrides,
});

export const LINE_HOURLY = line(1, {
  kind: 'session',
  amount: '375.00',
  class_session_id: SESSION_ID,
  service_date: '2026-10-05',
  class_kind: 'individual',
  minutes: 90,
  rate_model: 'per_hour',
  rate: '250.0000',
});
export const LINE_COURSE = line(2, {
  kind: 'session',
  amount: '166.67',
  class_session_id: '91000000-0000-4000-8000-000000000002',
  service_date: '2026-10-06',
  class_kind: 'course',
  minutes: 60,
  rate_model: 'per_course',
  rate: '1000.0000',
  cumulative_sessions: 2,
  cohort_sessions: 6,
});
export const LINE_NO_SHOW = line(3, {
  kind: 'no_show',
  amount: '125.00',
  class_session_id: '91000000-0000-4000-8000-000000000003',
  service_date: '2026-10-07',
  class_kind: 'individual',
  minutes: 60,
  rate_model: 'per_session',
  rate: '125.0000',
});
export const LINE_MAKEUP = line(4, {
  kind: 'makeup',
  amount: '200.00',
  class_session_id: '91000000-0000-4000-8000-000000000004',
  service_date: '2026-10-08',
  class_kind: 'individual',
  minutes: 60,
  rate_model: 'per_session',
  rate: '200.0000',
});
export const LINE_LATE = line(5, {
  kind: 'late_cancellation',
  amount: '100.00',
  class_session_id: '91000000-0000-4000-8000-000000000005',
  service_date: '2026-10-09',
  class_kind: 'group',
  minutes: 60,
  rate_model: 'per_session',
  rate: '100.0000',
});
export const LINE_COVERED = line(6, {
  kind: 'session',
  amount: '0.00',
  class_session_id: '91000000-0000-4000-8000-000000000006',
  service_date: '2026-10-10',
  class_kind: 'group',
  minutes: 60,
});
export const LINE_SALARY = line(7, {
  kind: 'salary',
  amount: '3000.00',
  salary_amount: '5000.00',
  salary_starts_on: '2026-10-01',
  salary_ends_on: '2026-10-09',
  proration_numerator: 3,
  proration_denominator: 5,
  proration_basis: 'weekdays',
});
export const LINE_SALARY_NONE = line(8, {
  kind: 'salary',
  amount: '0.00',
  salary_amount: '5000.00',
  salary_starts_on: '2026-10-10',
  salary_ends_on: '2026-10-11',
  proration_numerator: 0,
  proration_denominator: 5,
  proration_basis: 'weekdays',
});
export const LINE_BONUS = line(9, {
  kind: 'pay_item',
  amount: '300.00',
  pay_item_id: '93000000-0000-4000-8000-000000000001',
  pay_item_type_name: 'Transporte',
  pay_item_description: 'Apoyo de transporte',
});
export const LINE_DEDUCTION = line(10, {
  kind: 'pay_item',
  amount: '-150.00',
  pay_item_id: '93000000-0000-4000-8000-000000000002',
  pay_item_type_name: 'Préstamo',
  pay_item_description: 'Abono al préstamo',
});
/** #111's kind (C9); M7-08b renders its adjustment fields. */
export const LINE_ADJUSTMENT = line(11, {
  kind: 'adjustment',
  amount: '50.00',
  service_date: '2026-10-12',
});

export const ALL_LINES: PayrollLine[] = [
  LINE_HOURLY,
  LINE_COURSE,
  LINE_NO_SHOW,
  LINE_MAKEUP,
  LINE_LATE,
  LINE_COVERED,
  LINE_SALARY,
  LINE_SALARY_NONE,
  LINE_BONUS,
  LINE_DEDUCTION,
  LINE_ADJUSTMENT,
];

const detail: PayrollPeriodDetail = {
  id: PERIOD_ID,
  tutor_user_id: TUTOR_ID,
  starts_on: '2026-10-01',
  ends_on: '2026-10-15',
  pay_frequency: 'biweekly',
  status: 'open',
  tutor_display_name: 'Ana Tutora',
  tutor_state: 'active',
  prepared_at: null,
  prepared_by: null,
  finalized_at: null,
  finalized_by: null,
  total_amount: null,
  returned_at: null,
  returned_by: null,
  return_reason: null,
  earlier_unfinalized_period_id: null,
  pending_closeouts: 0,
};

export const PERIOD_OPEN: PayrollPeriodDetail = detail;

export const PERIOD_RETURNED: PayrollPeriodDetail = {
  ...detail,
  returned_at: '2026-10-17T16:00:00Z',
  returned_by: { id: ADMIN_ID, display_name: 'Demo Admin' },
  return_reason: 'Falta el bono de transporte.',
};

export const PERIOD_PREPARED: PayrollPeriodDetail = {
  ...detail,
  status: 'prepared',
  prepared_at: '2026-10-16T15:30:00.123456Z',
  prepared_by: { id: FINANCE_ID, display_name: 'Demo Finance' },
};

export const PERIOD_FINALIZED: PayrollPeriodDetail = {
  ...PERIOD_PREPARED,
  status: 'finalized',
  finalized_at: '2026-10-18T17:00:00Z',
  finalized_by: { id: OWNER_ID, display_name: 'Demo Owner' },
  total_amount: '4116.67',
};

export const PERIOD_NOT_ENDED: PayrollPeriodDetail = {
  ...detail,
  id: FUTURE_PERIOD_ID,
  starts_on: '2099-01-01',
  ends_on: '2099-01-15',
};

export const review = (
  period: PayrollPeriodDetail,
  lines: PayrollLine[] = ALL_LINES,
  total = '4166.67',
): PayrollPeriodReview => ({ period, lines, total });

export const listRow = (
  n: number,
  overrides: Partial<PayrollPeriod> = {},
): PayrollPeriod => ({
  id: `90000000-0000-4000-8000-${String(n + 100).padStart(12, '0')}`,
  tutor_user_id: TUTOR_ID,
  tutor_display_name: 'Ana Tutora',
  tutor_state: 'active',
  starts_on: '2026-10-01',
  ends_on: '2026-10-15',
  pay_frequency: 'biweekly',
  status: 'open',
  line_count: 4,
  total: '1250.00',
  prepared_at: null,
  finalized_at: null,
  ...overrides,
});

export const PERIODS_PAGE: PayrollPeriodPage = {
  items: [
    listRow(1, { id: PERIOD_ID }),
    listRow(2, {
      tutor_display_name: 'Beto Salario',
      tutor_state: 'archived',
      status: 'prepared',
      total: '-150.00',
      pay_frequency: 'monthly',
      starts_on: '2026-09-01',
      ends_on: '2026-09-30',
      prepared_at: '2026-10-02T15:00:00Z',
    }),
    listRow(3, {
      tutor_display_name: 'Carla Grande',
      status: 'finalized',
      total: null,
      line_count: 12,
      pay_frequency: 'weekly',
      finalized_at: '2026-10-03T15:00:00Z',
    }),
  ],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_PERIODS: PayrollPeriodPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
