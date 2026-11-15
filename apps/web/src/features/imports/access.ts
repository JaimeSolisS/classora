import type { AccessRequirement } from '@/features/auth/access';

// What the Imports nav item and routes require (M8-05b A1, E1), kept apart
// from the pages so the router and the navigation don't load them: holders
// of a permission of some import kind (student.manage for students,
// user.invite for tutors, #123: schedule.manage for recurring schedules and
// billing.manage for opening balances). The page lists only the kinds the
// user may import; the API checks every request.
export const IMPORTS_ACCESS = {
  anyPermission: [
    'student.manage',
    'user.invite',
    'schedule.manage',
    'billing.manage',
  ],
} as const satisfies AccessRequirement;
