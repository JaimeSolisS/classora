import type {
  CompensationRate,
  PayrollSettings,
  PayrollTutor,
  PayrollTutorPage,
  PayTerms,
  TutorCompensation,
} from './api';

// Payroll fixtures (#425 H1, H2) for the tests and the stories. The center's
// "today" in them is 2026-10-20. The terms and rates arrive in the API's
// order, newest first per kind.

export const TODAY = '2026-10-20';
export const TUTOR_ID = '70000000-0000-4000-8000-000000000001';

const tutor = {
  user_id: TUTOR_ID,
  display_name: 'Ana Tutora',
  state: 'active',
} as const;

const terms = (
  n: number,
  effectiveFrom: string,
  status: PayTerms['status'],
  payFrequency: PayTerms['pay_frequency'],
  salary: string | null,
): PayTerms => ({
  id: `71000000-0000-4000-8000-00000000000${n}`,
  effective_from: effectiveFrom,
  pay_frequency: payFrequency,
  salary_amount: salary,
  status,
});

const rate = (
  n: number,
  kind: CompensationRate['class_kind'],
  effectiveFrom: string,
  status: CompensationRate['status'],
  model: CompensationRate['rate_model'],
  value: string | null,
): CompensationRate => ({
  id: `72000000-0000-4000-8000-00000000000${n}`,
  class_kind: kind,
  effective_from: effectiveFrom,
  rate_model: model,
  rate: value,
  status,
});

const compensation = (
  partial: Pick<TutorCompensation, 'terms' | 'rates'>,
): TutorCompensation => ({ tutor, today: TODAY, ...partial });

export const TERMS_BIWEEKLY = terms(
  1,
  '2026-08-01',
  'current',
  'biweekly',
  null,
);
export const TERMS_SALARY = terms(
  2,
  '2026-09-01',
  'current',
  'monthly',
  '18000.00',
);
export const TERMS_STOP = terms(3, '2026-10-01', 'current', null, null);

export const RATE_PAST = rate(
  1,
  'individual',
  '2026-01-05',
  'past',
  'per_hour',
  '200.0000',
);
export const RATE_CURRENT = rate(
  2,
  'individual',
  '2026-08-01',
  'current',
  'per_hour',
  '250.1250',
);
export const RATE_UPCOMING = rate(
  3,
  'individual',
  '2026-12-01',
  'upcoming',
  'per_hour',
  '275.0000',
);
export const RATE_GROUP_ZERO = rate(
  4,
  'group',
  '2026-08-01',
  'current',
  'per_session',
  '0.0000',
);
export const RATE_COURSE_NONE = rate(
  5,
  'course',
  '2026-09-01',
  'current',
  null,
  null,
);

/** Paid by the hour, with an upcoming change and history. */
export const HOURLY = compensation({
  terms: [TERMS_BIWEEKLY],
  rates: [RATE_UPCOMING, RATE_CURRENT, RATE_PAST],
});

export const SALARIED = compensation({ terms: [TERMS_SALARY], rates: [] });

export const SALARY_PLUS = compensation({
  terms: [TERMS_SALARY],
  rates: [RATE_CURRENT],
});

export const STOPPED = compensation({
  terms: [TERMS_STOP, TERMS_BIWEEKLY],
  rates: [RATE_CURRENT, RATE_COURSE_NONE],
});

export const NOT_SET_UP = compensation({ terms: [], rates: [] });

const listItem = (n: number, partial: Partial<PayrollTutor>): PayrollTutor => ({
  user_id: `70000000-0000-4000-8000-00000000000${n}`,
  display_name: `Tutor ${n}`,
  username: `tutor.${n}`,
  state: 'active',
  current_terms: null,
  current_rate_kinds: [],
  ...partial,
});

export const TUTOR_HOURLY = listItem(1, {
  display_name: 'Ana Tutora',
  username: 'ana.tutora',
  current_terms: { pay_frequency: 'biweekly', salary_amount: null },
  current_rate_kinds: ['individual', 'group'],
});
export const TUTOR_SALARIED = listItem(2, {
  display_name: 'Beto Salario',
  username: 'beto.salario',
  current_terms: { pay_frequency: 'monthly', salary_amount: '18000.00' },
});
/** No current terms: never set up, or stopped (the API can't tell, D1). */
export const TUTOR_NO_TERMS = listItem(3, {
  display_name: 'Carla Nueva',
  username: 'carla.nueva',
  state: 'pending',
});

export const TUTORS_PAGE: PayrollTutorPage = {
  items: [TUTOR_HOURLY, TUTOR_SALARIED, TUTOR_NO_TERMS],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_PAGE: PayrollTutorPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const SETTINGS_OFF: PayrollSettings = {
  payroll_starts_on: null,
  biweekly_anchor_on: '2026-01-05',
  salary_proration_basis: 'calendar_days',
  start_locked: false,
  anchor_locked: false,
  version: 1,
  updated_at: '2026-10-01T00:00:00Z',
};

export const SETTINGS_ON: PayrollSettings = {
  ...SETTINGS_OFF,
  payroll_starts_on: '2026-09-01',
  version: 2,
};

export const SETTINGS_LOCKED: PayrollSettings = {
  ...SETTINGS_ON,
  salary_proration_basis: 'weekdays',
  start_locked: true,
  anchor_locked: true,
  version: 3,
};
