import type { AccessRequirement } from '@/features/auth/access';

// What the header's Billing item and `/billing` require (#366 A2): holders
// of billing.view or billing.manage. Kept apart from the pages so the
// router and the navigation don't load them.
export const BILLING_ACCESS = {
  anyPermission: ['billing.view', 'billing.manage'],
} as const satisfies AccessRequirement;
