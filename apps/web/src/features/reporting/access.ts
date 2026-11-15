import type { AccessRequirement } from '@/features/auth/access';

// What the Reports nav item and routes require (M7-14b A1), kept apart from
// the pages so the router and the navigation don't load them. Owners and
// Finance Staff hold it (#117); Administrators don't, even with
// payroll.finalize (ADR-0017 E12). The API checks every request.
export const FINANCE_REPORT_ACCESS = {
  permission: 'finance.report.view',
} as const satisfies AccessRequirement;

// The KPI page (M8-09b A1, #126 E3): "Reportes" opens it for holders of
// either permission; each section shows only with its API route's
// permission. Tutor performance (K6) needs both schedule.view and tutor.view
// (a tutor holds tutor.view only; Finance Staff hold neither). `canAccess`
// applies `permission` and `anyPermission` together.
export const KPI_PAGE_ACCESS = {
  anyPermission: ['finance.report.view', 'schedule.view'],
} as const satisfies AccessRequirement;

/** K1 revenue trend, K2 course profitability, K3 balances. */
export const KPI_FINANCE_ACCESS = FINANCE_REPORT_ACCESS;

/** K4 room utilization, K5 student retention. */
export const KPI_OPERATIONS_ACCESS = {
  permission: 'schedule.view',
} as const satisfies AccessRequirement;

/** K6 tutor performance (K7 center activity). */
export const KPI_TUTOR_ACCESS = {
  permission: 'schedule.view',
  anyPermission: ['tutor.view'],
} as const satisfies AccessRequirement;
