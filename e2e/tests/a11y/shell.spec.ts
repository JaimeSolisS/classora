import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createGuardianViaApi, linkGuardianViaApi } from '../support/guardians';
import { createStudentViaApi } from '../support/students';
import { createActiveUser } from '../support/users';

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
// through the API first (#33 L2), /guardians, /guardians/new and the detail
// of a guardian the scan creates and links to a new student first (#34 K2),
// /families, /families/new and the detail of a family the scan creates with
// that student and guardian (#172 J2)
// are behind the guard: scanned signed in
// (the shared state). /login, /invite and
// /reset (the "incomplete link" state: no token) and /register are scanned
// signed out. /privacy-notice (to accept) is scanned by its own test below,
// as a fresh user who signs in (#174 I3).
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
  /**
   * First create a guardian linked to a new student through the API; `path`
   * is then the guardian's detail.
   */
  createsGuardian?: boolean;
  /**
   * First create a family with a new student and guardian through the API;
   * `path` is then the family's detail.
   */
  createsFamily?: boolean;
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
  { name: 'guardians', path: '/guardians', signedIn: true },
  { name: 'guardian-new', path: '/guardians/new', signedIn: true },
  {
    name: 'guardian-detail',
    path: '/guardians/<created>',
    signedIn: true,
    createsGuardian: true,
  },
  { name: 'families', path: '/families', signedIn: true },
  { name: 'family-new', path: '/families/new', signedIn: true },
  {
    name: 'family-detail',
    path: '/families/<created>',
    signedIn: true,
    createsGuardian: true,
    createsFamily: true,
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
        if (route.createsStudent || route.createsGuardian) {
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
          if (route.createsGuardian) {
            const guardianId = await createGuardianViaApi(page, {
              givenNames: 'Elena',
              familyNames: `Accesible ${sfx}`,
            });
            await linkGuardianViaApi(page, id, guardianId);
            path = `/guardians/${guardianId}`;
            if (route.createsFamily) {
              const familyId = await createFamilyViaApi(
                page,
                `Familia Accesible ${sfx}`,
              );
              await addToFamilyViaApi(page, familyId, 'students', id);
              await addToFamilyViaApi(page, familyId, 'guardians', guardianId);
              path = `/families/${familyId}`;
            }
          }
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

// #174 I3: the privacy notice as a new user sees it (to accept), in both
// themes, from one sign-in. Budgets (see support/auth.ts): 2 logins (1 by
// createActiveUser, 1 through the form) and 1 redemption.
test('privacy-notice (to accept) has no axe violations in light and dark', async ({
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
  const username = `e2e.a11ynotice.${suffix}`;
  const password = 'e2e-a11y-notice-password';
  await createActiveUser(playwright.request, baseURL!, {
    username,
    role: 'tutor',
    password,
    displayName: `E2E A11y Notice ${suffix}`,
  });
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  try {
    const page = await context.newPage();
    await recordCspViolations(page);
    const consoleErrors: string[] = [];
    page.on('pageerror', (error) => consoleErrors.push(error.message));
    page.on('console', (message) => {
      // The signed-out /auth/me answers 401 on purpose.
      if (
        message.type() === 'error' &&
        !/Failed to load resource: the server responded with a status of 401/.test(
          message.text(),
        )
      ) {
        consoleErrors.push(message.text());
      }
    });
    await page.goto('/login');
    await page
      .getByLabel(common.auth.login.username, { exact: true })
      .fill(username);
    await page
      .getByLabel(common.auth.login.password, { exact: true })
      .fill(password);
    await page.getByRole('button', { name: common.auth.login.submit }).click();
    await expect(page).toHaveURL(/\/privacy-notice\?next=/);

    for (const theme of THEMES) {
      // The stored theme choice; a reload applies it (the session cookie
      // stays, no new login).
      await page.evaluate((choice) => {
        window.localStorage.setItem('classora.theme', choice);
      }, theme);
      await page.reload();
      await expect(
        page
          .getByRole('main')
          .getByRole('button', { name: common.privacyNotice.accept }),
      ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
      const html = page.locator('html');
      if (theme === 'dark') {
        await expect(html).toHaveClass(/\bdark\b/);
      } else {
        await expect(html).not.toHaveClass(/\bdark\b/);
      }
      const violations = await scan(page);
      expect(violations, `${theme}:\n${violations.join('\n')}`).toEqual([]);
    }
    expect(await cspViolations(page)).toEqual([]);
    expect(consoleErrors).toEqual([]);
  } finally {
    await context.close();
  }
});
