import type { AccessRequirement } from '@/features/auth/access';

// What the Expenses nav item and routes require (M7-11b A1), kept apart from
// the pages so the router and the navigation don't load them. Owners,
// Administrators and Finance Staff hold it (ADR-0017 E12); the API checks
// every request.
export const EXPENSE_CONFIGURE_ACCESS = {
  permission: 'expense.configure',
} as const satisfies AccessRequirement;
