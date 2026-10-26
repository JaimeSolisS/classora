import type { AccessRequirement } from '@/features/auth/access';
import { useCan, useSession } from '@/features/auth/useSession';

// Who opens the progress pages (#322 A2). UX only: the API's progress
// record policy decides every read (#84 A1).

/** /my-progress: students and guardians (per child). */
export const MY_PROGRESS_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;

/** The staff and tutor page: evaluation.manage holders (Owner,
 * Administrator, Academic Coordinator) and tutor-role users (current and
 * former tutors; the API decides which). */
export const STUDENT_PROGRESS_WORK_ACCESS = {
  anyOf: [{ permission: 'evaluation.manage' }, { role: 'tutor' }],
} as const satisfies AccessRequirement;

/**
 * The route handle of /students/:studentId/progress: the working access,
 * plus students and guardians, whom the page sends to their own
 * /my-progress page (the #308 QA D2 rule, as #310, #313 and #315 did: a
 * family-only user never lands on a staff view). Finance and everyone else
 * get the guard's access-denied page, with no progress request.
 */
export const STUDENT_PROGRESS_ACCESS = {
  anyOf: [...STUDENT_PROGRESS_WORK_ACCESS.anyOf, MY_PROGRESS_ACCESS],
} as const satisfies AccessRequirement;

function useRoles(): readonly string[] {
  const session = useSession();
  return session.status === 'signedIn' ? session.user.roles : [];
}

/** An evaluation.manage holder or a tutor-role user (the student-page
 * link, A5; the staff view). */
export function useProgressWorker(): boolean {
  const roles = useRoles();
  const manager = useCan('evaluation.manage');
  return manager || roles.includes('tutor');
}

/** A student or guardian who is neither: the staff URL redirects them. */
export function useProgressFamilyOnly(): boolean {
  const roles = useRoles();
  const worker = useProgressWorker();
  return !worker && (roles.includes('student') || roles.includes('guardian'));
}

/** The staff and tutor page of a student. */
export function studentProgressPath(studentId: string): string {
  return `/students/${encodeURIComponent(studentId)}/progress`;
}

/** The family page, for one student when given. */
export function myProgressPath(studentId?: string): string {
  return studentId
    ? `/my-progress?student=${encodeURIComponent(studentId)}`
    : '/my-progress';
}
