import { expect, test } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { SIGNED_OUT_STATE } from '../support/auth';
import { demoOwner } from '../support/credentials';

// Security headers in Chromium (#24 H2): the web app runs under the enforced
// development Content Security Policy of apps/web/src/security-headers.ts
// (served by the Vite dev server), API responses through the web origin carry
// the API headers, the app works with zero CSP violations, and it cannot be
// framed. Policy and reasons: _docs/security-headers.md.

declare global {
  interface Window {
    __cspViolations?: string[];
  }
}

test('the app is served with the development CSP, nosniff and DENY', async ({
  request,
}) => {
  const response = await request.get('/');
  const headers = response.headers();
  const csp = headers['content-security-policy'] ?? '';

  expect(response.status()).toBe(200);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).toMatch(/(^|; )script-src 'self' 'sha256-[A-Za-z0-9+/=]+'(;|$)/);
  expect(csp).not.toContain("'unsafe-eval'");
  expect(headers['content-security-policy-report-only']).toBeUndefined();
  expect(headers['x-content-type-options']).toBe('nosniff');
  expect(headers['x-frame-options']).toBe('DENY');
});

test('an API response through the web origin is no-store and nosniff', async ({
  request,
}) => {
  const response = await request.get('/health/live');

  expect(response.status()).toBe(200);
  expect(response.headers()['cache-control']).toBe('no-store');
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
});

test.describe('a sign-in round trip', () => {
  // Logs in and out: a fresh context, never the shared session (#28 H2).
  test.use({ storageState: SIGNED_OUT_STATE });

  test('the app and a sign-in round trip cause no CSP violation', async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener('securitypolicyviolation', (event) => {
        window.__cspViolations?.push(
          `${event.violatedDirective} ${event.blockedURI}`,
        );
      });
    });
    const cspConsoleErrors: string[] = [];
    page.on('console', (message) => {
      if (/content security policy/i.test(message.text())) {
        cspConsoleErrors.push(message.text());
      }
    });

    await page.goto('/');
    await expect(page.getByRole('main')).toBeVisible();
    const login = await api(page, '/api/v1/auth/login', {
      method: 'POST',
      json: demoOwner,
    });
    expect(login.status).toBe(200);
    const me = await api(page, '/api/v1/auth/me');
    expect(me.status).toBe(200);
    const logout = await api(page, '/api/v1/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfTokenOf(login) },
    });
    expect(logout.status).toBe(204);

    expect(await page.evaluate(() => window.__cspViolations)).toEqual([]);
    expect(cspConsoleErrors).toEqual([]);
  });
});

test('a page that embeds the app in an iframe does not render it', async ({
  page,
  baseURL,
}) => {
  const consoleMessages: string[] = [];
  page.on('console', (message) => consoleMessages.push(message.text()));

  await page.setContent(
    `<iframe title="embedded" src="${baseURL}/" onload="window.__framed = true"></iframe>`,
  );
  await page.waitForFunction(() => '__framed' in window);

  await expect(
    page.frameLocator('iframe[title="embedded"]').locator('#root'),
  ).toHaveCount(0);
  // Blocked by the CSP directive, not only by X-Frame-Options.
  expect(consoleMessages.join('\n')).toContain("frame-ancestors 'none'");
});
