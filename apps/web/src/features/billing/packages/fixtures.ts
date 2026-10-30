import {
  SUBJECT_ALGEBRA,
  V_CURRENT,
  V_PAST,
  V_UPCOMING,
} from '../pricing/fixtures';
import type { ChargeDetail } from '../accounts/api';
import { STUDENT_ANA, STUDENT_BRUNO } from '../tuition/fixtures';
import type {
  ChargeItem,
  PackageCancelled,
  PackageDetail,
  PackagePage,
  PackageProduct,
  PackageProductDetail,
  PackageProductPage,
  SoldPackage,
} from './api';

// Package fixtures (#377 G1, G2) for the tests and the stories. The
// center's "today" in them is 2026-10-20.

export { STUDENT_ANA, STUDENT_BRUNO };

export const FAMILY_LOPEZ = {
  id: '50000000-0000-4000-8000-000000000002',
  name: 'Familia López',
};
const FAMILY_DIAZ = {
  id: '50000000-0000-4000-8000-000000000001',
  name: 'Familia Díaz',
};
const ALGEBRA = { id: SUBJECT_ALGEBRA.id, name: SUBJECT_ALGEBRA.name };

export const PRODUCT_SESSIONS: PackageProduct = {
  id: 'c0000000-0000-4000-8000-000000000001',
  name: 'Álgebra 10 sesiones',
  unit: 'sessions',
  quantity: 10,
  validity_days: 90,
  subject: ALGEBRA,
  is_active: true,
  current_amount: '2000.00',
  version: 1,
};
export const PRODUCT_HOURS: PackageProduct = {
  id: 'c0000000-0000-4000-8000-000000000002',
  name: 'Bolsa 20 horas',
  unit: 'minutes',
  quantity: 1200,
  validity_days: 180,
  subject: null,
  is_active: true,
  current_amount: '4500.00',
  version: 3,
};
export const PRODUCT_NOT_OFFERED: PackageProduct = {
  id: 'c0000000-0000-4000-8000-000000000003',
  name: 'Física 5 sesiones',
  unit: 'sessions',
  quantity: 5,
  validity_days: 30,
  subject: null,
  is_active: true,
  current_amount: null,
  version: 1,
};
export const PRODUCT_INACTIVE: PackageProduct = {
  id: 'c0000000-0000-4000-8000-000000000004',
  name: 'Repaso 75 minutos',
  unit: 'minutes',
  quantity: 75,
  validity_days: 1,
  subject: null,
  is_active: false,
  current_amount: '300.00',
  version: 2,
};

export const PRODUCTS_PAGE: PackageProductPage = {
  items: [
    PRODUCT_SESSIONS,
    PRODUCT_HOURS,
    PRODUCT_NOT_OFFERED,
    PRODUCT_INACTIVE,
  ],
  total: 4,
  limit: 25,
  offset: 0,
};
export const EMPTY_PRODUCTS_PAGE: PackageProductPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
/** The sell dialog's choices (active products only). */
export const ACTIVE_PRODUCTS = [
  PRODUCT_SESSIONS,
  PRODUCT_HOURS,
  PRODUCT_NOT_OFFERED,
];

export const PRODUCT_DETAIL: PackageProductDetail = {
  ...PRODUCT_SESSIONS,
  versions: [V_UPCOMING, V_CURRENT, V_PAST],
};
export const PRODUCT_INACTIVE_DETAIL: PackageProductDetail = {
  ...PRODUCT_INACTIVE,
  versions: [V_CURRENT],
};

export const PACKAGE_ACTIVE: SoldPackage = {
  id: 'd0000000-0000-4000-8000-000000000001',
  student: STUDENT_ANA,
  family: FAMILY_LOPEZ,
  product: { id: PRODUCT_SESSIONS.id, name: PRODUCT_SESSIONS.name },
  unit: 'sessions',
  units_granted: 10,
  remaining_units: 8,
  subject: ALGEBRA,
  purchased_on: '2026-10-01',
  expires_on: '2026-12-30',
  status: 'active',
  purchase_charge_id: 'e0000000-0000-4000-8000-000000000001',
};
export const PACKAGE_EXHAUSTED: SoldPackage = {
  id: 'd0000000-0000-4000-8000-000000000002',
  student: STUDENT_BRUNO,
  family: FAMILY_DIAZ,
  product: { id: PRODUCT_HOURS.id, name: PRODUCT_HOURS.name },
  unit: 'minutes',
  units_granted: 1200,
  remaining_units: 0,
  subject: null,
  purchased_on: '2026-06-01',
  expires_on: '2026-11-28',
  status: 'exhausted',
  purchase_charge_id: 'e0000000-0000-4000-8000-000000000002',
};
export const PACKAGE_EXPIRED: SoldPackage = {
  id: 'd0000000-0000-4000-8000-000000000003',
  student: STUDENT_ANA,
  family: FAMILY_LOPEZ,
  product: { id: PRODUCT_HOURS.id, name: PRODUCT_HOURS.name },
  unit: 'minutes',
  units_granted: 1200,
  remaining_units: 75,
  subject: null,
  purchased_on: '2026-03-01',
  expires_on: '2026-08-28',
  status: 'expired',
  purchase_charge_id: 'e0000000-0000-4000-8000-000000000003',
};

export const PACKAGES_PAGE: PackagePage = {
  items: [PACKAGE_ACTIVE, PACKAGE_EXHAUSTED, PACKAGE_EXPIRED],
  total: 3,
  limit: 25,
  offset: 0,
};
export const EMPTY_PACKAGES_PAGE: PackagePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const SESSION_ID = 'f0000000-0000-4000-8000-000000000001';

export const PACKAGE_ACTIVE_DETAIL: PackageDetail = {
  ...PACKAGE_ACTIVE,
  transactions: [
    {
      id: 'a1000000-0000-4000-8000-000000000001',
      kind: 'grant',
      units: 10,
      class_session_id: null,
      session_starts_at: null,
      created_at: '2026-10-01T16:00:00Z',
    },
    {
      id: 'a1000000-0000-4000-8000-000000000002',
      kind: 'consume',
      units: -1,
      class_session_id: SESSION_ID,
      // 2026-10-05 18:00 in Mexico City.
      session_starts_at: '2026-10-06T00:00:00Z',
      created_at: '2026-10-06T02:00:00Z',
    },
    {
      id: 'a1000000-0000-4000-8000-000000000003',
      kind: 'consume',
      units: -1,
      class_session_id: 'f0000000-0000-4000-8000-000000000002',
      session_starts_at: '2026-10-12T22:00:00Z',
      created_at: '2026-10-13T01:00:00Z',
    },
  ],
};

/** Just sold: only the grant (the sell flow's 201). */
export const PACKAGE_SOLD_DETAIL: PackageDetail = {
  ...PACKAGE_ACTIVE,
  id: 'd0000000-0000-4000-8000-000000000009',
  remaining_units: 10,
  purchased_on: '2026-10-20',
  expires_on: '2027-01-18',
  purchase_charge_id: 'e0000000-0000-4000-8000-000000000009',
  transactions: [PACKAGE_ACTIVE_DETAIL.transactions[0]!],
};

export const PACKAGE_EXPIRED_DETAIL: PackageDetail = {
  ...PACKAGE_EXPIRED,
  transactions: [
    {
      id: 'a3000000-0000-4000-8000-000000000001',
      kind: 'grant',
      units: 1200,
      class_session_id: null,
      session_starts_at: null,
      created_at: '2026-03-01T16:00:00Z',
    },
    {
      id: 'a3000000-0000-4000-8000-000000000002',
      kind: 'consume',
      units: -90,
      class_session_id: SESSION_ID,
      session_starts_at: '2026-04-10T23:00:00Z',
      created_at: '2026-04-11T01:00:00Z',
    },
    {
      id: 'a3000000-0000-4000-8000-000000000003',
      kind: 'reversal',
      units: -1035,
      class_session_id: null,
      session_starts_at: null,
      created_at: '2026-05-02T17:00:00Z',
    },
    {
      id: 'a3000000-0000-4000-8000-000000000004',
      kind: 'expire',
      units: -75,
      class_session_id: null,
      session_starts_at: null,
      created_at: '2026-08-29T07:40:00Z',
    },
  ],
};

export const PURCHASE_CHARGE: ChargeItem = {
  id: PACKAGE_ACTIVE.purchase_charge_id!,
  student: { id: STUDENT_ANA.id, name: STUDENT_ANA.name },
  kind: 'package_purchase',
  service_date: '2026-10-01',
  due_date: '2026-10-01',
  original_due_date: '2026-10-01',
  description: null,
  list_amount: '2000.00',
  discount_amount: '200.00',
  amount: '1800.00',
  adjustments_total: '0.00',
  net: '1800.00',
  allocated: '0.00',
  open: '1800.00',
  status: 'pending',
  posted_at: '2026-10-01T16:00:00Z',
  posted_by_user_id: null,
  class_session_id: null,
  group_enrollment_id: null,
  cohort_enrollment_id: null,
};

export function searchFixtureFamilies(q = '') {
  const text = q.toLowerCase();
  return Promise.resolve(
    [FAMILY_LOPEZ, FAMILY_DIAZ].filter((family) =>
      family.name.toLowerCase().includes(text),
    ),
  );
}

// #386 C1: a cancelled package, and the purchase charge's detail (the
// cancellation estimate reads its net).
export const PACKAGE_CANCELLED: SoldPackage = {
  ...PACKAGE_ACTIVE,
  id: 'd0000000-0000-4000-8000-000000000004',
  remaining_units: 0,
  status: 'cancelled',
  purchase_charge_id: 'e0000000-0000-4000-8000-000000000004',
};
export const PURCHASE_CHARGE_DETAIL: ChargeDetail = {
  ...PURCHASE_CHARGE,
  adjustments: [],
  allocations: [],
};
export const CANCELLED: PackageCancelled = {
  package: {
    ...PACKAGE_ACTIVE_DETAIL,
    remaining_units: 0,
    status: 'cancelled',
  },
  units_reversed: 8,
  credit: '1440.00',
  adjustment_id: 'a2000000-0000-4000-8000-000000000001',
  account_credit_after: '0.00',
};
