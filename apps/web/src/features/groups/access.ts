import type { AccessRequirement } from '@/features/auth/access';

// Who opens the groups pages (#234 A1-A3, UX only: the API decides the
// data): staff with schedule.view (every group, the roster), or a student
// (their own groups). Tutors and guardians get the Forbidden page; #67
// extends this list. The nav item, /groups and /groups/:groupId share it.
export const GROUPS_ACCESS = {
  anyOf: [{ permission: 'schedule.view' }, { anyRole: ['student'] }],
} as const satisfies AccessRequirement;
