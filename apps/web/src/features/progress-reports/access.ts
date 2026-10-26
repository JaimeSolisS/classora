import type { AccessRequirement } from '@/features/auth/access';
import { useCan, useSession } from '@/features/auth/useSession';

// Who opens the progress report pages (#315 A1). UX only: the API's report
// record policy decides every read and write (#83).

/** The working pages (the list and review queue, a report, a student's
 * reports, an approved report): tutor-role users and
 * progress_report.review holders (Owner, Administrator, Academic
 * Coordinator). */
export const PROGRESS_REPORT_WORK_ACCESS = {
  anyOf: [{ role: 'tutor' }, { permission: 'progress_report.review' }],
} as const satisfies AccessRequirement;

/** "New progress report": tutors only (only a current tutor writes one). */
export const PROGRESS_REPORT_START_ACCESS = {
  role: 'tutor',
} as const satisfies AccessRequirement;

/** The family pages (/my-progress-reports): students and guardians. */
export const MY_PROGRESS_REPORTS_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;

/**
 * The route handle of the working URLs: the working access, plus students
 * and guardians, whom the page sends to their own /my-progress-reports page
 * (the #308 QA D2 rule, as #310 and #313 did: a family-only user never
 * lands on a staff view). Finance and everyone else get the guard's
 * access-denied page.
 */
export const PROGRESS_REPORT_ROUTE_ACCESS = {
  anyOf: [...PROGRESS_REPORT_WORK_ACCESS.anyOf, MY_PROGRESS_REPORTS_ACCESS],
} as const satisfies AccessRequirement;

/** The user's roles (none while the session loads). */
export function useRoles(): readonly string[] {
  const session = useSession();
  return session.status === 'signedIn' ? session.user.roles : [];
}

/** A progress_report.review holder. */
export function useReportReviewer(): boolean {
  return useCan('progress_report.review');
}

/** A tutor-role user or a progress_report.review holder (the student page
 * link, A3; the working views). */
export function useReportWorker(): boolean {
  const roles = useRoles();
  const reviewer = useReportReviewer();
  return reviewer || roles.includes('tutor');
}

/**
 * A student or guardian who is neither a tutor nor a reviewer: the working
 * URLs redirect them to /my-progress-reports (no working request is sent).
 * A user with several roles keeps the union.
 */
export function useFamilyOnly(): boolean {
  const roles = useRoles();
  const worker = useReportWorker();
  return !worker && (roles.includes('student') || roles.includes('guardian'));
}
