import type {
  Option,
  PriceVersion,
  ServicePrice,
  ServicePriceDetail,
  ServicePricePage,
  SubjectOption,
} from './api';

// Price catalog fixtures (#363 F1, F3) for the tests and the stories. The
// center's "today" in them is 2026-10-20.

export const TODAY = '2026-10-20';

export const SUBJECT_ALGEBRA = {
  id: '30000000-0000-4000-8000-000000000001',
  name: 'Álgebra',
  education_level_id: '20000000-0000-4000-8000-000000000002',
  education_level_name: 'Secundaria',
};
export const SUBJECT_PHYSICS = {
  id: '30000000-0000-4000-8000-000000000002',
  name: 'Física',
  education_level_id: '20000000-0000-4000-8000-000000000003',
  education_level_name: 'Preparatoria',
};

export const LEVEL_OPTIONS: Option[] = [
  { id: '20000000-0000-4000-8000-000000000001', name: 'Primaria' },
  { id: '20000000-0000-4000-8000-000000000002', name: 'Secundaria' },
  { id: '20000000-0000-4000-8000-000000000003', name: 'Preparatoria' },
];

export const SUBJECT_OPTIONS: SubjectOption[] = [
  {
    id: SUBJECT_ALGEBRA.id,
    name: SUBJECT_ALGEBRA.name,
    levelName: SUBJECT_ALGEBRA.education_level_name,
  },
  {
    id: SUBJECT_PHYSICS.id,
    name: SUBJECT_PHYSICS.name,
    levelName: SUBJECT_PHYSICS.education_level_name,
  },
];

export const GROUP_OPTIONS: Option[] = [
  { id: '40000000-0000-4000-8000-000000000001', name: 'Álgebra sábados' },
];
export const COHORT_OPTIONS: Option[] = [
  { id: '50000000-0000-4000-8000-000000000001', name: 'Física 2027-A' },
];

function version(
  id: string,
  effectiveFrom: string,
  amount: string | null,
  status: PriceVersion['status'],
): PriceVersion {
  return {
    id,
    effective_from: effectiveFrom,
    amount,
    status,
    created_at: '2026-10-01T16:00:00Z',
  };
}

export const V_PAST = version(
  '60000000-0000-4000-8000-000000000001',
  '2026-01-05',
  '300.00',
  'past',
);
export const V_CURRENT = version(
  '60000000-0000-4000-8000-000000000002',
  '2026-08-01',
  '350.00',
  'current',
);
export const V_UPCOMING = version(
  '60000000-0000-4000-8000-000000000003',
  '2026-11-03',
  '380.00',
  'upcoming',
);
export const V_NOT_OFFERED = version(
  '60000000-0000-4000-8000-000000000004',
  '2026-09-01',
  null,
  'current',
);

const BASE = {
  created_at: '2026-10-01T16:00:00Z',
  education_level: null,
  duration_minutes: null,
  cohort: null,
  group: null,
  period_frequency: null,
};

/** Individual, per session, 60 min, current 350.00, next 380.00. */
export const PRICE_WITH_NEXT: ServicePrice = {
  ...BASE,
  id: '70000000-0000-4000-8000-000000000001',
  class_type: 'individual',
  pricing_model: 'per_session',
  subject: SUBJECT_ALGEBRA,
  duration_minutes: 60,
  current: V_CURRENT,
  next: V_UPCOMING,
};

/** Group, flat fee, one group, not offered. */
export const PRICE_NOT_OFFERED: ServicePrice = {
  ...BASE,
  id: '70000000-0000-4000-8000-000000000002',
  class_type: 'group',
  pricing_model: 'full_course',
  subject: SUBJECT_ALGEBRA,
  group: { id: GROUP_OPTIONS[0]!.id, name: GROUP_OPTIONS[0]!.name },
  current: V_NOT_OFFERED,
  next: null,
};

/** Course fee, one level, no price in effect yet. */
export const PRICE_NO_PRICE_YET: ServicePrice = {
  ...BASE,
  id: '70000000-0000-4000-8000-000000000003',
  class_type: 'course',
  pricing_model: 'full_course',
  subject: SUBJECT_PHYSICS,
  education_level: { id: LEVEL_OPTIONS[2]!.id, name: 'Preparatoria' },
  current: null,
  next: null,
};

export const PRICES_PAGE: ServicePricePage = {
  items: [PRICE_WITH_NEXT, PRICE_NOT_OFFERED, PRICE_NO_PRICE_YET],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_PAGE: ServicePricePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

/** PRICE_WITH_NEXT with its three versions, newest first. */
export const PRICE_DETAIL: ServicePriceDetail = {
  ...PRICE_WITH_NEXT,
  versions: [V_UPCOMING, V_CURRENT, V_PAST],
};

/** PRICE_DETAIL after its upcoming version was deleted. */
export const PRICE_DETAIL_WITHOUT_UPCOMING: ServicePriceDetail = {
  ...PRICE_WITH_NEXT,
  next: null,
  versions: [V_CURRENT, V_PAST],
};

/** #374 B: individual, per period, monthly, current 1,500.00. */
export const PRICE_PER_PERIOD: ServicePrice = {
  ...BASE,
  id: '70000000-0000-4000-8000-000000000004',
  class_type: 'individual',
  pricing_model: 'per_period',
  period_frequency: 'monthly',
  subject: SUBJECT_ALGEBRA,
  current: version(
    '60000000-0000-4000-8000-000000000005',
    '2026-08-01',
    '1500.00',
    'current',
  ),
  next: null,
};

/** #374 B: a group per-period price scoped to the Saturday group. */
export const PRICE_GROUP_PER_PERIOD: ServicePrice = {
  ...BASE,
  id: '70000000-0000-4000-8000-000000000005',
  class_type: 'group',
  pricing_model: 'per_period',
  period_frequency: 'biweekly',
  subject: SUBJECT_ALGEBRA,
  group: { id: GROUP_OPTIONS[0]!.id, name: GROUP_OPTIONS[0]!.name },
  current: version(
    '60000000-0000-4000-8000-000000000006',
    '2026-08-01',
    '700.00',
    'current',
  ),
  next: null,
};
