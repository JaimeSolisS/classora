import type { SessionUserBody } from '@/features/auth/session';
import { TEST_USER } from '@/test/api-stub';

// Vitest only (M7-11b F1): the session users of the finance suites.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const OWNER = user(
  ['owner'],
  [
    'user.view',
    'expense.configure',
    'expense.view',
    'expense.manage',
    'finance.refund.approve',
  ],
);
export const ADMIN = user(
  ['administrator'],
  ['user.view', 'expense.configure'],
);
export const FINANCE = user(
  ['finance_staff'],
  ['payroll.view', 'expense.configure', 'expense.view', 'expense.manage'],
);
/** An Administrator delegated refunds: still no expense.view (#115 E6). */
export const ADMIN_REFUNDS = user(
  ['administrator'],
  ['user.view', 'expense.configure', 'finance.refund.approve'],
);
export const COORDINATOR = user(['academic_coordinator'], ['schedule.view']);
export const TUTOR = user(['tutor'], ['tutor.view']);
export const STUDENT = user(['student'], ['student.view']);
export const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);

export const CATEGORIES = 'GET /api/v1/finance/expense-categories';
export const CATEGORY_POST = 'POST /api/v1/finance/expense-categories';
export const categoryPatch = (id: string) =>
  `PATCH /api/v1/finance/expense-categories/${id}`;
export const VENDORS = 'GET /api/v1/finance/vendors';
export const VENDOR_POST = 'POST /api/v1/finance/vendors';
export const vendorPatch = (id: string) =>
  `PATCH /api/v1/finance/vendors/${id}`;

// M7-12b: the expense routes.
export const EXPENSES = 'GET /api/v1/finance/expenses';
export const EXPENSE_POST = 'POST /api/v1/finance/expenses';
export const expenseGet = (id: string) => `GET /api/v1/finance/expenses/${id}`;
export const paymentPost = (id: string) =>
  `POST /api/v1/finance/expenses/${id}/payments`;
export const adjustmentPost = (id: string) =>
  `POST /api/v1/finance/expenses/${id}/adjustments`;
export const reversalPost = (id: string) =>
  `POST /api/v1/finance/expense-payments/${id}/reversal`;
export const METHODS = 'GET /api/v1/billing/payment-methods';
export const REAUTH = 'POST /api/v1/auth/reauthenticate';
