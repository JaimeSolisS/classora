import type { SessionUserBody } from '@/features/auth/session';
import { TEST_USER } from '@/test/api-stub';

// Vitest only (M7-11b F1): the session users of the finance suites.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const OWNER = user(['owner'], ['user.view', 'expense.configure']);
export const ADMIN = user(
  ['administrator'],
  ['user.view', 'expense.configure'],
);
export const FINANCE = user(
  ['finance_staff'],
  ['payroll.view', 'expense.configure'],
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
