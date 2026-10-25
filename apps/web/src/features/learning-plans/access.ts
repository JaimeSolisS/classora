import type { AccessRequirement } from '@/features/auth/access';
import { useCan, useSession } from '@/features/auth/useSession';

// Who opens the learning plan pages (#310 A1). UX only: the API's plan
// record policy decides every read and write (#81).

/** The writers' pages: learning_plan.manage holders and tutor-role users
 * (a tutor's access to one student is the API's answer). */
export const LEARNING_PLAN_WRITER_ACCESS = {
  anyOf: [{ role: 'tutor' }, { permission: 'learning_plan.manage' }],
} as const satisfies AccessRequirement;

/** The family pages (/my-learning-plans): students and guardians. */
export const MY_LEARNING_PLANS_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;

/**
 * The route handle of the staff and tutor URLs (/students/:id/learning-plans
 * …, /learning-plans/:id …): the writers, plus students and guardians, whom
 * the page sends to their own family page (the #308 QA D2 rule: a
 * family-only user never lands on a staff view). Finance and everyone else
 * get the guard's access-denied page.
 */
export const LEARNING_PLAN_ROUTE_ACCESS = {
  anyOf: [...LEARNING_PLAN_WRITER_ACCESS.anyOf, MY_LEARNING_PLANS_ACCESS],
} as const satisfies AccessRequirement;

/** The user's roles (none while the session loads). */
export function useRoles(): readonly string[] {
  const session = useSession();
  return session.status === 'signedIn' ? session.user.roles : [];
}

/**
 * A student or guardian who is neither a tutor nor a learning_plan.manage
 * holder: the staff URLs redirect them to /my-learning-plans (no staff
 * request is sent). A user with several roles keeps the union.
 */
export function useFamilyOnly(): boolean {
  const roles = useRoles();
  const canManage = useCan('learning_plan.manage');
  return (
    !canManage &&
    !roles.includes('tutor') &&
    (roles.includes('student') || roles.includes('guardian'))
  );
}

/** A writer (holder or tutor-role user): the student page link (A3). */
export function useLearningPlanWriter(): boolean {
  const roles = useRoles();
  const canManage = useCan('learning_plan.manage');
  return canManage || roles.includes('tutor');
}
