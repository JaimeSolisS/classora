import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, signedInAs } from '../support/catalog';

// Request, approve, sign in (#169 I1): a parent or guardian requests an
// account on /register (reached from /login), can't sign in while it is
// pending, the demo Owner approves it on /registration-requests (the shared
// signed-in state: approval needs no reauthentication), and the person then
// signs in.
//
// Budgets (#169 I2; #27, ADR-0008; see support/auth.ts): 1 registration
// request (`registration.ip`: 10 per hour), 2 logins (`login.ip`), and 1
// failed login on `e2e.parent`'s own `login.username` budget (the pending
// account). The username is unique per run: every `make e2e` starts from an
// empty database.

const USERNAME = 'e2e.parent';
const DISPLAY_NAME = 'E2E Parent';
const PASSWORD = 'e2e-parent-password';

// Chromium logs every 4xx resource as a console error; these are expected:
// the signed-out /auth/me and the refused login of the pending account (401).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 401/;

// Two browsers, two logins and a dialog: more than the default 30 s under
// machine load (the invitation spec has the same allowance).
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

async function signIn(page: Page) {
  const main = page.getByRole('main');
  await main
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(USERNAME);
  await main
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(PASSWORD);
  await main.getByRole('button', { name: common.auth.login.submit }).click();
}

test('a parent requests an account, the Owner approves it and the parent signs in', async ({
  browser,
  page: owner,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // 1. A fresh anonymous browser: /login → "Request an account" → the form
  // (registration 1) → the confirmation.
  const requester = await freshPage(browser);
  await requester.page.goto('/login');
  const main = requester.page.getByRole('main');
  await main
    .getByRole('link', { name: common.auth.login.requestAccount })
    .click();
  await expect(requester.page).toHaveURL('/register');
  await expect(
    main.getByRole('heading', { level: 1, name: common.register.title }),
  ).toBeVisible();
  await main
    .getByRole('group', { name: common.register.role.legend })
    .getByRole('radio', { name: common.register.role.guardian })
    .check();
  await main
    .getByLabel(common.register.displayName, { exact: true })
    .fill(DISPLAY_NAME);
  await main
    .getByLabel(common.register.username, { exact: true })
    .fill(USERNAME);
  await main
    .getByLabel(common.register.password, { exact: true })
    .fill(PASSWORD);
  await main
    .getByLabel(common.register.confirmPassword, { exact: true })
    .fill(PASSWORD);
  await main.getByRole('button', { name: common.register.submit }).click();
  await expect(main.getByRole('status')).toHaveText(
    common.register.sent.replace('{{username}}', USERNAME),
  );
  await expect(main.locator('form')).toHaveCount(0);

  // 2. Signing in while the account is pending (login 1) is refused.
  await main.getByRole('link', { name: common.register.backToLogin }).click();
  await expect(requester.page).toHaveURL('/login');
  await signIn(requester.page);
  await expect(main.getByRole('alert')).toContainText(
    errors.invalid_credentials,
  );

  // 3. The demo Owner (the shared session) approves the request.
  await owner.goto('/');
  await owner
    .getByRole('banner')
    .getByRole('link', { name: common.nav.registrations })
    .click();
  await expect(owner).toHaveURL('/registration-requests');
  const ownerMain = owner.getByRole('main');
  await expect(
    ownerMain.getByRole('heading', {
      level: 1,
      name: common.registrations.title,
    }),
  ).toBeVisible();
  const approveButton = ownerMain.getByRole('button', {
    name: common.registrations.approveFor.replace('{{username}}', USERNAME),
  });
  await approveButton.click();
  const dialog = owner.getByRole('dialog');
  await expect(dialog).toContainText(DISPLAY_NAME);
  await dialog
    .getByRole('button', { name: common.registrations.approve, exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await expect(ownerMain.getByRole('status')).toHaveText(
    common.registrations.approved,
  );
  // The row left the Pending view.
  await expect(approveButton).toHaveCount(0);

  // 4. Back in the requester's browser, signing in works (login 2).
  await signIn(requester.page);
  await expect(
    requester.page
      .getByRole('banner')
      .getByText(signedInAs(common, DISPLAY_NAME)),
  ).toBeVisible();
  expect(await cspViolations(requester.page)).toEqual([]);
  expect(requester.consoleErrors).toEqual([]);
  await requester.context.close();
});
