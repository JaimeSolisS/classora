import type { AccessRequirement } from '@/features/auth/access';

// What the Expenses nav item and routes require (M7-11b A1), kept apart from
// the pages so the router and the navigation don't load them. Owners,
// Administrators and Finance Staff hold it (ADR-0017 E12); the API checks
// every request.
export const EXPENSE_CONFIGURE_ACCESS = {
  permission: 'expense.configure',
} as const satisfies AccessRequirement;

// The expense records (M7-12b A1, A2): Owners and Finance Staff (#115).
// Recording, adjusting and voiding need expense.manage, and reversing a
// payment needs finance.refund.approve too (expenses/rules.ts).
export const EXPENSE_VIEW_ACCESS = {
  permission: 'expense.view',
} as const satisfies AccessRequirement;
