import { expect, test, type Page } from '@playwright/test';
import { demoOwner } from '../support/credentials';

// The session cookie in Chromium (M2-05). The E2E stack runs in the local
// cookie mode (SESSION_COOKIE_SECURE=false from .env.example), so the cookie is
// `classora_session` without Secure. The API is called with fetch() from the
// page, through the web origin (Vite proxy), so Chromium's own cookie handling
// stores the cookie and sends it back (page.request keeps its own cookies).
// The login screen arrives later; this spec then grows into the UI flow.

const COOKIE = 'classora_session';

type ApiResult = { status: number; body: unknown };

async function api(
  page: Page,
  path: string,
  init: { method?: string; json?: unknown } = {},
): Promise<ApiResult> {
  return page.evaluate(
    async ({ path, init }) => {
      const response = await fetch(path, {
        method: init.method ?? 'GET',
        headers:
          init.json === undefined
            ? undefined
            : { 'Content-Type': 'application/json' },
        body: init.json === undefined ? undefined : JSON.stringify(init.json),
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : null };
    },
    { path, init },
  );
}

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
  const login = await api(page, '/api/v1/auth/login', {
    method: 'POST',
    json: demoOwner,
  });
  expect(login.status).toBe(200);
  expect(login.body).toMatchObject({ username: demoOwner.username });

  // 2. Exactly one local session cookie, with the local-mode attributes.
  const cookies = await context.cookies();
  const session = cookies.filter((c) => c.name === COOKIE);
  expect(session).toHaveLength(1);
  expect(session[0]).toMatchObject({
    domain: '127.0.0.1',
    httpOnly: true,
    secure: false,
    sameSite: 'Lax',
    path: '/',
    expires: -1,
  });
  expect(cookies.some((c) => c.name === `__Host-${COOKIE}`)).toBe(false);

  // 3. HttpOnly: invisible to the page's scripts.
  expect(await page.evaluate(() => document.cookie)).not.toContain(COOKIE);

  // 4. The browser sends the cookie back.
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  expect((me.body as { roles: string[] }).roles).toContain('owner');

  // 5. Logout clears it and ends the session.
  const logout = await api(page, '/api/v1/auth/logout', { method: 'POST' });
  expect(logout.status).toBe(204);
  expect((await context.cookies()).some((c) => c.name === COOKIE)).toBe(false);
  const after = await api(page, '/api/v1/auth/me');
  expect(after.status).toBe(401);
  expect(after.body).toMatchObject({ code: 'authentication_required' });
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
