import { expect, test } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { createDemoNotificationViaApi } from '../support/notifications';

// Live notifications (#42 F1, F2): with the demo Owner's shared session, the
// bell's stream opens (data-live="open"); a notification created through the
// local/DEV route then shows in the already open panel within 5 s, without a
// reload. The list has no polling at all and the count polls every 30 s, so
// only the stream explains it. The stream goes through the Vite dev proxy
// (connect-src 'self'): no CSP violation, no console error. Assertions use
// this run's unique label, never counts.

const LIVE_TIMEOUT_MS = 5_000;

test('a new notification arrives live in the open panel', async ({
  page,
}, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const label = `Live ${suffix}`;
  const message = fill(common.notifications.types.dev_demo, { label });
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (entry) => {
    if (entry.type() === 'error') {
      consoleErrors.push(entry.text());
    }
  });

  // 1. The stream is open.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const bellSlot = page
    .getByRole('banner')
    .locator('[data-slot="notification-bell"]');
  await expect(bellSlot).toHaveAttribute('data-live', 'open');

  // 2. The panel is open and doesn't show the new label yet.
  await bellSlot.getByRole('button').click();
  const panel = page.getByRole('dialog', { name: common.notifications.title });
  await expect(panel).toBeVisible();
  const item = panel.getByRole('button').filter({ hasText: message });
  await expect(item).toHaveCount(0);

  // 3. Created through the API, it shows without a reload.
  await createDemoNotificationViaApi(page, label);
  await expect(item).toHaveCount(1, { timeout: LIVE_TIMEOUT_MS });
  await expect(item).toContainText(common.notifications.unread);
  await expect(bellSlot).toHaveAttribute('data-live', 'open');

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
