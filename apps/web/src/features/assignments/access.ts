import type { AccessRequirement } from '@/features/auth/access';
import { useCan, useSession } from '@/features/auth/useSession';

// Who opens the homework pages (#313 A1). UX only: the API's assignment
// record policy decides every read and write (#82).

/** The working pages (the list, a roster, a student's homework): tutors
 * (the homework they set; their students) and assignment.review holders. */
export const ASSIGNMENT_WORK_ACCESS = {
  anyOf: [{ role: 'tutor' }, { permission: 'assignment.review' }],
} as const satisfies AccessRequirement;

/** The form (new and edit): tutors only (only a tutor sets homework). */
export const ASSIGNMENT_FORM_ACCESS = {
  role: 'tutor',
} as const satisfies AccessRequirement;

/** The family pages (/my-homework): students and guardians. */
export const MY_HOMEWORK_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;

/**
 * The route handle of the working URLs (/assignments, /assignments/:id,
 * /students/:id/assignments[/:id]): the working access, plus students and
 * guardians, whom the page sends to their own /my-homework page (the #308
 * QA D2 rule, as #310 did: a family-only user never lands on a staff view).
 * Finance and everyone else get the guard's access-denied page.
 */
export const ASSIGNMENT_ROUTE_ACCESS = {
  anyOf: [...ASSIGNMENT_WORK_ACCESS.anyOf, MY_HOMEWORK_ACCESS],
} as const satisfies AccessRequirement;

/** The user's roles (none while the session loads). */
export function useRoles(): readonly string[] {
  const session = useSession();
  return session.status === 'signedIn' ? session.user.roles : [];
}

/** A tutor-role user or an assignment.review holder (the student page
 * link, A3; the working views). */
export function useAssignmentWorker(): boolean {
  const roles = useRoles();
  const canReview = useCan('assignment.review');
  return canReview || roles.includes('tutor');
}

/**
 * A student or guardian who is neither a tutor nor an assignment.review
 * holder: the working URLs redirect them to /my-homework (no working
 * request is sent). A user with several roles keeps the union.
 */
export function useFamilyOnly(): boolean {
  const roles = useRoles();
  const worker = useAssignmentWorker();
  return !worker && (roles.includes('student') || roles.includes('guardian'));
}
