import type {
  CompensationRate,
  PayItem,
  PayItemPage,
  PayItemType,
  PayrollSettings,
  PayrollTutor,
  PayrollTutorPage,
  PayRule,
  PayRuleOverview,
  PayTerms,
  TutorCompensation,
  UnresolvedItem,
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

// ------------------------------------------------- pay rules (#444 D2)

const payRule = (
  n: number,
  event: PayRule['event'],
  effectiveFrom: string,
  mode: PayRule['mode'],
  percent: string | null = null,
  amount: string | null = null,
): PayRule => ({
  id: `73000000-0000-4000-8000-00000000000${n}`,
  event,
  effective_from: effectiveFrom,
  mode,
  percent,
  amount,
  created_by_user_id: null,
  created_at: '2026-01-01T00:00:00Z',
});

export const LATE_SEED = payRule(1, 'late_cancellation', '2000-01-01', 'none');
export const NO_SHOW_SEED = payRule(
  2,
  'no_show',
  '2000-01-01',
  'percent',
  '100.0000',
);
export const NO_SHOW_CURRENT = payRule(
  3,
  'no_show',
  '2026-09-01',
  'percent',
  '12.5000',
);
export const NO_SHOW_UPCOMING = payRule(
  4,
  'no_show',
  '2026-12-01',
  'fixed',
  null,
  '150.00',
);
export const NO_SHOW_LATER = payRule(5, 'no_show', '2027-01-01', 'none');
export const MAKEUP_SEED = payRule(
  6,
  'makeup',
  '2000-01-01',
  'percent',
  '100.0000',
);

/** The three seeds: what a new center has. */
export const RULES_SEEDED: PayRuleOverview = {
  today: TODAY,
  late_cancellation: { current: LATE_SEED, upcoming: [], history: [] },
  no_show: { current: NO_SHOW_SEED, upcoming: [], history: [] },
  makeup: { current: MAKEUP_SEED, upcoming: [], history: [] },
};

/** A no-show rule with two upcoming versions and one earlier. */
export const RULES_VERSIONED: PayRuleOverview = {
  ...RULES_SEEDED,
  no_show: {
    current: NO_SHOW_CURRENT,
    upcoming: [NO_SHOW_UPCOMING, NO_SHOW_LATER],
    history: [NO_SHOW_SEED],
  },
};

// --------------------------------------------- unresolved work (#444 D2)

const unresolved = (
  n: number,
  partial: Partial<UnresolvedItem>,
): UnresolvedItem => ({
  class_session_id: `74000000-0000-4000-8000-00000000000${n}`,
  tutor_user_id: TUTOR_ID,
  tutor_display_name: 'Ana Tutora',
  service_date: '2026-10-15',
  class_kind: 'individual',
  source: 'closeout',
  reason: 'no_rate',
  blocking: true,
  ...partial,
});

export const UNRESOLVED_NO_RATE = unresolved(1, {});
export const UNRESOLVED_NO_TERMS = unresolved(2, {
  service_date: '2026-10-14',
  class_kind: 'group',
  source: 'cancellation',
  reason: 'no_pay_terms',
});
export const UNRESOLVED_BEFORE_FIRST = unresolved(3, {
  service_date: '2026-08-03',
  class_kind: 'course',
  reason: 'before_first_period',
  blocking: false,
});

// ----------------------------------- bonuses and deductions (#454 F2)

const payItemType = (
  n: number,
  name: string,
  direction: PayItemType['direction'],
  isActive = true,
): PayItemType => ({
  id: `75000000-0000-4000-8000-00000000000${n}`,
  name,
  direction,
  is_active: isActive,
  version: 1,
});

export const TYPE_BONUS = payItemType(1, 'Bono de puntualidad', 'bonus');
export const TYPE_DEDUCTION = payItemType(2, 'Préstamo', 'deduction');
export const TYPE_RETIRED = payItemType(3, 'Bono viejo', 'bonus', false);
/** Active first, as the API returns them. */
export const PAY_ITEM_TYPES = [TYPE_BONUS, TYPE_DEDUCTION, TYPE_RETIRED];

const payItem = (n: number, partial: Partial<PayItem>): PayItem => ({
  id: `76000000-0000-4000-8000-00000000000${n}`,
  tutor_user_id: TUTOR_ID,
  pay_item_type: {
    id: TYPE_BONUS.id,
    name: TYPE_BONUS.name,
    direction: 'bonus',
  },
  description: 'Sin faltas en el mes',
  amount: '500.00',
  recurrence: 'one_time',
  effective_from: '2026-10-15',
  effective_to: null,
  version: 1,
  applied: false,
  placed_period: null,
  created_at: '2026-10-15T12:00:00Z',
  ...partial,
});

const DEDUCTION_TYPE: PayItem['pay_item_type'] = {
  id: TYPE_DEDUCTION.id,
  name: TYPE_DEDUCTION.name,
  direction: 'deduction',
};

/** B2's four states, and a recurring one with and without an end. */
export const ITEM_WAITING = payItem(1, {});
export const ITEM_PLACED = payItem(2, {
  placed_period: {
    id: '77000000-0000-4000-8000-000000000001',
    starts_on: '2026-10-01',
    ends_on: '2026-10-31',
    status: 'open',
  },
});
export const ITEM_PREPARED = payItem(3, {
  placed_period: {
    id: '77000000-0000-4000-8000-000000000002',
    starts_on: '2026-09-01',
    ends_on: '2026-09-30',
    status: 'prepared',
  },
});
export const ITEM_PAID = payItem(4, {
  pay_item_type: DEDUCTION_TYPE,
  description: 'Anticipo',
  amount: '1250.50',
  applied: true,
});
export const ITEM_RECURRING = payItem(5, {
  pay_item_type: DEDUCTION_TYPE,
  description: 'Abono del préstamo',
  amount: '300.00',
  recurrence: 'recurring',
  effective_from: '2026-09-01',
});
export const ITEM_ENDED = payItem(6, {
  recurrence: 'recurring',
  description: 'Apoyo de transporte',
  effective_from: '2026-08-01',
  effective_to: '2026-12-31',
  applied: true,
});
export const ITEM_LONG = payItem(7, {
  description: 'Descripción-sin-espacios-'.repeat(20),
});
export const PAY_ITEMS: PayItem[] = [
  ITEM_WAITING,
  ITEM_PLACED,
  ITEM_PREPARED,
  ITEM_PAID,
  ITEM_RECURRING,
  ITEM_ENDED,
];

export const payItemPage = (
  items: PayItem[],
  total = items.length,
  offset = 0,
): PayItemPage => ({ items, total, limit: 25, offset });
