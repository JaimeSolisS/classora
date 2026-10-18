import { expect, test } from '@playwright/test';
import { common } from './support/catalog';

test('the app shell loads without errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(`console.error: ${message.text()}`);
    }
  });

  await page.goto('/');

  await expect(page.getByRole('main')).toBeVisible();
  await expect(page).toHaveTitle('Classora');
  // The home page's own API call has finished (and succeeded).
  await expect(page.getByRole('status')).toHaveText(common.apiStatus.live);
  expect(errors).toEqual([]);
});

test('an unknown path shows the 404 page (client-side routing)', async ({
  page,
}) => {
  await page.goto('/e2e-route-that-does-not-exist');

  await expect(
    page
      .getByRole('main')
      .getByRole('heading', { level: 1, name: common.notFound.title }),
  ).toBeVisible();
});

// Through the web origin (the Vite proxy), so a pass proves web → API, and
// for readiness API → PgBouncer → PostgreSQL in the seeded stack.
for (const path of ['/health/live', '/health/ready']) {
  test(`GET ${path} through the web origin returns 200`, async ({
    request,
  }) => {
    const response = await request.get(path);

    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({ status: 'ok' });
  });
}
// H1 throwaway: no-op comment
