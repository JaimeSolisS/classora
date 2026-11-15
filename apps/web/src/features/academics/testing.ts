import type { SessionUserBody } from '@/features/auth/session';
import { TEST_USER } from '@/test/api-stub';

// Test helpers of the academics pages (#308 G1): the users by role and a
// Problem Details response with field errors.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

export const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'evaluation.manage', 'course.view'],
);
export const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'schedule.view', 'course.view'],
);
export const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
export const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
export const STUDENT = user(['student'], ['student.view']);

/** A Problem Details response, with field errors and details if given. */
export function problemWith(
  status: number,
  code: string,
  extra: {
    errors?: { path: (string | number)[]; code: string; message?: string }[];
    details?: Record<string, string | string[]>;
  } = {},
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-${code}`,
      detail: 'Server detail that must never reach the UI',
      ...(extra.errors
        ? {
            errors: extra.errors.map((error) => ({
              message: 'Server message',
              ...error,
            })),
          }
        : {}),
      ...(extra.details ? { details: extra.details } : {}),
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

/** A tutor who is also an Academic Coordinator (the union of both). */
export const TUTOR_COORDINATOR = user(
  ['tutor', 'academic_coordinator'],
  ['student.view', 'tutor.view', 'evaluation.manage', 'course.view'],
);
