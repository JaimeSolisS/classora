import type { AccessRequirement } from '@/features/auth/access';

// What the header's items and the pay routes require (#425 A1), kept apart
// from the pages so the router and the navigation don't load them. Finance
// Staff and Owners hold both permissions; each page still checks its own.
export const PAYROLL_ACCESS = {
  anyPermission: ['payroll.view', 'payroll.manage'],
} as const satisfies AccessRequirement;

/** `/my-pay`: tutors read their own terms and rates (the API decides). */
export const MY_PAY_ACCESS = {
  anyRole: ['tutor'],
} as const satisfies AccessRequirement;
