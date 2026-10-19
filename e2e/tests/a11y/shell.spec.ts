import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { SIGNED_OUT_STATE } from '../support/auth';
import { createStudentViaApi } from '../support/students';

// WCAG 2.0, 2.1 and 2.2 level A and AA (the A7 tags in
// _docs/testing-guidelines.md). No rule is disabled; a per-test exclusion
// needs a reason comment here and a line in "E2E axe exceptions".
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const THEMES = ['light', 'dark'] as const;

// Home, 404, /users, a user's detail (demo.owner's own record, opened from
// the list: its id is not known in advance), /invitations,
// /password-resets, /registration-requests, /account/password, the catalog
// levels page and the seeded "Primaria" level's page (opened from the list),
// /students, /students/new and the detail of a student the scan creates
// through the API first (#33 L2) are behind the guard: scanned signed in
// (the shared state). /login, /invite and
// /reset (the "incomplete link" state: no token) and /register are scanned
// signed out.
interface ScannedRoute {
  name: string;
  path: string;
  signedIn: boolean;
  /** Then open this row of the list (its link text) and scan its page. */
  openRow?: string;
  /** The URL of the page `openRow` opens. */
  openedPath?: RegExp;
  /** First create a student through the API; `path` is then its detail. */
  createsStudent?: boolean;
}

const ROUTES: ScannedRoute[] = [
  { name: 'home', path: '/', signedIn: true },
  { name: 'users', path: '/users', signedIn: true },
  {
    name: 'user-detail',
    path: '/users?q=demo.owner',
    signedIn: true,
    openRow: 'Demo Owner',
    openedPath: /\/users\/[0-9a-f-]{36}$/,
  },
  { name: '404', path: '/e2e-route-that-does-not-exist', signedIn: true },
  { name: 'invitations', path: '/invitations', signedIn: true },
  { name: 'password-resets', path: '/password-resets', signedIn: true },
  {
    name: 'registration-requests',
    path: '/registration-requests',
    signedIn: true,
  },
  { name: 'account-password', path: '/account/password', signedIn: true },
  { name: 'catalog', path: '/catalog/education-levels', signedIn: true },
  {
    name: 'catalog-level',
    path: '/catalog/education-levels',
    signedIn: true,
    openRow: 'Primaria',
    openedPath: /\/catalog\/education-levels\/[0-9a-f-]{36}$/,
  },
  { name: 'students', path: '/students', signedIn: true },
  { name: 'student-new', path: '/students/new', signedIn: true },
  {
    name: 'student-detail',
    path: '/students/<created>',
    signedIn: true,
    createsStudent: true,
  },
  { name: 'login', path: '/login', signedIn: false },
  { name: 'invite', path: '/invite', signedIn: false },
  { name: 'reset', path: '/reset', signedIn: false },
  { name: 'register', path: '/register', signedIn: false },
];

// Under machine load, the first pages of a cold dev server (and the session
// request before a guarded page renders) can take longer than the default
// waits: the first heading gets a longer wait, inside a longer test timeout.
const FIRST_RENDER_TIMEOUT_MS = 60_000;
const TEST_TIMEOUT_MS = 90_000;

/** Every violation as `rule-id (impact): selector, selector`. */
async function scan(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact ?? 'unknown'}): ${violation.nodes
        .map((node) => node.target.join(' '))
        .join(', ')}`,
  );
}

for (const theme of THEMES) {
  for (const route of ROUTES) {
    test.describe(() => {
      test.describe.configure({ timeout: TEST_TIMEOUT_MS });
      if (!route.signedIn) {
        test.use({ storageState: SIGNED_OUT_STATE });
      }
      test(`${route.name} has no axe violations in ${theme}`, async ({
        page,
      }, testInfo) => {
        // The stored theme choice, applied before the app's first render.
        await page.addInitScript((choice) => {
          window.localStorage.setItem('classora.theme', choice);
        }, theme);

        let path = route.path;
        if (route.createsStudent) {
          // The app origin first, so the API call carries the session.
          await page.goto('/');
          await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
            timeout: FIRST_RENDER_TIMEOUT_MS,
          });
          const sfx = `a11y${theme}${testInfo.workerIndex}${Date.now().toString(36)}`;
          const id = await createStudentViaApi(page, {
            givenNames: 'Ana',
            familyNames: `Accesible ${sfx}`,
          });
          path = `/students/${id}`;
        }
        await page.goto(path);
        await expect(page).toHaveURL(path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
          timeout: FIRST_RENDER_TIMEOUT_MS,
        });
        if (route.openRow) {
          const name = route.openRow;
          await page
            .getByRole('main')
            .getByRole('link', { name, exact: true })
            .click();
          await expect(page).toHaveURL(route.openedPath!);
          await expect(
            page.getByRole('heading', { level: 1, name, exact: true }),
          ).toBeVisible();
        }
        const html = page.locator('html');
        if (theme === 'dark') {
          await expect(html).toHaveClass(/\bdark\b/);
        } else {
          await expect(html).not.toHaveClass(/\bdark\b/);
        }

        const violations = await scan(page);
        expect(violations, violations.join('\n')).toEqual([]);
      });
    });
  }
}
