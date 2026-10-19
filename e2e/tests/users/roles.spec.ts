import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, signedInAs } from '../support/catalog';
import { demoOwner } from '../support/credentials';
import { createActiveUser } from '../support/users';

// Role assignment and delegations (#170 I1, I2).
//
// I1: the demo Owner gives a tutor the Finance staff role from the user
// detail page (the #28 password dialog against the real
// /auth/reauthenticate, then the retried request); the tutor's open session
// ends, and their next login has both roles.
// I2: the demo Owner delegates "Approve refunds" to an Administrator; the
// Administrator's existing session sees it on its next request, without
// signing in again.
//
// Every context is fresh (SIGNED_OUT_STATE): the reauthentication mark
// belongs to one session, so the dialog always opens, and logins never touch
// the shared session (#28 H2). The spec never changes `demo.owner`.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 7 logins (2 by
// createActiveUser; I1: the tutor twice and the Owner once; I2: the
// Administrator and the Owner once each), no wrong password, and 2
// redemption calls (createActiveUser, twice). The
// Administrator invitation also reauthenticates the demo Owner once in the
// helper's own API context. Usernames are unique per run: every `make e2e`
// starts from an empty database.

const TUTOR = 'e2e.roles.tutor';
const TUTOR_NAME = 'E2E Roles Tutor';
const ADMIN = 'e2e.roles.admin';
const ADMIN_NAME = 'E2E Roles Admin';
const PASSWORD = 'e2e-roles-password';

// Chromium logs every 4xx resource as a console error; these are expected:
// the signed-out /auth/me and the ended session (401), and reauth_required
// before the password dialog (403).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|403)/;

// Several browsers, logins and dialogs: more than the default 30 s under
// machine load (as in suspend.spec.ts).
const TEST_TIMEOUT_MS = 90_000;

interface FreshPage {
  page: Page;
  close: () => Promise<void>;
  /** No CSP violation and no unexpected console error (I3). */
  expectClean: () => Promise<void>;
}

async function freshPage(browser: Browser): Promise<FreshPage> {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (
      message.type() === 'error' &&
      !EXPECTED_RESOURCE_ERROR.test(message.text())
    ) {
      consoleErrors.push(message.text());
    }
  });
  return {
    page,
    close: () => context.close(),
    expectClean: async () => {
      expect(await cspViolations(page)).toEqual([]);
      expect(consoleErrors).toEqual([]);
    },
  };
}

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/login');
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(username);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(password);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
}

async function expectSignedInAs(page: Page, name: string) {
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, name)),
  ).toBeVisible();
}

/** Opens a user's detail page from the Users list (search, then the row). */
async function openFromList(page: Page, search: string, name: string) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.users })
    .click();
  await expect(page).toHaveURL('/users');
  const main = page.getByRole('main');
  await main.getByLabel(common.users.list.search, { exact: true }).fill(search);
  await expect(page).toHaveURL(/[?&]q=/);
  await main.getByRole('link', { name, exact: true }).click();
  await expect(
    main.getByRole('heading', { level: 1, name, exact: true }),
  ).toBeVisible();
}

/** Confirms the #28 password dialog with the demo Owner's password. */
async function confirmPassword(page: Page) {
  const reauth = page.getByRole('dialog', { name: common.auth.reauth.title });
  await expect(reauth).toBeVisible();
  await reauth
    .getByLabel(common.auth.reauth.password, { exact: true })
    .fill(demoOwner.password);
  await reauth
    .getByRole('button', { name: common.auth.reauth.confirm })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
}

/** GET /api/v1/auth/me from the page, with its own session cookie. */
async function me(page: Page): Promise<{
  roles: string[];
  permissions: string[];
}> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/me');
    if (!response.ok) {
      throw new Error(`/auth/me answered ${response.status}`);
    }
    return (await response.json()) as {
      roles: string[];
      permissions: string[];
    };
  });
}

test('a role change ends the user’s sessions; the next login has the new role', async ({
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // 1. The tutor exists with P1 (login 1, redemption call 1) and is signed
  // in in context B (login 2).
  await createActiveUser(playwright.request, baseURL!, {
    username: TUTOR,
    role: 'tutor',
    password: PASSWORD,
    displayName: TUTOR_NAME,
  });
  const tutor = await freshPage(browser);
  await signIn(tutor.page, TUTOR, PASSWORD);
  await expectSignedInAs(tutor.page, TUTOR_NAME);

  // 2. Context A: the demo Owner (login 3) opens the tutor, checks Finance
  // staff and saves → the password dialog → the roles show both.
  const owner = await freshPage(browser);
  await signIn(owner.page, demoOwner.username, demoOwner.password);
  await expectSignedInAs(owner.page, 'Demo Owner');
  await openFromList(owner.page, TUTOR, TUTOR_NAME);
  const roles = owner.page.getByRole('region', {
    name: common.users.roles.title,
  });
  const financeStaff = roles.getByRole('checkbox', {
    name: common.roles.finance_staff,
    exact: true,
  });
  const tutorBox = roles.getByRole('checkbox', {
    name: common.roles.tutor,
    exact: true,
  });
  await expect(tutorBox).toBeChecked();
  await expect(financeStaff).not.toBeChecked();
  await financeStaff.click();
  await roles.getByRole('button', { name: common.users.roles.save }).click();
  await confirmPassword(owner.page);
  await expect(roles.getByText(common.users.roles.saved)).toBeVisible();
  await expect(financeStaff).toBeChecked();
  await expect(tutorBox).toBeChecked();
  await owner.expectClean();
  await owner.close();

  // 3. B's next navigation lands on /login; signing in again (login 4)
  // gives both roles.
  await tutor.page.goto('/');
  await expect(tutor.page).toHaveURL(/\/login/);
  await signIn(tutor.page, TUTOR, PASSWORD);
  await expectSignedInAs(tutor.page, TUTOR_NAME);
  expect((await me(tutor.page)).roles).toEqual(['finance_staff', 'tutor']);
  await tutor.expectClean();
  await tutor.close();
});

test('a delegation reaches the Administrator without a new sign-in', async ({
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // 1. The Administrator exists (login 5 and a reauthentication in the
  // helper; redemption call 2) and is signed in in context C (login 6).
  await createActiveUser(playwright.request, baseURL!, {
    username: ADMIN,
    role: 'administrator',
    password: PASSWORD,
    displayName: ADMIN_NAME,
  });
  const admin = await freshPage(browser);
  await signIn(admin.page, ADMIN, PASSWORD);
  await expectSignedInAs(admin.page, ADMIN_NAME);
  expect((await me(admin.page)).permissions).not.toContain(
    'finance.refund.approve',
  );

  // 2. A fresh Owner context (login 7) opens the Administrator, checks
  // "Approve refunds" and saves → the password dialog → saved.
  const owner = await freshPage(browser);
  await signIn(owner.page, demoOwner.username, demoOwner.password);
  await expectSignedInAs(owner.page, 'Demo Owner');
  await openFromList(owner.page, ADMIN, ADMIN_NAME);
  const delegations = owner.page.getByRole('region', {
    name: common.users.delegations.title,
  });
  const refunds = delegations.getByRole('checkbox', {
    name: common.delegations.finance_refund_approve!.label,
    exact: true,
  });
  await expect(refunds).not.toBeChecked();
  await refunds.click();
  await delegations
    .getByRole('button', { name: common.users.delegations.save })
    .click();
  await confirmPassword(owner.page);
  await expect(
    delegations.getByText(common.users.delegations.saved),
  ).toBeVisible();
  await expect(refunds).toBeChecked();
  await owner.expectClean();
  await owner.close();

  // 3. The same session in C (no new login) now holds the permission.
  expect((await me(admin.page)).permissions).toContain(
    'finance.refund.approve',
  );
  await admin.page.reload();
  await expectSignedInAs(admin.page, ADMIN_NAME);
  await admin.expectClean();
  await admin.close();
});
