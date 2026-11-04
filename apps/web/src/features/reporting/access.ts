import type { AccessRequirement } from '@/features/auth/access';

// What the Reports nav item and routes require (M7-14b A1), kept apart from
// the pages so the router and the navigation don't load them. Owners and
// Finance Staff hold it (#117); Administrators don't, even with
// payroll.finalize (ADR-0017 E12). The API checks every request.
export const FINANCE_REPORT_ACCESS = {
  permission: 'finance.report.view',
} as const satisfies AccessRequirement;
