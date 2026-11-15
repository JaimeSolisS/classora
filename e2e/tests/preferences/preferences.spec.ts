import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
} from '@playwright/test';
import { api } from '../support/api';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
} from '../support/auth';
import { common, commonEn, signedInAs } from '../support/catalog';
import { createActiveUser } from '../support/users';

// User preferences (#36 H1). A new tutor's language and theme follow the
// account: the first sign-in fills the account from the browser's local
// (M1) values, a switch while signed in is saved, and another device with
// other local values gets the account's values at sign-in. Signing out keeps
// the language and theme on the login page.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 2 logins (the tutor in
// context A and in context B) and 1 redemption call (createActiveUser, which
// invites with the shared Owner session: no login). The shared `demo.owner`
// session is never used for a switch. Every `make e2e` starts from an empty
// database, so the username is unique per run.

const TUTOR = 'e2e.prefs.tutor';
const TUTOR_NAME = 'E2E Prefs Tutor';
const PASSWORD = 'e2e-prefs-password';

// Chromium logs every 4xx resource as a console error: the signed-out
// /auth/me (401) is expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 401/;

const TEST_TIMEOUT_MS = 90_000;

type Preferences = { language: string | null; theme: string | null };

/**
 * A fresh context (another browser, no session) whose web origin already
 * holds the M1 local values `en` and `dark`. Set through `storageState`, not
 * an init script, so a reload doesn't reset them.
 */
async function deviceWithEnglishDark(
  browser: Browser,
  baseURL: string,
): Promise<BrowserContext> {
  return browser.newContext({
    storageState: {
      cookies: [],
      origins: [
        {
          origin: new URL(baseURL).origin,
          localStorage: [
            { name: 'classora.language', value: 'en' },
            { name: 'classora.theme', value: 'dark' },
          ],
        },
      ],
    },
  });
}

async function watch(page: Page): Promise<string[]> {
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
  return consoleErrors;
}

/** Signs the tutor in through the English login form (one login). */
async function signInInEnglish(page: Page): Promise<void> {
  await page.goto('/login');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page
    .getByLabel(commonEn.auth.login.username, { exact: true })
    .fill(TUTOR);
  await page
    .getByLabel(commonEn.auth.login.password, { exact: true })
    .fill(PASSWORD);
  await page.getByRole('button', { name: commonEn.auth.login.submit }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function accountPreferences(page: Page): Promise<Preferences> {
  const me = await api(page, '/api/v1/auth/me');
  return (me.body as { preferences: Preferences }).preferences;
}

async function localValues(
  page: Page,
): Promise<[string | null, string | null]> {
  return page.evaluate(() => [
    window.localStorage.getItem('classora.language'),
    window.localStorage.getItem('classora.theme'),
  ]);
}

test('language and theme follow the account across devices', async ({
  browser,
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
  const html = (page: Page) => page.locator('html');

  // 2. Context A (login 1): local en/dark, an account with no values.
  const contextA = await deviceWithEnglishDark(browser, baseURL!);
  const contextB = await deviceWithEnglishDark(browser, baseURL!);
  try {
    const a = await contextA.newPage();
    const errorsA = await watch(a);
    await signInInEnglish(a);
    await acceptPrivacyNoticeIfShown(a);
    await expect(
      a.getByRole('banner').getByText(signedInAs(commonEn, TUTOR_NAME)),
    ).toBeVisible();
    await expect(html(a)).toHaveAttribute('lang', 'en');
    await expect(html(a)).toHaveClass(/\bdark\b/);
    // The first sign-in filled the account with the local values.
    await expect
      .poll(() => accountPreferences(a))
      .toEqual({ language: 'en', theme: 'dark' });

    // 3. In A: "Español", then "Claro": applied at once and saved.
    const bannerA = a.getByRole('banner');
    await bannerA
      .getByRole('button', { name: common.language.options.es, exact: true })
      .click();
    await expect(html(a)).toHaveAttribute('lang', 'es');
    await bannerA
      .getByRole('button', { name: common.theme.options.light, exact: true })
      .click();
    await expect(html(a)).not.toHaveClass(/\bdark\b/);
    await expect(
      bannerA.getByText(signedInAs(common, TUTOR_NAME)),
    ).toBeVisible();
    await expect
      .poll(() => accountPreferences(a))
      .toEqual({ language: 'es', theme: 'light' });

    // 4. Context B (login 2), another device with local en/dark: the
    // account wins, and the local values follow it.
    const b = await contextB.newPage();
    const errorsB = await watch(b);
    await signInInEnglish(b);
    await acceptPrivacyNoticeIfShown(b);
    const bannerB = b.getByRole('banner');
    await expect(
      bannerB.getByText(signedInAs(common, TUTOR_NAME)),
    ).toBeVisible();
    await expect(html(b)).toHaveAttribute('lang', 'es');
    await expect(html(b)).not.toHaveClass(/\bdark\b/);
    expect(await localValues(b)).toEqual(['es', 'light']);
    await b.reload();
    await expect(
      bannerB.getByText(signedInAs(common, TUTOR_NAME)),
    ).toBeVisible();
    await expect(html(b)).toHaveAttribute('lang', 'es');
    await expect(html(b)).not.toHaveClass(/\bdark\b/);

    // 5. In B, "Cerrar sesión": the login page stays Spanish and light.
    await bannerB
      .getByRole('button', { name: common.auth.userMenu.signOut })
      .click();
    await expect(b).toHaveURL('/login');
    await expect(
      b.getByRole('heading', { level: 1, name: common.auth.login.title }),
    ).toBeVisible();
    await expect(html(b)).toHaveAttribute('lang', 'es');
    await expect(html(b)).not.toHaveClass(/\bdark\b/);

    // 6. No CSP violation and no console error on either device.
    expect(await cspViolations(a)).toEqual([]);
    expect(await cspViolations(b)).toEqual([]);
    expect(errorsA).toEqual([]);
    expect(errorsB).toEqual([]);
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
