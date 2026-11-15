import type { AccessRequirement } from '@/features/auth/access';

// Who opens the groups pages (#234 A1-A3; #246 B1; UX only: the API decides
// the data): staff with schedule.view (every group, the roster), a student
// or a guardian (the groups of their visible students) or a tutor (the
// groups they teach). The nav item, /groups and /groups/:groupId share it.
export const GROUPS_ACCESS = {
  anyOf: [
    { permission: 'schedule.view' },
    { anyRole: ['student', 'tutor', 'guardian'] },
  ],
} as const satisfies AccessRequirement;
