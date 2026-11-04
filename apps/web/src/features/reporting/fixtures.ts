import type { Session } from '@/features/auth/session';
import type { Balances, Profitability } from './api';

// Report fixtures (M7-14b E1, E2), taken from #117's oracle: November 2026
// on the accrual basis (D1), on the cash basis (D2), and the balances as of
// 2026-12-10 (E1, E2; receivables from E3's standalone account).

export const ORACLE_TODAY = '2026-12-10';

const RENT = {
  id: '86000000-0000-4000-8000-000000000001',
  name: 'Renta',
  system_key: 'rent',
  is_active: true,
} as const;
/** A seeded category renamed by staff: its name in every language. */
const UTILITIES_RENAMED = {
  id: '86000000-0000-4000-8000-000000000002',
  name: 'Luz y agua (oficina)',
  system_key: null,
  is_active: true,
} as const;
const MOBILIARIO = {
  id: '86000000-0000-4000-8000-000000000003',
  name: 'Mobiliario',
  system_key: null,
  is_active: false,
} as const;
const MAINTENANCE = {
  id: '86000000-0000-4000-8000-000000000004',
  name: 'Mantenimiento',
  system_key: 'maintenance',
  is_active: true,
} as const;

const LANDLORD = {
  id: '87000000-0000-4000-8000-000000000001',
  name: 'Inmobiliaria Ficticia',
  is_active: true,
} as const;
const OLD_SUPPLIER = {
  id: '87000000-0000-4000-8000-000000000002',
  name: 'Muebles de Prueba',
  is_active: false,
} as const;

/** D1: accrual, Nov 1–30. */
export const ACCRUAL_NOVEMBER: Profitability = {
  basis: 'accrual',
  from: '2026-11-01',
  to: '2026-11-30',
  today: ORACLE_TODAY,
  class_rows: [
    {
      class_type: 'individual',
      revenue: '4491.67',
      direct_tutor_cost: '500.00',
      gross_profit: '3991.67',
    },
    {
      class_type: 'group',
      revenue: '1062.00',
      direct_tutor_cost: '330.00',
      gross_profit: '732.00',
    },
    {
      class_type: 'course',
      revenue: '1266.67',
      direct_tutor_cost: '400.00',
      gross_profit: '866.67',
    },
    {
      class_type: 'unallocated',
      revenue: '250.00',
      direct_tutor_cost: '3200.00',
      gross_profit: '-2950.00',
    },
  ],
  revenue_total: '7070.34',
  direct_tutor_cost_total: '4430.00',
  gross_profit: '2640.34',
  operating_expenses: {
    total: '3350.00',
    by_category: [
      { category: RENT, amount: '1500.00' },
      { category: UTILITIES_RENAMED, amount: '1350.00' },
      { category: MOBILIARIO, amount: '500.00' },
    ],
    by_vendor: [
      { vendor: null, amount: '2850.00' },
      { vendor: OLD_SUPPLIER, amount: '500.00' },
    ],
  },
  net_result: '-709.66',
};

/** D2: cash, Nov 1–30 (no class rows). */
export const CASH_NOVEMBER: Profitability = {
  basis: 'cash',
  from: '2026-11-01',
  to: '2026-11-30',
  today: ORACLE_TODAY,
  class_rows: [],
  revenue_total: '700.00',
  direct_tutor_cost_total: '900.00',
  gross_profit: '-200.00',
  operating_expenses: {
    total: '2900.00',
    by_category: [
      { category: RENT, amount: '1500.00' },
      { category: MAINTENANCE, amount: '800.00' },
      { category: UTILITIES_RENAMED, amount: '600.00' },
    ],
    by_vendor: [
      { vendor: LANDLORD, amount: '1500.00' },
      { vendor: null, amount: '1400.00' },
    ],
  },
  net_result: '-3100.00',
};

/** D5: a one-day range with nothing in it: every row present, all zero. */
export const EMPTY_DAY: Profitability = {
  basis: 'accrual',
  from: '2026-12-09',
  to: '2026-12-09',
  today: ORACLE_TODAY,
  class_rows: (['individual', 'group', 'course', 'unallocated'] as const).map(
    (class_type) => ({
      class_type,
      revenue: '0.00',
      direct_tutor_cost: '0.00',
      gross_profit: '0.00',
    }),
  ),
  revenue_total: '0.00',
  direct_tutor_cost_total: '0.00',
  gross_profit: '0.00',
  operating_expenses: { total: '0.00', by_category: [], by_vendor: [] },
  net_result: '0.00',
};

/** A month whose tutor cost is above its revenue: negative gross profit. */
export const NEGATIVE_MONTH: Profitability = {
  ...ACCRUAL_NOVEMBER,
  class_rows: ACCRUAL_NOVEMBER.class_rows.map((row) =>
    row.class_type === 'individual'
      ? {
          ...row,
          revenue: '300.00',
          direct_tutor_cost: '500.00',
          gross_profit: '-200.00',
        }
      : row,
  ),
  revenue_total: '2878.67',
  gross_profit: '-1551.33',
  net_result: '-4901.33',
};

/** E1, E2, E3: the balances on 2026-12-10. */
export const BALANCES: Balances = {
  today: ORACLE_TODAY,
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

/** The Owner's session for the stories (every link shown). */
export const REPORTS_OWNER_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.owner',
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
    display_name: 'Demo Owner',
    roles: ['owner'],
    permissions: ['finance.report.view', 'billing.view', 'expense.view'],
  },
};
