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

// #313 A2: "Assigned homework" for tutors and assignment.review holders,
// "Homework" for students and guardians; Finance sees neither; a tutor who
// is also a guardian sees both.
describe('the homework nav items (#313 A2)', () => {
  const REVIEWER = user(
    ['academic_coordinator'],
    ['student.view', 'assignment.review'],
  );
  const TUTOR_GUARDIAN = user(
    ['tutor', 'guardian'],
    ['student.view', 'tutor.view', 'guardian.view'],
  );

  it.each([
    ['a tutor', TUTOR, true, false],
    ['a Coordinator (assignment.review)', REVIEWER, true, false],
    ['a guardian', GUARDIAN, false, true],
    ['a student', STUDENT, false, true],
    ['Finance', FINANCE, false, false],
    ['a tutor who is also a guardian', TUTOR_GUARDIAN, true, true],
  ] as const)(
    '%s: assigned %s, homework %s',
    (_who, as, assigned, homework) => {
      const items = shown(as);
      expect(items.includes('/assignments')).toBe(assigned);
      expect(items.includes('/my-homework')).toBe(homework);
    },
  );

  it('labels both items in the common catalog', () => {
    expect(
      NAV_ITEMS.filter((item) =>
        ['/assignments', '/my-homework'].includes(item.to),
      ).map((item) => item.labelKey),
    ).toEqual(['nav.assignedHomework', 'nav.homework']);
  });
});

// #315 A2: "Progress reports" for tutors and progress_report.review holders,
// "Tutor reports" for students and guardians; Finance sees neither; a tutor
// who is also a guardian sees both.
describe('the progress report nav items (#315 A2)', () => {
  const REVIEWER = user(
    ['academic_coordinator'],
    ['student.view', 'progress_report.review'],
  );
  const TUTOR_GUARDIAN = user(
    ['tutor', 'guardian'],
    ['student.view', 'tutor.view', 'guardian.view'],
  );

  it.each([
    ['a tutor', TUTOR, true, false],
    ['a Coordinator (progress_report.review)', REVIEWER, true, false],
    ['a guardian', GUARDIAN, false, true],
    ['a student', STUDENT, false, true],
    ['Finance', FINANCE, false, false],
    ['a tutor who is also a guardian', TUTOR_GUARDIAN, true, true],
  ] as const)(
    '%s: progress reports %s, tutor reports %s',
    (_who, as, working, family) => {
      const items = shown(as);
      expect(items.includes('/progress-reports')).toBe(working);
      expect(items.includes('/my-progress-reports')).toBe(family);
    },
  );

  it('labels both items in the common catalog', () => {
    expect(
      NAV_ITEMS.filter((item) =>
        ['/progress-reports', '/my-progress-reports'].includes(item.to),
      ).map((item) => item.labelKey),
    ).toEqual(['nav.progressReports', 'nav.tutorReports']);
  });
});

// #322 A4: "Progress" for students and guardians (per child); staff and
// tutors open a student's progress from the student page and My schedule.
describe('the progress nav item (#322 A4)', () => {
  const TUTOR_GUARDIAN = user(
    ['tutor', 'guardian'],
    ['student.view', 'tutor.view', 'guardian.view'],
  );

  it.each([
    ['a guardian', GUARDIAN, true],
    ['a student', STUDENT, true],
    ['a Coordinator', COORDINATOR, false],
    ['Finance', FINANCE, false],
    ['a tutor-only user', TUTOR, false],
    ['a tutor who is also a guardian', TUTOR_GUARDIAN, true],
  ] as const)('%s: %s', (_who, as, visible) => {
    expect(shown(as).includes('/my-progress')).toBe(visible);
  });

  it('is labelled "Progress" in the common catalog', () => {
    expect(NAV_ITEMS.find((item) => item.to === '/my-progress')?.labelKey).toBe(
      'nav.progress',
    );
  });
});

// M8-03 (#120) A4, D1: "Audit log" after Approvals, for audit.view holders
// only (Owner, Administrator); Finance and a tutor never see it.
describe('the audit log nav item (#120 D1)', () => {
  const AUDITOR = user(['administrator'], ['student.view', 'audit.view']);

  it.each([
    ['an audit.view holder', AUDITOR, true],
    ['Finance', FINANCE, false],
    ['a tutor-only user', TUTOR, false],
    ['a Coordinator', COORDINATOR, false],
  ] as const)('%s: %s', (_who, as, visible) => {
    expect(shown(as).includes('/audit')).toBe(visible);
  });

  it('comes right after Approvals and is labelled in the common catalog', () => {
    const paths = NAV_ITEMS.map((item) => item.to);
    expect(paths.indexOf('/audit')).toBe(paths.indexOf('/approvals') + 1);
    expect(NAV_ITEMS.find((item) => item.to === '/audit')).toMatchObject({
      labelKey: 'nav.audit',
      permission: 'audit.view',
    });
  });
});
