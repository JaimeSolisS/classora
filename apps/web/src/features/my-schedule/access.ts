import type { AccessRequirement } from '@/features/auth/access';

// Who opens "My schedule" (#67 E1): tutors, students and guardians. UX only:
// the API's record policies decide whose schedule each may read.
export const MY_SCHEDULE_ACCESS = {
  anyRole: ['tutor', 'student', 'guardian'],
} as const satisfies AccessRequirement;
