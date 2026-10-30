import type { OverduePage } from './api';

// Overdue accounts fixtures (#382 F1, F2) for the tests and the stories.
// The center's "today" in them is 2026-10-20 (America/Mexico_City).

export const LOPEZ_ID = '51000000-0000-4000-8000-000000000001';
export const ORTIZ_ID = '51000000-0000-4000-8000-000000000002';

export const OVERDUE_PAGE: OverduePage = {
  items: [
    {
      family: { id: ORTIZ_ID, name: 'Familia Ortiz', is_active: false },
      account_id: '54000000-0000-4000-8000-000000000002',
      overdue_total: '3200.00',
      overdue_charges: 3,
      oldest_due_date: '2026-06-30',
      days_overdue: 112,
      buckets: {
        days_1_30: '200.00',
        days_31_60: '1000.00',
        days_61_90: '0.00',
        days_over_90: '2000.00',
      },
      outstanding: '3400.00',
      account_credit: '0.00',
      net_balance: '3400.00',
      last_reminded_at: '2026-10-19T15:00:00Z',
    },
    {
      family: { id: LOPEZ_ID, name: 'Familia López', is_active: true },
      account_id: '54000000-0000-4000-8000-000000000001',
      overdue_total: '1500.00',
      overdue_charges: 1,
      oldest_due_date: '2026-09-15',
      days_overdue: 35,
      buckets: {
        days_1_30: '0.00',
        days_31_60: '1500.00',
        days_61_90: '0.00',
        days_over_90: '0.00',
      },
      outstanding: '2750.00',
      account_credit: '300.00',
      net_balance: '2450.00',
      last_reminded_at: null,
    },
  ],
  total: 27,
  limit: 25,
  offset: 0,
  today: '2026-10-20',
  totals: {
    accounts: 27,
    overdue_total: '48250.50',
    buckets: {
      days_1_30: '10200.00',
      days_31_60: '15050.50',
      days_61_90: '8000.00',
      days_over_90: '15000.00',
    },
  },
};

export const EMPTY_OVERDUE_PAGE: OverduePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
  today: '2026-10-20',
  totals: {
    accounts: 0,
    overdue_total: '0.00',
    buckets: {
      days_1_30: '0.00',
      days_31_60: '0.00',
      days_61_90: '0.00',
      days_over_90: '0.00',
    },
  },
};
