import { expect, test, type Page } from '@playwright/test';
import { api } from '../support/api';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, commonEn, fill, signedInAs } from '../support/catalog';
import { createActiveUser } from '../support/users';

// The privacy notice (#174 I2). A new tutor signs in through the login form
// (not the shared helper), lands on the notice with `next`, reads version 1
// and its draft line, switches to English, accepts and returns to the page
// they asked for. Signing out and in again shows no notice, and /auth/me
// says privacy_notice_required: false.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 3 logins (1 by
// createActiveUser, then the tutor twice), no wrong password, and 1
// redemption call (createActiveUser). The username has a per-run random
// suffix with its own prefix, so it never matches another spec's search.

const SUFFIX = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
const TUTOR = `e2e.notice.${SUFFIX}`;
const TUTOR_NAME = `E2E Notice ${SUFFIX}`;
const PASSWORD = 'e2e-notice-password';

// Chromium logs every 4xx resource as a console error: the signed-out
// /auth/me (401) is expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 401/;

const TEST_TIMEOUT_MS = 90_000;

test.use({ storageState: SIGNED_OUT_STATE });

function recordConsoleErrors(page: Page): string[] {
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
  return consoleErrors;
}

async function signIn(
  page: Page,
  catalog: typeof common,
  username: string,
  password: string,
) {
  await page
    .getByLabel(catalog.auth.login.username, { exact: true })
    .fill(username);
  await page
    .getByLabel(catalog.auth.login.password, { exact: true })
    .fill(password);
  await page.getByRole('button', { name: catalog.auth.login.submit }).click();
}

test('a new user accepts the privacy notice once, then goes where they asked', async ({
  page,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await createActiveUser(playwright.request, baseURL!, {
    username: TUTOR,
    role: 'tutor',
    password: PASSWORD,
    displayName: TUTOR_NAME,
  });
  await recordCspViolations(page);
  const consoleErrors = recordConsoleErrors(page);
  const main = page.getByRole('main');
  const notice = common.privacyNotice;
  const noticeEn = commonEn.privacyNotice;

  // 1. A guarded page asks for a sign-in (login 2), then the notice.
  await page.goto('/students');
  await expect(page).toHaveURL(
    `/login?next=${encodeURIComponent('/students')}`,
  );
  await signIn(page, common, TUTOR, PASSWORD);
  await expect(page).toHaveURL(
    `/privacy-notice?next=${encodeURIComponent('/students')}`,
  );
  const heading = main.getByRole('heading', { level: 1, name: notice.title });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, TUTOR_NAME)),
  ).toBeVisible();
  // No navigation items before acceptance.
  await expect(page.getByRole('banner').getByRole('navigation')).toHaveCount(0);
  await expect(main).toContainText(
    fill(notice.version, { version: 1, date: '' }).replace(/\s*$/, ''),
  );
  const text = main.getByRole('region', { name: notice.textLabel });
  await expect(text.locator('p').first()).toHaveText(
    'Borrador pendiente de revisión legal.',
  );

  // 2. English: the other text of the same response.
  await page
    .getByRole('banner')
    .getByRole('button', { name: 'English', exact: true })
    .click();
  const textEn = main.getByRole('region', { name: noticeEn.textLabel });
  await expect(textEn.locator('p').first()).toHaveText(
    'Draft pending legal review.',
  );
  await expect(main).toContainText('Version 1, published');

  // 3. "I accept" → back to /students.
  await main.getByRole('button', { name: noticeEn.accept }).click();
  await expect(page).toHaveURL('/students');
  await expect(
    main.getByRole('heading', { level: 1, name: noticeEn.title }),
  ).toHaveCount(0);
  await expect(page.getByRole('banner').getByRole('navigation')).toBeVisible();

  // 4. Sign out, sign in again (login 3): no notice.
  await page
    .getByRole('banner')
    .getByRole('button', { name: commonEn.auth.userMenu.signOut })
    .click();
  await expect(page).toHaveURL('/login');
  await signIn(page, commonEn, TUTOR, PASSWORD);
  await expect(page).toHaveURL('/');
  await expect(
    page.getByRole('banner').getByText(signedInAs(commonEn, TUTOR_NAME)),
  ).toBeVisible();
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  expect(
    (me.body as { privacy_notice_required: boolean }).privacy_notice_required,
  ).toBe(false);

  // 5. The user menu still opens the notice: accepted, no button.
  await page
    .getByRole('banner')
    .getByRole('link', { name: noticeEn.menu })
    .click();
  await expect(page).toHaveURL('/privacy-notice');
  await expect(main.getByRole('button', { name: noticeEn.accept })).toHaveCount(
    0,
  );
  await expect(main).toContainText(
    noticeEn.accepted.replace('{{date}}', '').trim(),
  );

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
