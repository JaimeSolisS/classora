import type { AccessRequirement } from '@/features/auth/access';

// Who opens the cohort list and detail (#243 A2; #246 B2; UX only: the API
// decides the data): course.view holders (every cohort, the roster), or a
// tutor, a student or a guardian (the cohorts they teach or take). The
// create form stays schedule.manage.
export const COHORTS_ACCESS = {
  anyOf: [
    { permission: 'course.view' },
    { anyRole: ['tutor', 'student', 'guardian'] },
  ],
} as const satisfies AccessRequirement;

/** "My courses" (#246 B2): the nav item of tutors, students and guardians. */
export const MY_COURSES_ACCESS = {
  anyRole: ['tutor', 'student', 'guardian'],
} as const satisfies AccessRequirement;
