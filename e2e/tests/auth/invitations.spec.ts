import { expect, test, type Browser, type Page } from '@playwright/test';
import { api } from '../support/api';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, signedInAs } from '../support/catalog';
import { demoOwner } from '../support/credentials';

// Invite to login (#29 L1): the demo Owner invites an Academic coordinator,
// confirms the password in the reauthentication dialog (the first E2E of the
// dialog against a real endpoint), and the invited person opens the one-time
// link in another browser, sets a password and signs in. The used link is
// then no longer valid.
//
// Every context is fresh (SIGNED_OUT_STATE): parallel tests share the stored
// session and its reauthentication mark, so a test that needs the dialog
// signs in on its own (#28 H2).
//
// Budgets (#27, ADR-0008; see support/auth.ts): 2 logins (the Owner and the
// invited person) and 3 redemption calls (2 lookups, 1 redemption) per run.
// The username is unique per run: every `make e2e` starts from an empty
// database.

const USERNAME = 'e2e.coordinator';
const DISPLAY_NAME = 'E2E Coordinator';
const NEW_PASSWORD = 'e2e-coordinator-password';

// Chromium logs every 4xx resource as a console error; these are expected:
// the signed-out /auth/me (401), reauth_required before the dialog (403) and
// the used link (410).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|403|410)/;

// Three browsers, two logins, a dialog and four page loads: more than the
// default 30 s under machine load (the a11y spec has the same allowance).
const TEST_TIMEOUT_MS = 90_000;

async function freshPage(browser: Browser) {
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
  return { context, page, consoleErrors };
}

async function signIn(page: Page, username: string, password: string) {
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(username);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(password);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
}

test('an invited person sets a password with the one-time link and signs in', async ({
  browser,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // 1. The Owner signs in (login 1).
  const owner = await freshPage(browser);
  await owner.page.goto('/login');
  await signIn(owner.page, demoOwner.username, demoOwner.password);
  await expect(
    owner.page.getByRole('banner').getByText(signedInAs(common, 'Demo Owner')),
  ).toBeVisible();

  // 2. Invitations → invite an Academic coordinator.
  await owner.page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.invitations })
    .click();
  await expect(owner.page).toHaveURL('/invitations');
  const main = owner.page.getByRole('main');
  await expect(
    main.getByRole('heading', { level: 1, name: common.invitations.title }),
  ).toBeVisible();
  await main
    .getByLabel(common.invitations.form.username, { exact: true })
    .fill(USERNAME);
  await main
    .getByLabel(common.invitations.form.displayName, { exact: true })
    .fill(DISPLAY_NAME);
  await main
    .getByLabel(common.invitations.form.role, { exact: true })
    .selectOption({ label: common.roles.academic_coordinator! });
  await main
    .getByRole('button', { name: common.invitations.form.submit })
    .click();

  // 3. The reauthentication dialog, then the one-time link panel.
  const dialog = owner.page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog
    .getByLabel(common.auth.reauth.password, { exact: true })
    .fill(demoOwner.password);
  await dialog
    .getByRole('button', { name: common.auth.reauth.confirm })
    .click();
  await expect(dialog).toBeHidden();
  const linkField = main.getByLabel(common.invitations.result.link, {
    exact: true,
  });
  await expect(linkField).toBeVisible();
  await expect(main.getByText(common.oneTimeLink.onceWarning)).toBeVisible();
  const link = await linkField.inputValue();
  expect(link).toMatch(/\/invite#token=[A-Za-z0-9_-]{43}$/);
  expect(await cspViolations(owner.page)).toEqual([]);
  expect(owner.consoleErrors).toEqual([]);
  await owner.context.close();

  // 4. The invited person opens the link in another browser (lookup 1): the
  // fragment is gone from the address bar, the username is shown.
  const invited = await freshPage(browser);
  await invited.page.goto(link);
  const invitedMain = invited.page.getByRole('main');
  await expect(
    invitedMain.getByRole('heading', { level: 1, name: common.invite.title }),
  ).toBeVisible();
  await expect(
    invitedMain.getByLabel(common.invite.username, { exact: true }),
  ).toHaveValue(USERNAME);
  expect(new URL(invited.page.url()).hash).toBe('');
  expect(invited.page.url()).not.toContain('token');

  // 5. Set a password (redemption 1) → /login with the notice and the
  // username → sign in (login 2).
  await invitedMain
    .getByLabel(common.invite.newPassword, { exact: true })
    .fill(NEW_PASSWORD);
  await invitedMain
    .getByLabel(common.invite.confirmPassword, { exact: true })
    .fill(NEW_PASSWORD);
  await invitedMain.getByRole('button', { name: common.invite.submit }).click();
  await expect(invited.page).toHaveURL('/login');
  await expect(
    invitedMain.getByText(common.auth.login.invitationRedeemed),
  ).toBeVisible();
  const username = invitedMain.getByLabel(common.auth.login.username, {
    exact: true,
  });
  await expect(username).toHaveValue(USERNAME);
  await expect(
    invitedMain.getByLabel(common.auth.login.password, { exact: true }),
  ).toBeFocused();
  await invitedMain
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(NEW_PASSWORD);
  await invitedMain
    .getByRole('button', { name: common.auth.login.submit })
    .click();
  await expect(
    invited.page
      .getByRole('banner')
      .getByText(signedInAs(common, DISPLAY_NAME)),
  ).toBeVisible();
  const me = await api(invited.page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  expect((me.body as { roles: string[] }).roles).toEqual([
    'academic_coordinator',
  ]);
  expect(await cspViolations(invited.page)).toEqual([]);
  expect(invited.consoleErrors).toEqual([]);
  await invited.context.close();

  // 6. The same link again, in a third browser (lookup 2): no longer valid.
  const late = await freshPage(browser);
  await late.page.goto(link);
  const lateMain = late.page.getByRole('main');
  await expect(lateMain.getByText(errors.token_invalid)).toBeVisible();
  await expect(lateMain.locator('form')).toHaveCount(0);
  expect(await cspViolations(late.page)).toEqual([]);
  expect(late.consoleErrors).toEqual([]);
  await late.context.close();
});
