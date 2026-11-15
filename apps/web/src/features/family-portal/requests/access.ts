import type { AccessRequirement } from '@/features/auth/access';

// Who opens the session request pages (#59 F1): guardians and students.
// UX only: the API's record policy decides which students they may act for.
export const SESSION_REQUESTS_ACCESS = {
  anyRole: ['guardian', 'student'],
} as const satisfies AccessRequirement;
