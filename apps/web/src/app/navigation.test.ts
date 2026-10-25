import { describe, expect, it } from 'vitest';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import { TEST_USER } from '@/test/api-stub';
import { NAV_ITEMS } from './navigation';

// #308 A2: "Evaluation templates" for evaluation.manage holders, "Academic
// history" for students and guardians; Finance and a tutor-only user see
// neither (the header shows the items canAccess allows, as AppLayout does).

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'evaluation.manage'],
);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
const STUDENT = user(['student'], ['student.view']);
const FINANCE = user(['finance_staff'], ['student.view', 'schedule.view']);
const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);

function shown(as: SessionUserBody): string[] {
  return NAV_ITEMS.filter((item) => canAccess(as, item)).map((item) => item.to);
}

describe('the evaluation nav items (#308 A2)', () => {
  it.each([
    ['a Coordinator', COORDINATOR, true, false],
    ['a guardian', GUARDIAN, false, true],
    ['a student', STUDENT, false, true],
    ['Finance', FINANCE, false, false],
    ['a tutor-only user', TUTOR, false, false],
  ] as const)(
    '%s: templates %s, history %s',
    (_who, as, templates, history) => {
      const items = shown(as);
      expect(items.includes('/academics/evaluation-templates')).toBe(templates);
      expect(items.includes('/my-history')).toBe(history);
    },
  );

  it('labels both items in the common catalog', () => {
    const labels = NAV_ITEMS.filter((item) =>
      ['/academics/evaluation-templates', '/my-history'].includes(item.to),
    ).map((item) => item.labelKey);
    expect(labels).toEqual(['nav.evaluationTemplates', 'nav.academicHistory']);
  });
});

// #310 A2: "Learning plans" for students and guardians only; no staff or
// tutor item (they open a student's plans from the student page and My
// schedule).
describe('the learning plans nav item (#310 A2)', () => {
  const MANAGER = user(
    ['academic_coordinator'],
    ['student.view', 'learning_plan.manage'],
  );

  it.each([
    ['a guardian', GUARDIAN, true],
    ['a student', STUDENT, true],
    ['a Coordinator', MANAGER, false],
    ['Finance', FINANCE, false],
    ['a tutor-only user', TUTOR, false],
  ] as const)('%s: %s', (_who, as, visible) => {
    expect(shown(as).includes('/my-learning-plans')).toBe(visible);
  });

  it('is labelled in the common catalog', () => {
    expect(
      NAV_ITEMS.find((item) => item.to === '/my-learning-plans')?.labelKey,
    ).toBe('nav.learningPlans');
  });
});
