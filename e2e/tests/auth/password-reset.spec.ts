import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, signedInAs } from '../support/catalog';
import { demoOwner } from '../support/credentials';
import { createActiveUser } from '../support/users';

// Password reset and change (#30 M2, M3).
//
// M2: the demo Owner issues a reset link for a tutor (with the
// reauthentication dialog); the tutor sets a new password with it in another
// browser, and the tutor's earlier session ends.
// M3: a guardian changes their password on /account/password; the session
// that changed it stays signed in with the rotated CSRF token (Sign out
// works), the guardian's other session ends.
//
// Every context is fresh (SIGNED_OUT_STATE): a test that needs the dialog
// signs in on its own, and logins never touch the shared session (#28 H2).
//
// Budgets (#27, ADR-0008; see support/auth.ts): 6 logins (createActiveUser
// uses the shared Owner session, #36), 5 redemption calls (2 of them by createActiveUser, one
// each) and one wrong current password (reauth.user). No
// setting that disables limits is used. Usernames are unique per run:
// every `make e2e` starts from an empty database.

const TUTOR = 'e2e.reset.tutor';
const TUTOR_NAME = 'E2E Reset Tutor';
const GUARDIAN = 'e2e.change.guardian';
const GUARDIAN_NAME = 'E2E Change Guardian';
const FIRST_PASSWORD = 'e2e-first-password';
const SECOND_PASSWORD = 'e2e-second-password';

// Chromium logs every 4xx resource as a console error; these are expected:
// the signed-out /auth/me and the ended session (401), reauth_required before
// the dialog and the wrong current password (403), and the used link (410).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|403|410)/;

// Several browsers, logins and a dialog per test: more than the default 30 s
// under machine load (as in invitations.spec.ts).
const TEST_TIMEOUT_MS = 90_000;

interface FreshPage {
  page: Page;
  close: () => Promise<void>;
  /** No CSP violation and no unexpected console error (M4). */
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
  // A new user lands on the privacy notice first (#174).
  await acceptPrivacyNoticeIfShown(page);
}

test('staff issue a reset link; the person sets a new password and the old session ends', async ({
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
    password: FIRST_PASSWORD,
    displayName: TUTOR_NAME,
  });
  const tutorBefore = await freshPage(browser);
  await signIn(tutorBefore.page, TUTOR, FIRST_PASSWORD);
  await expectSignedInAs(tutorBefore.page, TUTOR_NAME);

  // 2. The Owner (login 3) opens "Password resets", enters the username and
  // confirms the password in the dialog; the panel shows the link.
  const owner = await freshPage(browser);
  await signIn(owner.page, demoOwner.username, demoOwner.password);
  await expectSignedInAs(owner.page, 'Demo Owner');
  await owner.page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.passwordResets })
    .click();
  await expect(owner.page).toHaveURL('/password-resets');
  const main = owner.page.getByRole('main');
  await expect(
    main.getByRole('heading', { level: 1, name: common.passwordResets.title }),
  ).toBeVisible();
  const usernameField = main.getByLabel(common.passwordResets.form.username, {
    exact: true,
  });
  await usernameField.fill(TUTOR);
  await main
    .getByRole('button', { name: common.passwordResets.form.submit })
    .click();
  const dialog = owner.page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog
    .getByLabel(common.auth.reauth.password, { exact: true })
    .fill(demoOwner.password);
  await dialog
    .getByRole('button', { name: common.auth.reauth.confirm })
    .click();
  await expect(dialog).toBeHidden();
  const linkField = main.getByLabel(common.passwordResets.result.link, {
    exact: true,
  });
  await expect(linkField).toBeVisible();
  await expect(main.getByText(common.oneTimeLink.onceWarning)).toBeVisible();
  const link = await linkField.inputValue();
  expect(link).toMatch(/\/reset#token=[A-Za-z0-9_-]{43}$/);

  // 5. Entering the Owner's own username shows the hint and sends nothing.
  let requests = 0;
  owner.page.on('request', (request) => {
    if (request.url().includes('/api/v1/password-resets')) {
      requests += 1;
    }
  });
  await usernameField.fill(demoOwner.username);
  await expect(main.getByText(common.passwordResets.form.self)).toBeVisible();
  await main
    .getByRole('button', { name: common.passwordResets.form.submit })
    .click();
  await expect(owner.page.getByRole('dialog')).toHaveCount(0);
  expect(requests).toBe(0);
  await owner.expectClean();
  await owner.close();

  // 3. The tutor opens the link in context C (lookup: call 2): no fragment in
  // the address bar, the username is shown. Set P2 (call 3) → /login with
  // the notice and the username → sign in (login 4).
  const resetter = await freshPage(browser);
  await resetter.page.goto(link);
  const resetMain = resetter.page.getByRole('main');
  await expect(
    resetMain.getByRole('heading', { level: 1, name: common.reset.title }),
  ).toBeVisible();
  await expect(
    resetMain.getByLabel(common.invite.username, { exact: true }),
  ).toHaveValue(TUTOR);
  expect(new URL(resetter.page.url()).hash).toBe('');
  expect(resetter.page.url()).not.toContain('token');
  await resetMain
    .getByLabel(common.invite.newPassword, { exact: true })
    .fill(SECOND_PASSWORD);
  await resetMain
    .getByLabel(common.invite.confirmPassword, { exact: true })
    .fill(SECOND_PASSWORD);
  await resetMain.getByRole('button', { name: common.invite.submit }).click();
  await expect(resetter.page).toHaveURL('/login');
  await expect(
    resetMain.getByText(common.auth.login.passwordReset),
  ).toBeVisible();
  await expect(
    resetMain.getByLabel(common.auth.login.username, { exact: true }),
  ).toHaveValue(TUTOR);
  await resetMain
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(SECOND_PASSWORD);
  await resetMain
    .getByRole('button', { name: common.auth.login.submit })
    .click();
  await expectSignedInAs(resetter.page, TUTOR_NAME);
  await resetter.expectClean();
  await resetter.close();

  // 4. Context B's session was revoked: the next navigation lands on /login.
  await tutorBefore.page.goto('/');
  await expect(tutorBefore.page).toHaveURL(/\/login/);
  await tutorBefore.expectClean();
  await tutorBefore.close();

  // 6. The same link in context D (lookup: call 4): no longer valid.
  const late = await freshPage(browser);
  await late.page.goto(link);
  const lateMain = late.page.getByRole('main');
  await expect(lateMain.getByText(errors.token_invalid)).toBeVisible();
  await expect(lateMain.locator('form')).toHaveCount(0);
  await late.expectClean();
  await late.close();
});

test('a signed-in user changes their password; other sessions end', async ({
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // The guardian exists with P1 (login 5, redemption call 5); contexts A
  // and B sign in (logins 6 and 7).
  await createActiveUser(playwright.request, baseURL!, {
    username: GUARDIAN,
    role: 'guardian',
    password: FIRST_PASSWORD,
    displayName: GUARDIAN_NAME,
  });
  const a = await freshPage(browser);
  await signIn(a.page, GUARDIAN, FIRST_PASSWORD);
  await expectSignedInAs(a.page, GUARDIAN_NAME);
  const b = await freshPage(browser);
  await signIn(b.page, GUARDIAN, FIRST_PASSWORD);
  await expectSignedInAs(b.page, GUARDIAN_NAME);

  // In A: "Change password" from the header.
  await a.page
    .getByRole('banner')
    .getByRole('link', { name: common.account.password.link })
    .click();
  await expect(a.page).toHaveURL('/account/password');
  const main = a.page.getByRole('main');
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: common.account.password.title,
    }),
  ).toBeVisible();
  const current = main.getByLabel(common.account.password.current, {
    exact: true,
  });
  const fresh = main.getByLabel(common.account.password.new, { exact: true });
  const confirm = main.getByLabel(common.account.password.confirm, {
    exact: true,
  });
  const submit = main.getByRole('button', {
    name: common.account.password.submit,
  });

  // A wrong current password (the one reauth.user failure) → field error.
  await current.fill('not-the-current-password');
  await fresh.fill(SECOND_PASSWORD);
  await confirm.fill(SECOND_PASSWORD);
  await submit.click();
  await expect(current).toHaveAttribute('aria-invalid', 'true');
  await expect(main.getByText(errors.current_password_invalid)).toBeVisible();

  // The right one → the success status; A stays signed in.
  await current.fill(FIRST_PASSWORD);
  await fresh.fill(SECOND_PASSWORD);
  await confirm.fill(SECOND_PASSWORD);
  await submit.click();
  await expect(main.getByText(common.account.password.done)).toBeVisible();
  await expect(a.page).toHaveURL('/account/password');
  await expectSignedInAs(a.page, GUARDIAN_NAME);

  // Sign out works: it needs the rotated CSRF token.
  await a.page
    .getByRole('banner')
    .getByRole('button', { name: common.auth.userMenu.signOut })
    .click();
  await expect(a.page).toHaveURL('/login');
  await a.expectClean();
  await a.close();

  // B's session ended: the next navigation lands on /login; P2 signs in
  // (login 8).
  await b.page.goto('/');
  await expect(b.page).toHaveURL(/\/login/);
  await signIn(b.page, GUARDIAN, SECOND_PASSWORD);
  await expectSignedInAs(b.page, GUARDIAN_NAME);
  await b.expectClean();
  await b.close();
});
