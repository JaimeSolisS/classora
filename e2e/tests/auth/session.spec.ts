import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, commonEn, errors, signedInAs } from '../support/catalog';
import { demoOwner } from '../support/credentials';

// The session cookie in Chromium (M2-05). The E2E stack runs in the local
// cookie mode (SESSION_COOKIE_SECURE=false from .env.example), so the cookie is
// `classora_session` without Secure. The API is called with fetch() from the
// page, through the web origin (Vite proxy), so Chromium's own cookie handling
// stores the cookie and sends it back (page.request keeps its own cookies).
// Unsafe requests carry the CSRF token from the login response (#24); the
// browser's own Origin passes through the proxy unchanged. The second part
// signs in and out through the login page (#28 H3).
//
// Every test here logs in or out, so every test runs in a fresh context
// WITHOUT the shared signed-in state (#28 H2): a login sent with the shared
// cookie would revoke the shared session (#23 rotation), and a logout would
// end it.
//
// Login budget (#27, see LOGIN_ATTEMPTS_PER_RUN in support/auth.ts): this
// file makes 8 login attempts per run, 2 of them with a wrong password.
test.use({ storageState: SIGNED_OUT_STATE });

const COOKIE = 'classora_session';
const CSRF_HEADER = 'X-CSRF-Token';
// The demo Owner is staff: the cookie lives as long as the absolute timeout.
const STAFF_ABSOLUTE_SECONDS = 12 * 60 * 60;

test.beforeEach(async ({ page }) => {
  // Any page of the web origin, so fetch() runs same-origin.
  await page.goto('/');
  await expect(page.getByRole('main')).toBeVisible();
});

test('login stores an HttpOnly session cookie that is sent back and cleared on logout', async ({
  page,
  context,
}) => {
  // 1. Login.
  const loggedInAt = Date.now() / 1000;
  const login = await api(page, '/api/v1/auth/login', {
    method: 'POST',
    json: demoOwner,
  });
  expect(login.status).toBe(200);
  expect(login.body).toMatchObject({ username: demoOwner.username });
  const token = csrfTokenOf(login);
  expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);

  // 2. Exactly one local session cookie, with the local-mode attributes and
  // Max-Age = the staff absolute timeout (12 hours, #25).
  const cookies = await context.cookies();
  const session = cookies.filter((c) => c.name === COOKIE);
  expect(session).toHaveLength(1);
  expect(session[0]).toMatchObject({
    domain: '127.0.0.1',
    httpOnly: true,
    secure: false,
    sameSite: 'Lax',
    path: '/',
  });
  const expires = session[0]?.expires ?? -1;
  expect(
    Math.abs(expires - (loggedInAt + STAFF_ABSOLUTE_SECONDS)),
  ).toBeLessThanOrEqual(120);
  expect(cookies.some((c) => c.name === `__Host-${COOKIE}`)).toBe(false);

  // 3. HttpOnly: invisible to the page's scripts.
  expect(await page.evaluate(() => document.cookie)).not.toContain(COOKIE);

  // 4. The browser sends the cookie back; /auth/me returns the same token.
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  expect((me.body as { roles: string[] }).roles).toContain('owner');
  expect(csrfTokenOf(me)).toBe(token);

  // 5. Logout (with the token) clears it and ends the session.
  const logout = await api(page, '/api/v1/auth/logout', {
    method: 'POST',
    headers: { [CSRF_HEADER]: token },
  });
  expect(logout.status).toBe(204);
  expect((await context.cookies()).some((c) => c.name === COOKIE)).toBe(false);
  const after = await api(page, '/api/v1/auth/me');
  expect(after.status).toBe(401);
  expect(after.body).toMatchObject({ code: 'authentication_required' });
});

test('an unsafe request without the CSRF token is csrf_failed and keeps the session', async ({
  page,
}) => {
  const login = await api(page, '/api/v1/auth/login', {
    method: 'POST',
    json: demoOwner,
  });
  expect(login.status).toBe(200);

  // Same-origin fetch: Chromium sends Origin, which the API accepts (no
  // Origin failure), so the 403 is the missing token.
  const logout = await api(page, '/api/v1/auth/logout', { method: 'POST' });
  expect(logout.status).toBe(403);
  expect(logout.body).toMatchObject({ code: 'csrf_failed' });

  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  expect(csrfTokenOf(me)).toBe(csrfTokenOf(login));
});

test('a wrong password is invalid_credentials and stores no cookie', async ({
  page,
  context,
}) => {
  const login = await api(page, '/api/v1/auth/login', {
    method: 'POST',
    json: { username: demoOwner.username, password: 'wrong-password-1' },
  });

  expect(login.status).toBe(401);
  expect(login.body).toMatchObject({ code: 'invalid_credentials' });
  const cookies = await context.cookies();
  expect(cookies.filter((c) => c.name.endsWith(COOKIE))).toEqual([]);
});

// --- The login page (#28 H3) ---------------------------------------------------

const owner = common.auth;

async function signInThroughTheForm(
  page: Page,
  password: string = demoOwner.password,
) {
  await page
    .getByLabel(owner.login.username, { exact: true })
    .fill(demoOwner.username);
  await page.getByLabel(owner.login.password, { exact: true }).fill(password);
  await page.getByRole('button', { name: owner.login.submit }).click();
}

test.describe('the login page', () => {
  // The file's beforeEach opened "/" (signed out: the login page); each test
  // then opens its own page.
  test('signs in and returns to the page that asked for it', async ({
    page,
  }) => {
    await recordCspViolations(page);
    const consoleErrors: string[] = [];
    page.on('pageerror', (error) => consoleErrors.push(error.message));
    page.on('console', (message) => {
      // The signed-out bootstrap's GET /auth/me answers 401 on purpose;
      // Chromium logs every 4xx resource as a console error.
      if (
        message.type() === 'error' &&
        !/Failed to load resource: the server responded with a status of 401/.test(
          message.text(),
        )
      ) {
        consoleErrors.push(message.text());
      }
    });

    await page.goto('/e2e-return-path?x=1');
    await expect(page).toHaveURL(
      `/login?next=${encodeURIComponent('/e2e-return-path?x=1')}`,
    );
    await signInThroughTheForm(page);

    await expect(page).toHaveURL('/e2e-return-path?x=1');
    // The 404 page inside the layout proves the path and query came back.
    await expect(
      page
        .getByRole('main')
        .getByRole('heading', { level: 1, name: common.notFound.title }),
    ).toBeVisible();
    const header = page.getByRole('banner');
    await expect(
      header.getByText(signedInAs(common, 'Demo Owner')),
    ).toBeVisible();
    await expect(
      header.getByRole('button', { name: owner.userMenu.signOut }),
    ).toBeVisible();
    await expect(
      header
        .getByRole('navigation')
        .getByRole('link', { name: common.nav.home }),
    ).toBeVisible();
    expect(await cspViolations(page)).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });

  test('a wrong password shows the error and stores no cookie', async ({
    page,
    context,
  }) => {
    await page.goto('/login');
    await signInThroughTheForm(page, 'wrong-password-2');

    await expect(page.getByRole('alert')).toContainText(
      errors.invalid_credentials,
    );
    await expect(page).toHaveURL('/login');
    const password = page.getByLabel(owner.login.password, { exact: true });
    await expect(password).toHaveValue('');
    await expect(password).toBeFocused();
    const cookies = await context.cookies();
    expect(cookies.filter((c) => c.name.endsWith(COOKIE))).toEqual([]);
  });

  test('sign out ends the session; Back still shows the login page', async ({
    page,
    context,
  }) => {
    await page.goto('/login');
    await signInThroughTheForm(page);
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('status')).toHaveText(common.apiStatus.live);
    // A second history entry under the guard.
    await page.goto('/e2e-after-login');
    await expect(
      page.getByRole('heading', { level: 1, name: common.notFound.title }),
    ).toBeVisible();

    await page.getByRole('button', { name: owner.userMenu.signOut }).click();

    await expect(page).toHaveURL('/login');
    await expect(
      page.getByRole('heading', { level: 1, name: owner.login.title }),
    ).toBeVisible();
    expect((await context.cookies()).some((c) => c.name === COOKIE)).toBe(
      false,
    );
    await page.goBack();
    await expect(
      page.getByRole('heading', { level: 1, name: owner.login.title }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
    const me = await api(page, '/api/v1/auth/me');
    expect(me.status).toBe(401);
  });

  test('in English, the login page uses the en catalog and signs in', async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('classora.language', 'en');
    });
    await page.goto('/login');

    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(
      page.getByRole('heading', { level: 1, name: commonEn.auth.login.title }),
    ).toBeVisible();
    await page
      .getByLabel(commonEn.auth.login.username, { exact: true })
      .fill(demoOwner.username);
    await page
      .getByLabel(commonEn.auth.login.password, { exact: true })
      .fill(demoOwner.password);
    await page
      .getByRole('button', { name: commonEn.auth.login.submit })
      .click();

    await expect(page).toHaveURL('/');
    await expect(
      page.getByText(signedInAs(commonEn, 'Demo Owner')),
    ).toBeVisible();
  });
});
