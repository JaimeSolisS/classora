import type { SessionUserBody } from '@/features/auth/session';
import type {
  ChargeDetail,
  PaymentDetail,
} from '@/features/billing/accounts/api';
import {
  ANA,
  CHARGE_DETAIL,
  CHARGE_OVERDUE,
  CHARGE_PAID,
  CHARGE_PARTIAL,
  CHARGE_PENDING,
  FAMILY_ID,
  LUIS,
  PAYMENT_DETAIL,
  PAYMENT_REFUNDED_DETAIL,
  PAYMENT_REVERSED_DETAIL,
  SUMMARY,
  TODAY,
} from '@/features/billing/accounts/fixtures';
import type {
  AccountSummary,
  FamilyPackageDetail,
  FamilyPackagePage,
  OwnFamily,
} from './api';

// Family billing portal fixtures (#388 J1, J2), built on the staff account
// fixtures (#379, #386): the staff-shaped ones carry staff-only text (a
// payment note, adjustment, reversal and refund reasons, user ids), which
// the portal must never show (E4). The center's "today" is 2026-10-20.

export { ANA, FAMILY_ID, LUIS, SUMMARY, TODAY };

export const GUARDIAN: SessionUserBody = {
  id: '00000000-0000-4000-8000-000000000388',
  username: 'maria.lopez',
  display_name: 'María López',
  roles: ['guardian'],
  permissions: ['student.view', 'guardian.view'],
  csrf_token: 'csrf-token-ddddddddddddddddddddddddddddddddddd',
  privacy_notice_required: false,
  preferences: { language: null, theme: null },
  center_time_zone: 'America/Mexico_City',
};

/** A staff user who is also a guardian (their reads aren't redacted). */
export const STAFF_GUARDIAN: SessionUserBody = {
  ...GUARDIAN,
  id: '00000000-0000-4000-8000-000000000389',
  username: 'finanzas.madre',
  display_name: 'Finanzas Madre',
  roles: ['finance_staff', 'guardian'],
  permissions: [
    'student.view',
    'guardian.view',
    'billing.view',
    'billing.manage',
    'payment.record',
  ],
};

export const OWN_FAMILY: OwnFamily = {
  family: { id: FAMILY_ID, name: 'Familia López', is_active: true },
};
export const OWN_INACTIVE: OwnFamily = {
  family: { id: FAMILY_ID, name: 'Familia López', is_active: false },
};
export const OWN_NONE: OwnFamily = { family: null };

export const OTHER_FAMILY_ID = '51000000-0000-4000-8000-000000000009';
export const OWN_OTHER: OwnFamily = {
  family: { id: OTHER_FAMILY_ID, name: 'Familia Ortiz', is_active: true },
};

export const SUMMARY_INACTIVE: AccountSummary = {
  ...SUMMARY,
  family: { ...SUMMARY.family, is_active: false },
};
export const SUMMARY_ONE_CHILD: AccountSummary = {
  ...SUMMARY,
  students: [SUMMARY.students[0]!],
};
export const SUMMARY_OTHER: AccountSummary = {
  ...SUMMARY,
  family: { id: OTHER_FAMILY_ID, name: 'Familia Ortiz', is_active: true },
  outstanding: '100.00',
};

/** A charge moved from its posted due date (#382 marker). */
export const CHARGE_MOVED = {
  ...CHARGE_PENDING,
  due_date: '2026-11-05',
  original_due_date: '2026-10-25',
};
export const CHARGE_VOID = {
  ...CHARGE_PAID,
  id: '53000000-0000-4000-8000-000000000005',
  description: 'Sesión anulada',
  status: 'void' as const,
  open: '0.00',
};

export const PORTAL_CHARGES_PAGE = {
  items: [CHARGE_MOVED, CHARGE_PARTIAL, CHARGE_OVERDUE, CHARGE_PAID],
  total: 4,
  limit: 25,
  offset: 0,
};
export const ALL_STATUSES_PAGE = {
  items: [
    CHARGE_MOVED,
    CHARGE_PARTIAL,
    CHARGE_OVERDUE,
    CHARGE_PAID,
    CHARGE_VOID,
  ],
  total: 5,
  limit: 25,
  offset: 0,
};

/**
 * CHARGE_OVERDUE with two discount lines (T4), a staff reason on its
 * cancellation credit, an adjustment kind the client doesn't know and a
 * released allocation.
 */
export const DISCOUNTED_DETAIL: ChargeDetail = {
  ...CHARGE_DETAIL,
  list_amount: '2000.00',
  discount_amount: '400.00',
  amount: '1600.00',
  discounts: [
    {
      position: 1,
      kind: 'scholarship',
      name: 'Beca deportiva',
      percent: null,
      fixed_amount: '200.00',
      amount: '200.00',
    },
    {
      position: 2,
      kind: 'sibling',
      name: 'Hermanos',
      percent: '10.0000',
      fixed_amount: null,
      amount: '200.00',
    },
  ],
  adjustments: [
    ...CHARGE_DETAIL.adjustments,
    {
      id: '5a000000-0000-4000-8000-000000000002',
      kind: 'future_kind' as ChargeDetail['adjustments'][number]['kind'],
      amount: '-10.00',
      reason: 'CANARIO-AJUSTE motivo interno',
      posted_at: '2026-09-12T16:00:00Z',
      posted_by_user_id: '00000000-0000-4000-8000-000000000001',
    },
  ],
};

/** Every staff-only field filled with a canary (E4). */
export const CANARIES = [
  'Pagó en ventanilla',
  'Clase cancelada por el centro',
  'CANARIO-AJUSTE',
  'Transferencia rechazada por el banco',
  'Pago de más',
  '00000000-0000-4000-8000-000000000001',
];

export const STAFF_PAYMENT_DETAIL: PaymentDetail = {
  ...PAYMENT_REFUNDED_DETAIL,
  reversal: PAYMENT_REVERSED_DETAIL.reversal,
  is_reversed: true,
  reversed_at: PAYMENT_REVERSED_DETAIL.reversed_at,
  refunds: PAYMENT_REFUNDED_DETAIL.refunds.map((refund) => ({
    ...refund,
    withdrawal_id: '5e000000-0000-4000-8000-000000000001',
  })),
};

export { PAYMENT_DETAIL, PAYMENT_REVERSED_DETAIL, PAYMENT_REFUNDED_DETAIL };

const PACKAGE_REFERENCE = {
  family: { id: FAMILY_ID, name: 'Familia López' },
};

export const PACKAGE_ACTIVE: FamilyPackageDetail = {
  id: '5f000000-0000-4000-8000-000000000001',
  ...PACKAGE_REFERENCE,
  student: LUIS,
  product: { id: '5f100000-0000-4000-8000-000000000001', name: 'Álgebra 10' },
  unit: 'sessions',
  units_granted: 10,
  remaining_units: 7,
  subject: { id: '5f200000-0000-4000-8000-000000000001', name: 'Álgebra' },
  purchased_on: '2026-10-05',
  // Within 14 days of TODAY: "Expires soon".
  expires_on: '2026-10-30',
  status: 'active',
  purchase_charge_id: CHARGE_PARTIAL.id,
  transactions: [
    {
      id: '5f300000-0000-4000-8000-000000000001',
      kind: 'grant',
      units: 10,
      class_session_id: null,
      session_starts_at: null,
      created_at: '2026-10-05T16:00:00Z',
    },
    {
      id: '5f300000-0000-4000-8000-000000000002',
      kind: 'consume',
      units: -1,
      class_session_id: '5f400000-0000-4000-8000-000000000001',
      session_starts_at: '2026-10-08T22:00:00Z',
      created_at: '2026-10-09T01:00:00Z',
    },
  ],
};
export const PACKAGE_HOURS: FamilyPackageDetail = {
  ...PACKAGE_ACTIVE,
  id: '5f000000-0000-4000-8000-000000000002',
  student: ANA,
  product: { id: '5f100000-0000-4000-8000-000000000002', name: 'Inglés 20 h' },
  unit: 'minutes',
  units_granted: 1200,
  remaining_units: 0,
  subject: null,
  expires_on: '2027-01-05',
  status: 'exhausted',
  transactions: [],
};

export const PACKAGES_PAGE: FamilyPackagePage = {
  // The details have the list items' fields (plus their movements).
  items: [PACKAGE_ACTIVE, PACKAGE_HOURS],
  total: 2,
  limit: 25,
  offset: 0,
};
export const EMPTY_PACKAGES_PAGE: FamilyPackagePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
