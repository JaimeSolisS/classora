import type {
  EarningsPage,
  EarningsPayment,
  EarningsPeriod,
  EarningsPeriodDetail,
  PayslipLine,
  PayslipSummary,
} from './api';

// The tutor's earnings fixtures (M7-10b E1, E2) for the tests and the
// stories, in the API's shapes (#113): finalized periods in 2026, every line
// kind and adjustment kind, a covered line, a per-course share and a salary
// piece, a partial payment and a reversed one. Every amount is a string, and
// each detail's summary adds up to its total, as the API's validators make
// sure. No ids on lines and no student names: the API sends none.

export const EARNINGS_PERIOD_ID = '93000000-0000-4000-8000-000000000001';
export const EARNINGS_PAID_ID = '93000000-0000-4000-8000-000000000002';
export const EARNINGS_EMPTY_ID = '93000000-0000-4000-8000-000000000003';
export const EARNINGS_UNPAID_ID = '93000000-0000-4000-8000-000000000004';
export const PAYSLIP_ID = '94000000-0000-4000-8000-000000000001';
export const PAYSLIP_PAID_ID = '94000000-0000-4000-8000-000000000002';

export const EARNINGS_PARTIAL: EarningsPeriod = {
  id: EARNINGS_PERIOD_ID,
  starts_on: '2026-10-01',
  ends_on: '2026-10-15',
  pay_frequency: 'biweekly',
  finalized_on: '2026-10-17',
  total: '5150.13',
  paid_total: '3000.00',
  paid_state: 'partially_paid',
  last_paid_on: '2026-10-20',
  line_count: 13,
  payslip: { id: PAYSLIP_ID, number: 'P-2026-000012' },
};

export const EARNINGS_PAID: EarningsPeriod = {
  id: EARNINGS_PAID_ID,
  starts_on: '2026-09-16',
  ends_on: '2026-09-30',
  pay_frequency: 'biweekly',
  finalized_on: '2026-10-02',
  total: '4000.00',
  paid_total: '4000.00',
  paid_state: 'paid',
  last_paid_on: '2026-10-03',
  line_count: 1,
  payslip: { id: PAYSLIP_PAID_ID, number: 'P-2026-000007' },
};

/** A finalized period with no lines: 0.00, paid, and no payslip (#113). */
export const EARNINGS_EMPTY: EarningsPeriod = {
  id: EARNINGS_EMPTY_ID,
  starts_on: '2026-09-01',
  ends_on: '2026-09-15',
  pay_frequency: 'biweekly',
  finalized_on: '2026-09-17',
  total: '0.00',
  paid_total: '0.00',
  paid_state: 'paid',
  last_paid_on: null,
  line_count: 0,
  payslip: null,
};

export const EARNINGS_UNPAID: EarningsPeriod = {
  id: EARNINGS_UNPAID_ID,
  starts_on: '2026-10-16',
  ends_on: '2026-10-31',
  pay_frequency: 'biweekly',
  finalized_on: '2026-11-02',
  total: '750.00',
  paid_total: '0.00',
  paid_state: 'unpaid',
  last_paid_on: null,
  line_count: 3,
  payslip: {
    id: '94000000-0000-4000-8000-000000000004',
    number: 'P-2026-000015',
  },
};

export function earningsPage(
  items: EarningsPeriod[],
  {
    total = items.length,
    offset = 0,
  }: { total?: number; offset?: number } = {},
): EarningsPage {
  return { items, total, limit: 25, offset };
}

export const EARNINGS_NONE = earningsPage([]);
export const EARNINGS_MIXED = earningsPage([
  EARNINGS_UNPAID,
  EARNINGS_PARTIAL,
  EARNINGS_PAID,
  EARNINGS_EMPTY,
]);

const blank: PayslipLine = {
  kind: 'session',
  amount: '0.00',
  service_date: null,
  class_kind: null,
  minutes: null,
  rate_model: null,
  rate: null,
  covered_by_salary: false,
  cumulative_sessions: null,
  cohort_sessions: null,
  pay_rule: null,
  salary: null,
  pay_item: null,
  adjustment: null,
};

export const lineOf = (overrides: Partial<PayslipLine>): PayslipLine => ({
  ...blank,
  ...overrides,
});

export const LINE_SESSION = lineOf({
  amount: '250.00',
  service_date: '2026-10-02',
  class_kind: 'individual',
  minutes: 60,
  rate_model: 'per_hour',
  rate: '250.0000',
});
export const LINE_LATE = lineOf({
  kind: 'late_cancellation',
  amount: '100.00',
  service_date: '2026-10-05',
  class_kind: 'group',
  minutes: 60,
  rate_model: 'per_hour',
  rate: '200.0000',
  pay_rule: {
    event: 'late_cancellation',
    mode: 'percent',
    percent: '50.0000',
    amount: null,
  },
});
export const LINE_NO_SHOW = lineOf({
  kind: 'no_show',
  amount: '80.00',
  service_date: '2026-10-06',
  class_kind: 'individual',
  minutes: 60,
  rate_model: 'per_hour',
  rate: '250.0000',
  pay_rule: { event: 'no_show', mode: 'fixed', percent: null, amount: '80.00' },
});
export const LINE_MAKEUP = lineOf({
  kind: 'makeup',
  amount: '150.13',
  service_date: '2026-10-07',
  class_kind: 'individual',
  minutes: 30,
  rate_model: 'per_session',
  rate: '150.1250',
  pay_rule: {
    event: 'makeup',
    mode: 'percent',
    percent: '100.0000',
    amount: null,
  },
});
export const LINE_COURSE = lineOf({
  amount: '250.00',
  service_date: '2026-10-08',
  class_kind: 'course',
  minutes: 95,
  rate_model: 'per_course',
  rate: '3000.0000',
  cumulative_sessions: 3,
  cohort_sessions: 12,
});
export const LINE_COVERED = lineOf({
  amount: '0.00',
  service_date: '2026-10-09',
  class_kind: 'individual',
  minutes: 60,
  covered_by_salary: true,
});
export const LINE_SALARY = lineOf({
  kind: 'salary',
  amount: '4000.00',
  salary: {
    amount: '8000.00',
    starts_on: '2026-10-01',
    ends_on: '2026-10-15',
    numerator: 11,
    denominator: 22,
    basis: 'weekdays',
  },
});
export const LINE_BONUS = lineOf({
  kind: 'pay_item',
  amount: '500.00',
  pay_item: {
    type_name: 'Bono de puntualidad',
    direction: 'bonus',
    description: 'Octubre',
  },
});
export const LINE_DEDUCTION = lineOf({
  kind: 'pay_item',
  amount: '-300.00',
  pay_item: {
    type_name: 'Préstamo',
    direction: 'deduction',
    description: 'Cuota 2 de 6',
  },
});
export const LINE_LATE_APPROVAL = lineOf({
  kind: 'adjustment',
  amount: '250.00',
  adjustment: {
    kind: 'late_approval',
    reason: null,
    service_date: '2026-09-28',
    class_kind: 'individual',
    origin_period: null,
    paired_period: null,
  },
});
export const LINE_HOURS_CORRECTION = lineOf({
  kind: 'adjustment',
  amount: '-50.00',
  adjustment: {
    kind: 'hours_correction',
    reason: null,
    service_date: '2026-09-29',
    class_kind: 'group',
    origin_period: { starts_on: '2026-09-16', ends_on: '2026-09-30' },
    paired_period: null,
  },
});
export const LINE_MANUAL = lineOf({
  kind: 'adjustment',
  amount: '120.00',
  adjustment: {
    kind: 'manual',
    reason: 'Viáticos de la feria escolar',
    service_date: null,
    class_kind: null,
    origin_period: null,
    paired_period: null,
  },
});
export const LINE_CARRY = lineOf({
  kind: 'adjustment',
  amount: '-200.00',
  adjustment: {
    kind: 'carry_forward',
    reason: null,
    service_date: null,
    class_kind: null,
    origin_period: { starts_on: '2026-09-16', ends_on: '2026-09-30' },
    paired_period: { starts_on: '2026-09-16', ends_on: '2026-09-30' },
  },
});

export const PAYMENT_KEPT: EarningsPayment = {
  id: '95000000-0000-4000-8000-000000000001',
  paid_on: '2026-10-20',
  amount: '3000.00',
  method_code: 'transfer',
  reference: 'SPEI 4471',
  reversed_on: null,
};
export const PAYMENT_UNDONE: EarningsPayment = {
  id: '95000000-0000-4000-8000-000000000002',
  paid_on: '2026-10-18',
  amount: '1000.00',
  method_code: 'cash',
  reference: 'Recibo 12',
  reversed_on: '2026-10-19',
};

const summaryOf = (overrides: Partial<PayslipSummary>): PayslipSummary => ({
  class_line_count: 0,
  class_minutes: 0,
  classes: '0.00',
  salary: '0.00',
  bonuses: '0.00',
  deductions: '0.00',
  adjustments: '0.00',
  ...overrides,
});

/** Every line kind and adjustment kind, part paid with a reversal. */
export const DETAIL_FULL: EarningsPeriodDetail = {
  period: EARNINGS_PARTIAL,
  lines: [
    LINE_SESSION,
    LINE_LATE,
    LINE_NO_SHOW,
    LINE_MAKEUP,
    LINE_COURSE,
    LINE_COVERED,
    LINE_SALARY,
    LINE_BONUS,
    LINE_DEDUCTION,
    LINE_LATE_APPROVAL,
    LINE_HOURS_CORRECTION,
    LINE_MANUAL,
    LINE_CARRY,
  ],
  summary: summaryOf({
    class_line_count: 6,
    class_minutes: 365,
    classes: '830.13',
    salary: '4000.00',
    bonuses: '500.00',
    deductions: '-300.00',
    adjustments: '120.00',
  }),
  total: '5150.13',
  payments: [PAYMENT_KEPT, PAYMENT_UNDONE],
  payslip: EARNINGS_PARTIAL.payslip,
};

/** A salaried tutor, paid in full: the salary piece only. */
export const DETAIL_SALARIED: EarningsPeriodDetail = {
  period: EARNINGS_PAID,
  lines: [
    {
      ...LINE_SALARY,
      salary: {
        ...LINE_SALARY.salary!,
        starts_on: '2026-09-16',
        ends_on: '2026-09-30',
      },
    },
  ],
  summary: summaryOf({ salary: '4000.00' }),
  total: '4000.00',
  payments: [
    {
      id: '95000000-0000-4000-8000-000000000003',
      paid_on: '2026-10-03',
      amount: '4000.00',
      method_code: 'transfer',
      reference: 'SPEI 4100',
      reversed_on: null,
    },
  ],
  payslip: EARNINGS_PAID.payslip,
};

/** An hourly tutor with event lines, unpaid. */
export const DETAIL_HOURLY: EarningsPeriodDetail = {
  period: EARNINGS_UNPAID,
  lines: [LINE_SESSION, LINE_LATE, LINE_NO_SHOW, LINE_MAKEUP],
  summary: summaryOf({
    class_line_count: 4,
    class_minutes: 210,
    classes: '580.13',
  }),
  total: '580.13',
  payments: [],
  payslip: EARNINGS_UNPAID.payslip,
};

/** A per-course tutor: each line is its share `k / N`. */
export const DETAIL_COURSE: EarningsPeriodDetail = {
  period: { ...EARNINGS_UNPAID, total: '500.00', line_count: 2 },
  lines: [
    LINE_COURSE,
    { ...LINE_COURSE, service_date: '2026-10-15', cumulative_sessions: 4 },
  ],
  summary: summaryOf({
    class_line_count: 2,
    class_minutes: 190,
    classes: '500.00',
  }),
  total: '500.00',
  payments: [],
  payslip: EARNINGS_UNPAID.payslip,
};

/** Adjustments and a carry-forward, no class: totals below the lines. */
export const DETAIL_ADJUSTMENTS: EarningsPeriodDetail = {
  period: { ...EARNINGS_UNPAID, total: '120.00', line_count: 4 },
  lines: [LINE_LATE_APPROVAL, LINE_HOURS_CORRECTION, LINE_MANUAL, LINE_CARRY],
  summary: summaryOf({ adjustments: '120.00' }),
  total: '120.00',
  payments: [],
  payslip: EARNINGS_UNPAID.payslip,
};

/** A period with no lines: no payslip, "No activity". */
export const DETAIL_EMPTY: EarningsPeriodDetail = {
  period: EARNINGS_EMPTY,
  lines: [],
  summary: summaryOf({}),
  total: '0.00',
  payments: [],
  payslip: null,
};
