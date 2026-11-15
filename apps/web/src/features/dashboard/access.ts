import type { AccessRequirement } from '@/features/auth/access';

// Who gets the dashboard on the home page (M8-10 D1, #127 E4): holders of a
// permission that some KPI widget needs. Kept apart from the page so the
// router doesn't load it (or Recharts). Tutors, students and guardians keep
// the old home page and never ask for a layout. The API checks every
// request (the layout routes and each widget's KPI route).
export const DASHBOARD_ACCESS = {
  anyPermission: ['finance.report.view', 'schedule.view'],
} as const satisfies AccessRequirement;
