import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { recordConsentViaApi } from '../support/consent';
import { createDemoNotificationViaApi } from '../support/notifications';
import { createGuardianViaApi, linkGuardianViaApi } from '../support/guardians';
import { createStudentViaApi } from '../support/students';
import { inviteTutorViaApi } from '../support/tutors';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';
import { createActiveUser } from '../support/users';

// WCAG 2.0, 2.1 and 2.2 level A and AA (the A7 tags in
// _docs/testing-guidelines.md). No rule is disabled; a per-test exclusion
// needs a reason comment here and a line in "E2E axe exceptions".
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const THEMES = ['light', 'dark'] as const;

// Home, 404, /users, a user's detail (demo.owner's own record, opened from
// the list: its id is not known in advance), /invitations,
// /password-resets, /approvals (#40 G2), /account/password, the catalog
// levels page and the seeded "Primaria" level's page (opened from the list),
// /catalog/periods and /catalog/closures (#38 F2), /catalog/classrooms
// (#39 E2),
// /settings (once its form loaded, #37 I2), /settings/scheduling-policies
// (once its form loaded, #61 H2), /billing/prices (#363 F5),
// /billing/unbilled and /billing/settings (#366 E4), /billing/discounts
// and /billing/custom-prices (#367 G4), /billing/tuition-plans (#374 H3),
// /billing/packages (#377 G3),
// /students, /students/new and the detail of a student the scan creates
// through the API first (#33 L2; with a linked guardian and a paper consent,
// so the Login account and Consents sections show, #35 K4), /guardians, /guardians/new and the detail
// of a guardian the scan creates and links to a new student first (#34 K2),
// /families, /families/new and the detail of a family the scan creates with
// that student and guardian (#172 J2), /tutors and the profile of a tutor
// the scan invites through the API first (#43 F2; an invitation only, no
// redemption; scanned once its Qualifications and Availability sections
// loaded, #44 G2, #45 G2), /courses and the course template editor
// (/courses/templates/new once its form loaded, #64 H2), /courses/cohorts
// and /courses/cohorts/new (once its form loaded, #243 E5), and
// the open notification panel
// (#41 F2, its own test below)
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
  /**
   * First invite a tutor through the API; `path` is then the tutor's
   * profile.
   */
  createsTutor?: boolean;
  /** Wait for this labelled field (the page's data loaded) before scanning. */
  waitForField?: string;
  /**
   * Also link a guardian to the created student and record a paper consent,
   * then wait for the Consents section (#35 K4) before scanning.
   */
  withConsent?: boolean;
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
  { name: 'approvals', path: '/approvals', signedIn: true },
  { name: 'account-password', path: '/account/password', signedIn: true },
  { name: 'catalog', path: '/catalog/education-levels', signedIn: true },
  { name: 'catalog-periods', path: '/catalog/periods', signedIn: true },
  { name: 'catalog-closures', path: '/catalog/closures', signedIn: true },
  { name: 'catalog-classrooms', path: '/catalog/classrooms', signedIn: true },
  {
    name: 'settings',
    path: '/settings',
    signedIn: true,
    waitForField: common.settings.brand.centerName,
  },
  {
    name: 'settings-scheduling-policies',
    path: '/settings/scheduling-policies',
    signedIn: true,
    waitForField: common.settings.schedulingPolicies.fields.changeNoticeHours,
  },
  // #363 F5: the price catalog (Billing → Prices).
  {
    name: 'billing-prices',
    path: '/billing/prices',
    signedIn: true,
    waitForField: common.billing.prices.fields.classType,
  },
  // #366 E4: the Unbilled and Settings tabs (billing stays off here).
  {
    name: 'billing-unbilled',
    path: '/billing/unbilled',
    signedIn: true,
    waitForField: common.billing.unbilled.filters.reason,
  },
  { name: 'billing-settings', path: '/billing/settings', signedIn: true },
  // #367 G4: the Discounts and Custom prices tabs.
  {
    name: 'billing-discounts',
    path: '/billing/discounts',
    signedIn: true,
    waitForField: common.billing.discounts.filters.q,
  },
  {
    name: 'billing-custom-prices',
    path: '/billing/custom-prices',
    signedIn: true,
    waitForField: common.billing.customPrices.filters.status,
  },
  // #374 H3: the Tuition plans tab.
  {
    name: 'billing-tuition-plans',
    path: '/billing/tuition-plans',
    signedIn: true,
    waitForField: common.billing.tuition.filters.searchStudent,
  },
  // #377 G3: the Packages tab (the sold list).
  {
    name: 'billing-packages',
    path: '/billing/packages',
    signedIn: true,
    waitForField: common.billing.packages.sold.filters.searchStudent,
  },
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
    withConsent: true,
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
  { name: 'tutors', path: '/tutors', signedIn: true },
  {
    name: 'tutor-detail',
    path: '/tutors/<created>',
    signedIn: true,
    createsTutor: true,
  },
  { name: 'courses', path: '/courses', signedIn: true },
  {
    name: 'course-template-new',
    path: '/courses/templates/new',
    signedIn: true,
    waitForField: common.courses.templates.name,
  },
  // #243 E5: the cohorts list and the create form (once its form loaded).
  { name: 'cohorts', path: '/courses/cohorts', signedIn: true },
  {
    name: 'cohort-new',
    path: '/courses/cohorts/new',
    signedIn: true,
    waitForField: common.courses.cohorts.fields.name,
  },
  { name: 'login', path: '/login', signedIn: false },
  { name: 'invite', path: '/invite', signedIn: false },
  { name: 'reset', path: '/reset', signedIn: false },
  { name: 'register', path: '/register', signedIn: false },
];

// Under machine load the first heading of a page gets FIRST_RENDER_TIMEOUT_MS
// (support/timeouts.ts, #252), inside a longer test timeout.
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
      // The theme through colour-scheme emulation, not a stored local choice:
      // signed in, the account's value wins (#36), and demo.owner's stored
      // `system` follows the emulated scheme. Signed out, the local default
      // (`system`) does too.
      test.use({ colorScheme: theme });
      test(`${route.name} has no axe violations in ${theme}`, async ({
        page,
      }, testInfo) => {
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
          if (route.createsTutor) {
            // The Qualifications section (#44 G2) loads after the heading.
            await expect(
              page
                .getByRole('main')
                .locator('#qualifications')
                .getByText(common.tutors.qualifications.empty),
            ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
          }
          if (route.withConsent) {
            const guardianId = await createGuardianViaApi(page, {
              givenNames: 'Elena',
              familyNames: `Accesible ${sfx}`,
            });
            await linkGuardianViaApi(page, id, guardianId);
            await recordConsentViaApi(page, id, guardianId);
          }
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
        if (route.createsTutor) {
          // The app origin first, so the API call carries the session.
          await page.goto('/');
          await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
            timeout: FIRST_RENDER_TIMEOUT_MS,
          });
          const sfx = `${theme}${testInfo.workerIndex}${Date.now().toString(36)}`;
          const id = await inviteTutorViaApi(page, {
            username: `e2e.a11y.tutor.${sfx}`,
            displayName: `Tutor Accesible ${sfx}`,
          });
          path = `/tutors/${id}`;
        }
        await page.goto(path);
        await expect(page).toHaveURL(path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
          timeout: FIRST_RENDER_TIMEOUT_MS,
        });
        if (route.withConsent) {
          // The login account and consent sections (#35 K4) load after the
          // heading.
          await expect(
            page
              .getByRole('main')
              .getByRole('heading', { level: 2, name: common.consent.title }),
          ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
          await expect(
            page.getByRole('main').getByText(common.consent.active),
          ).toBeVisible();
        }
        if (route.createsTutor) {
          // The Qualifications and Availability sections (#44 G2, #45 G2)
          // load after the heading.
          const main = page.getByRole('main');
          await expect(
            main
              .locator('#qualifications')
              .getByText(common.tutors.qualifications.empty),
          ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
          await expect(
            main
              .locator('#availability')
              .getByText(common.tutors.availability.exceptionsEmpty),
          ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
        }
        if (route.waitForField) {
          await expect(
            page.getByLabel(route.waitForField, { exact: true }),
          ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
        }
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

// #41 F2: the notification panel, open over the home page with at least the
// notification this test creates (the shared demo Owner state), in both
// themes.
for (const theme of THEMES) {
  test.describe(() => {
    test.describe.configure({ timeout: TEST_TIMEOUT_MS });
    test.use({ colorScheme: theme });
    test(`notification panel has no axe violations in ${theme}`, async ({
      page,
    }, testInfo) => {
      await recordCspViolations(page);
      const label = `A11y ${theme} ${testInfo.workerIndex}${Date.now().toString(36)}`;
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
        timeout: FIRST_RENDER_TIMEOUT_MS,
      });
      await createDemoNotificationViaApi(page, label);
      await page.reload();
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
        timeout: FIRST_RENDER_TIMEOUT_MS,
      });
      await page
        .getByRole('banner')
        .getByRole('button', {
          name: new RegExp(`^${common.notifications.bell}`),
        })
        .click();
      const panel = page.getByRole('dialog', {
        name: common.notifications.title,
      });
      await expect(panel.getByText(label, { exact: false })).toBeVisible();
      const html = page.locator('html');
      if (theme === 'dark') {
        await expect(html).toHaveClass(/\bdark\b/);
      } else {
        await expect(html).not.toHaveClass(/\bdark\b/);
      }

      const violations = await scan(page);
      expect(violations, violations.join('\n')).toEqual([]);
      expect(await cspViolations(page)).toEqual([]);
    });
  });
}

// #174 I3: the privacy notice as a new user sees it (to accept), in both
// themes, from one sign-in. Budgets (see support/auth.ts): 1 login (through
// the form; createActiveUser uses the shared Owner session) and 1
// redemption. The first sign-in stores the local default theme (`system`) in
// the account (#36), so colour-scheme emulation switches the theme.
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
      // The emulated colour scheme; a reload starts from it (the session
      // cookie stays, no new login).
      await page.emulateMedia({ colorScheme: theme });
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
