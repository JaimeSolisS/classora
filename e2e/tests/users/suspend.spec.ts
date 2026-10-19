import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, signedInAs } from '../support/catalog';
import { demoOwner } from '../support/credentials';
import { createActiveUser } from '../support/users';

// Suspension and reactivation (#31 M1, M2): "the user then loses access".
//
// The demo Owner suspends a tutor from the user detail page (confirmation,
// then the #28 password dialog against the real /auth/reauthenticate, and
// the retried request succeeds); the tutor's open session ends and their
// login is refused. Reactivating needs no password, and the tutor can sign
// in again. The Owner's own record has no Suspend or Archive.
//
// Every context is fresh (SIGNED_OUT_STATE): the reauthentication mark
// belongs to one session, and logins never touch the shared session (#28
// H2). The spec never changes `demo.owner`.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 5 logins (1 by
// createActiveUser), 1 of them refused for the tutor's own username while
// suspended, and 1 redemption call (createActiveUser). No
// setting that disables limits is used. Usernames are unique per run: every
// `make e2e` starts from an empty database.

const TUTOR = 'e2e.suspend.tutor';
const TUTOR_NAME = 'E2E Suspend Tutor';
const PASSWORD = 'e2e-suspend-password';

// Chromium logs every 4xx resource as a console error; these are expected:
// the signed-out /auth/me and the ended session or refused login (401), and
// reauth_required before the password dialog (403).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|403)/;

// Several browsers, logins and dialogs: more than the default 30 s under
// machine load (as in password-reset.spec.ts).
const TEST_TIMEOUT_MS = 90_000;

interface FreshPage {
  page: Page;
  close: () => Promise<void>;
  /** No CSP violation and no unexpected console error (M2). */
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
  await expect(
    main.getByRole('heading', { level: 1, name: common.users.title }),
  ).toBeVisible();
  await main.getByLabel(common.users.list.search, { exact: true }).fill(search);
  await expect(page).toHaveURL(/[?&]q=/);
  await main.getByRole('link', { name, exact: true }).click();
  await expect(
    main.getByRole('heading', { level: 1, name, exact: true }),
  ).toBeVisible();
}

test('suspending a user ends their access; reactivating restores it', async ({
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

  // 2. Context A: the demo Owner (login 3) opens Users, searches and opens
  // the tutor.
  const owner = await freshPage(browser);
  await signIn(owner.page, demoOwner.username, demoOwner.password);
  await expectSignedInAs(owner.page, 'Demo Owner');
  await openFromList(owner.page, 'e2e.suspend', TUTOR_NAME);
  const main = owner.page.getByRole('main');
  await expect(
    main.getByText(common.userStates.active, { exact: true }),
  ).toBeVisible();

  // 3. Suspend → confirm → the password dialog → the badge says Suspended.
  await main
    .getByRole('button', { name: common.users.status.suspend, exact: true })
    .click();
  const confirm = owner.page.getByRole('dialog', {
    name: common.users.status.suspendTitle,
  });
  await expect(confirm).toBeVisible();
  await confirm
    .getByRole('button', { name: common.users.status.suspend, exact: true })
    .click();
  const reauth = owner.page.getByRole('dialog', {
    name: common.auth.reauth.title,
  });
  await expect(reauth).toBeVisible();
  await reauth
    .getByLabel(common.auth.reauth.password, { exact: true })
    .fill(demoOwner.password);
  await reauth
    .getByRole('button', { name: common.auth.reauth.confirm })
    .click();
  await expect(owner.page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.getByText(common.userStates.suspended, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByRole('button', {
      name: common.users.status.reactivate,
      exact: true,
    }),
  ).toBeVisible();

  // 4. Context B's next navigation lands on /login; signing in with P1 is
  // refused (login 4, the failure).
  await tutor.page.goto('/');
  await expect(tutor.page).toHaveURL(/\/login/);
  await signIn(tutor.page, TUTOR, PASSWORD);
  await expect(
    tutor.page.getByRole('main').getByText(errors.invalid_credentials),
  ).toBeVisible();
  await expect(tutor.page).toHaveURL(/\/login/);

  // 5. Reactivate → confirm (no password dialog) → Active. B signs in with
  // P1 (login 5) and sees the tutor's name.
  await main
    .getByRole('button', { name: common.users.status.reactivate, exact: true })
    .click();
  const reactivate = owner.page.getByRole('dialog', {
    name: common.users.status.reactivateTitle,
  });
  await reactivate
    .getByRole('button', { name: common.users.status.reactivate, exact: true })
    .click();
  await expect(owner.page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.getByText(common.userStates.active, { exact: true }),
  ).toBeVisible();
  await signIn(tutor.page, TUTOR, PASSWORD);
  await expectSignedInAs(tutor.page, TUTOR_NAME);
  await tutor.expectClean();
  await tutor.close();

  // 6. The Owner's own record: no Suspend or Archive.
  await openFromList(owner.page, demoOwner.username, 'Demo Owner');
  await expect(
    main.getByRole('button', { name: common.users.status.suspend }),
  ).toHaveCount(0);
  await expect(
    main.getByRole('button', { name: common.users.status.archive }),
  ).toHaveCount(0);
  await owner.expectClean();
  await owner.close();
});
