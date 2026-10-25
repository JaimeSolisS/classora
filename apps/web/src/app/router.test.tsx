import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { resetUiStore, useUiStore } from '@/stores/ui';
import { stubSignedIn } from '@/test/api-stub';
import { buttonNamed, renderApp, type RenderedApp } from '@/test/render-app';
import { routes } from './router';

// B/H7: route tree, layout, 404, skip link, guard, nav toggle and the header
// switchers. Signed in through a stubbed /auth/me (#28 I2): every page sits
// behind the guard.

let app: RenderedApp | undefined;

const FOCUSABLE =
  'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

beforeEach(() => {
  applyLanguage('en');
  stubSignedIn();
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  resetUiStore();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('route tree', () => {
  it('is layout → login, invite, reset, register and guard → home, pages and 404, nothing else', () => {
    expect(routes).toHaveLength(1);
    const [root] = routes;
    expect(root!.path).toBe('/');
    expect(root!.errorElement).toBeTruthy();
    const [login, invite, reset, register, guard] = root!.children!;
    expect(root!.children).toHaveLength(5);
    // /login, /invite, /reset and /register are the only pages outside the
    // guard (#28 B1, #29 J1, #30 J1, #169 F1).
    expect(login!.path).toBe('login');
    expect(login!.children).toBeUndefined();
    expect(invite!.path).toBe('invite');
    expect(invite!.children).toBeUndefined();
    expect(invite!.errorElement).toBeTruthy();
    expect(reset!.path).toBe('reset');
    expect(reset!.children).toBeUndefined();
    expect(reset!.errorElement).toBeTruthy();
    expect(register!.path).toBe('register');
    expect(register!.children).toBeUndefined();
    expect(register!.errorElement).toBeTruthy();
    expect(guard!.id).toBe('guard');
    expect(guard!.path).toBeUndefined();
    expect(guard!.errorElement).toBeTruthy();
    const children = guard!.children!;
    expect(children.map((r) => (r.index ? 'index' : r.path))).toEqual([
      'index',
      'users',
      'users/:userId',
      'invitations',
      'password-resets',
      'approvals',
      'registration-requests',
      'catalog/education-levels',
      'catalog/education-levels/:levelId',
      'catalog/periods',
      'catalog/closures',
      'catalog/classrooms',
      'students',
      'students/new',
      'students/:studentId',
      'students/:studentId/edit',
      'guardians',
      'guardians/new',
      'guardians/:guardianId',
      'guardians/:guardianId/edit',
      'families',
      'families/new',
      'families/:familyId',
      'families/:familyId/edit',
      'tutors',
      'tutors/:userId',
      'schedule',
      'schedule/new',
      'schedule/overdue-closeouts',
      'schedule/sessions/:sessionId',
      'schedule/series/:seriesId',
      'schedule/requests/:requestId',
      'schedule/change-requests/:requestId',
      'offers',
      'courses',
      'courses/templates/new',
      'courses/templates/:templateId',
      'courses/cohorts',
      'courses/cohorts/new',
      'courses/cohorts/:cohortId',
      'settings',
      'settings/scheduling-policies',
      'groups',
      'groups/new',
      'groups/:groupId',
      'requests',
      'requests/new',
      'my-schedule',
      'my-schedule/sessions/:sessionId/attendance',
      'my-schedule/sessions/:sessionId/closeout',
      'my-schedule/sessions/:sessionId/notes',
      'change-requests',
      'academics/evaluation-templates',
      'academics/evaluation-templates/new',
      'academics/evaluation-templates/:templateId',
      'students/:studentId/history',
      'students/:studentId/evaluations/new',
      'evaluations/:evaluationId',
      'my-history',
      'students/:studentId/learning-plans',
      'students/:studentId/learning-plans/new',
      'learning-plans/:planId',
      'learning-plans/:planId/edit',
      'my-learning-plans',
      'my-learning-plans/:planId',
      'account/password',
      'privacy-notice',
      '*',
    ]);
    expect(children[1]!.handle).toEqual({ permission: 'user.view' });
    expect(children[2]!.handle).toEqual({ permission: 'user.view' });
    expect(children[3]!.handle).toEqual({ permission: 'user.invite' });
    expect(children[4]!.handle).toEqual({
      permission: 'user.password_reset.issue',
    });
    // #40 F1, F2: any review permission; the old path only redirects.
    expect(children[5]!.handle).toEqual({
      anyPermission: [
        'registration.review',
        'qualification.review',
        'schedule.assign',
        'schedule.manage',
        'tutor.manage',
        'teaching_hours.approve',
      ],
    });
    expect(children[6]!.handle).toBeUndefined();
    // #32 H1, I1.
    expect(children[7]!.handle).toEqual({ permission: 'catalog.manage' });
    expect(children[8]!.handle).toEqual({ permission: 'catalog.manage' });
    // #38 E1.
    expect(children[9]!.handle).toEqual({ permission: 'catalog.manage' });
    expect(children[10]!.handle).toEqual({ permission: 'catalog.manage' });
    // #39 D1.
    expect(children[11]!.handle).toEqual({ permission: 'catalog.manage' });
    // #33 I1, J1, J3.
    expect(children[12]!.handle).toEqual({ permission: 'student.view' });
    expect(children[13]!.handle).toEqual({ permission: 'student.manage' });
    expect(children[14]!.handle).toEqual({ permission: 'student.view' });
    expect(children[15]!.handle).toEqual({ permission: 'student.manage' });
    // #34 I1-I3.
    expect(children[16]!.handle).toEqual({ permission: 'guardian.view' });
    expect(children[17]!.handle).toEqual({ permission: 'family.manage' });
    expect(children[18]!.handle).toEqual({ permission: 'guardian.view' });
    expect(children[19]!.handle).toEqual({ permission: 'family.manage' });
    // #172 H1-H3.
    expect(children[20]!.handle).toEqual({ permission: 'guardian.view' });
    expect(children[21]!.handle).toEqual({ permission: 'family.manage' });
    expect(children[22]!.handle).toEqual({ permission: 'guardian.view' });
    expect(children[23]!.handle).toEqual({ permission: 'family.manage' });
    // #43 E1.
    expect(children[24]!.handle).toEqual({ permission: 'tutor.view' });
    expect(children[25]!.handle).toEqual({ permission: 'tutor.view' });
    // #57 D1.
    expect(children[26]!.handle).toEqual({ permission: 'schedule.view' });
    // #48 F1.
    expect(children[27]!.handle).toEqual({ permission: 'schedule.manage' });
    // #301b A1.
    expect(children[28]!.handle).toEqual({
      permission: 'teaching_hours.approve',
    });
    // #52 H2, H1.
    expect(children[29]!.handle).toEqual({ permission: 'schedule.view' });
    // #54b B1.
    expect(children[30]!.handle).toEqual({ permission: 'schedule.view' });
    // #60 H1.
    expect(children[31]!.handle).toEqual({ permission: 'schedule.manage' });
    // #62 G1.
    expect(children[32]!.handle).toEqual({ permission: 'schedule.manage' });
    expect(children[33]!.handle).toEqual({ role: 'tutor' });
    // #64 G1.
    expect(children[34]!.handle).toEqual({ permission: 'course.view' });
    expect(children[35]!.handle).toEqual({ permission: 'course.manage' });
    expect(children[36]!.handle).toEqual({ permission: 'course.view' });
    // #243 A2, #246 B2: the cohorts for course.view, tutors, students and
    // guardians; the create form needs schedule.manage.
    const cohorts = {
      anyOf: [
        { permission: 'course.view' },
        { anyRole: ['tutor', 'student', 'guardian'] },
      ],
    };
    expect(children[37]!.handle).toEqual(cohorts);
    expect(children[38]!.handle).toEqual({ permission: 'schedule.manage' });
    expect(children[39]!.handle).toEqual(cohorts);
    // #37 H3.
    expect(children[40]!.handle).toEqual({ permission: 'settings.manage' });
    // #61 F1.
    expect(children[41]!.handle).toEqual({ permission: 'settings.manage' });
    // #234 A3, #246 B1: staff with schedule.view, students, tutors and
    // guardians; the create form needs schedule.manage.
    const groups = {
      anyOf: [
        { permission: 'schedule.view' },
        { anyRole: ['student', 'tutor', 'guardian'] },
      ],
    };
    expect(children[42]!.handle).toEqual(groups);
    expect(children[43]!.handle).toEqual({ permission: 'schedule.manage' });
    expect(children[44]!.handle).toEqual(groups);
    // #59 F1: guardians and students (the API's record policy decides).
    const requests = { anyRole: ['guardian', 'student'] };
    expect(children[45]!.handle).toEqual(requests);
    expect(children[46]!.handle).toEqual(requests);
    // #67 E1: tutors, students and guardians (the API decides).
    expect(children[47]!.handle).toEqual({
      anyRole: ['tutor', 'student', 'guardian'],
    });
    // #76 G1: tutors only (the API's record policy decides).
    expect(children[48]!.handle).toEqual({ role: 'tutor' });
    // #77 G1: tutors only (the API's record policy decides).
    expect(children[49]!.handle).toEqual({ role: 'tutor' });
    // #79 H1: tutors only (the API's record policy decides).
    expect(children[50]!.handle).toEqual({ role: 'tutor' });
    // #62b B1: guardians, students and tutors (the API decides).
    expect(children[51]!.handle).toEqual({
      anyRole: ['guardian', 'student', 'tutor'],
    });
    // #308 A1: the evaluation pages (all code-split).
    expect(children[52]!.handle).toEqual({ permission: 'evaluation.manage' });
    expect(children[53]!.handle).toEqual({ permission: 'evaluation.manage' });
    expect(children[54]!.handle).toEqual({ permission: 'evaluation.manage' });
    expect(children[55]!.handle).toEqual({ permission: 'student.view' });
    expect(children[56]!.handle).toEqual({ role: 'tutor' });
    expect(children[57]!.handle).toEqual({
      anyOf: [{ role: 'tutor' }, { permission: 'evaluation.manage' }],
    });
    expect(children[58]!.handle).toEqual({
      anyRole: ['student', 'guardian'],
    });
    for (const index of [52, 53, 54, 55, 56, 57, 58]) {
      expect(children[index]!.lazy).toBeTypeOf('function');
      expect(children[index]!.element).toBeUndefined();
    }
    // #310 A1: the learning plan pages (all code-split). The staff and
    // tutor URLs let the family roles through, so the page can send them
    // to their own page; the family pages serve students and guardians.
    const planRoute = {
      anyOf: [
        { role: 'tutor' },
        { permission: 'learning_plan.manage' },
        { anyRole: ['student', 'guardian'] },
      ],
    };
    for (const index of [59, 60, 61, 62]) {
      expect(children[index]!.handle).toEqual(planRoute);
    }
    for (const index of [63, 64]) {
      expect(children[index]!.handle).toEqual({
        anyRole: ['student', 'guardian'],
      });
    }
    for (const index of [59, 60, 61, 62, 63, 64]) {
      expect(children[index]!.lazy).toBeTypeOf('function');
      expect(children[index]!.element).toBeUndefined();
    }
    expect(children[65]!.handle).toBeUndefined();
    // #174 G1: any signed-in user, no permission.
    expect(children[66]!.id).toBe('privacy-notice');
    expect(children[66]!.handle).toBeUndefined();
    expect(
      JSON.stringify(routes, (key, value: unknown) =>
        key === 'element' || key === 'errorElement' ? undefined : value,
      ),
    ).not.toMatch(/"path":"\/?(api|health)/);
  });
});

describe('home', () => {
  it('renders the home page inside the layout with the title Classora', async () => {
    app = await renderApp('/');
    const { container } = app;

    expect(container.querySelector('header')).not.toBeNull();
    expect(container.querySelector('main#main h1')?.textContent).toBe(
      'Classora',
    );
    expect(document.title).toBe('Classora');
    const home = container.querySelector('nav a[href="/"]');
    expect(home?.textContent).toBe('Home');
    expect(home?.getAttribute('aria-current')).toBe('page');
    expect(container.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Main navigation',
    );
  });

  it('renders the guard route child for a signed-in user', async () => {
    app = await renderApp('/');
    expect(app.container.querySelector('[role="status"]')).not.toBeNull();
  });
});

describe('404', () => {
  it.each(['/does-not-exist', '/a/b/c'])(
    'renders the 404 page inside the layout for %s',
    async (path) => {
      app = await renderApp(path);
      const { container } = app;

      expect(container.querySelector('header nav')).not.toBeNull();
      const main = container.querySelector('main#main');
      expect(main?.querySelector('h1')?.textContent).toBe('Page not found');
      const back = main?.querySelector('a[href="/"]');
      expect(back?.textContent).toBe('Back to home');
      expect(document.title).toBe('Page not found · Classora');
    },
  );

  it('goes back home from the 404 link', async () => {
    app = await renderApp('/nope');
    const back =
      app.container.querySelector<HTMLAnchorElement>('main a[href="/"]');

    act(() => back?.click());

    expect(app.container.querySelector('main h1')?.textContent).toBe(
      'Classora',
    );
    expect(document.title).toBe('Classora');
  });
});

describe('skip link', () => {
  it('is the first focusable element and moves focus to #main', async () => {
    app = await renderApp('/');
    const { container } = app;
    const first = container.querySelector<HTMLAnchorElement>(FOCUSABLE);

    expect(first?.textContent).toBe('Skip to main content');
    expect(first?.getAttribute('href')).toBe('#main');
    const main = container.querySelector<HTMLElement>('main');
    expect(main?.id).toBe('main');
    expect(main?.getAttribute('tabindex')).toBe('-1');

    act(() => first?.click());
    expect(document.activeElement).toBe(main);
  });
});

describe('navigation toggle', () => {
  it('flips aria-expanded and the store navOpen', async () => {
    app = await renderApp('/');
    const toggle = buttonNamed(app.container, 'Menu');
    const controlled = toggle.getAttribute('aria-controls');
    expect(controlled).toBeTruthy();
    const region = app.container.querySelector(`#${controlled}`);
    expect(region?.querySelector('nav')).not.toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(region?.className).toMatch(/(^|\s)hidden(\s|$)/);

    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(useUiStore.getState().navOpen).toBe(true);
    expect(region?.className).not.toMatch(/(^|\s)hidden(\s|$)/);

    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(useUiStore.getState().navOpen).toBe(false);
  });
});

describe('header switchers', () => {
  it('has the theme and language switchers in the header', async () => {
    app = await renderApp('/');
    const groups = [
      ...app.container.querySelectorAll('header [role="group"]'),
    ].map((g) => g.getAttribute('aria-label'));
    expect(groups).toEqual(['Theme', 'Language']);
  });

  it('switches shell, page text, title and <html lang> in place', async () => {
    app = await renderApp('/missing');
    const { container } = app;
    const main = container.querySelector('main');
    expect(container.textContent).toContain('Skip to main content');

    act(() => buttonNamed(container, 'Español').click());

    expect(document.documentElement.lang).toBe('es');
    expect(container.querySelector('main')).toBe(main);
    expect(container.textContent).toContain('Saltar al contenido principal');
    expect(container.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Navegación principal',
    );
    expect(container.querySelector('nav a')?.textContent).toBe('Inicio');
    expect(buttonNamed(container, 'Menú')).toBeTruthy();
    expect(main?.querySelector('h1')?.textContent).toBe('Página no encontrada');
    expect(document.title).toBe('Página no encontrada · Classora');

    act(() => buttonNamed(container, 'English').click());
    expect(document.documentElement.lang).toBe('en');
    expect(document.title).toBe('Page not found · Classora');
  });
});
