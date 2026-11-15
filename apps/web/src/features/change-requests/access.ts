import type { AccessRequirement } from '@/features/auth/access';

// Who opens "My change requests" (#62b B1): guardians, students and tutors
// (the #59/#67 anyRole). UX only: the API's record policy decides which
// requests each may read.
export const CHANGE_REQUESTS_ACCESS = {
  anyRole: ['guardian', 'student', 'tutor'],
} as const satisfies AccessRequirement;
