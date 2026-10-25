import type { AccessRequirement } from '@/features/auth/access';

// Who opens "Academic history" (/my-history, #308 A1, A2): students and
// guardians. UX only: the API's record policies decide whose history each
// may read (the #67 family rule; an adult's guardian needs their consent).
export const MY_HISTORY_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;
