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

/**
 * The period list and review pages (#110b A1): Finance and Owners
 * (`payroll.view`, `payroll.prepare`) and a delegated Administrator
 * (`payroll.finalize`). The API decides what each one may read and do.
 */
export const PAYROLL_REVIEW_ACCESS = {
  anyPermission: ['payroll.view', 'payroll.prepare', 'payroll.finalize'],
} as const satisfies AccessRequirement;

/** The header's Payroll item and `/payroll` (#110b A2, A3): either. */
export const PAYROLL_ANY_ACCESS = {
  anyOf: [PAYROLL_ACCESS, PAYROLL_REVIEW_ACCESS],
} as const satisfies AccessRequirement;
