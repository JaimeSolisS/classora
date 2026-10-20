import { expect, test } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { createDemoNotificationViaApi } from '../support/notifications';

// Notifications (#41 F1): the demo Owner's shared session creates a dev.demo
// notification through the local/DEV route, sees the bell with unread ones
// after a reload, finds the item (unread) in the panel and activates it
// (read). Other specs notify demo.owner too: the assertions use this run's
// unique label, never exact counts.

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

test('a notification shows in the bell and becomes read', async ({
  page,
}, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const label = `E2E ${suffix}`;
  const message = fill(common.notifications.types.dev_demo, { label });
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });

  // 1. On the app origin, create the notification through the API.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createDemoNotificationViaApi(page, label);

  // 2. After a reload, the bell shows unread ones (at least this one).
  await page.reload();
  const [before, after] = common.notifications.bellUnread.split('{{unread}}');
  const bell = page.getByRole('banner').getByRole('button', {
    name: new RegExp(
      `^${escape(before!)}(?:[1-9][0-9]?|99\\+)${escape(after!)}$`,
    ),
  });
  await expect(bell).toBeVisible();

  // 3. The panel lists it, marked unread (in text).
  await bell.click();
  const panel = page.getByRole('dialog', { name: common.notifications.title });
  await expect(panel).toBeVisible();
  const item = panel.getByRole('button').filter({ hasText: message });
  await expect(item).toHaveCount(1);
  await expect(item).toContainText(common.notifications.unread);

  // 4. Activating it marks it read.
  await item.click();
  await expect(item).not.toContainText(common.notifications.unread);
  await expect(item).toContainText(common.notifications.read);

  // 5. Escape closes the panel and returns focus to the bell.
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(
    page.getByRole('banner').getByRole('button', {
      name: new RegExp(`^${escape(common.notifications.bell)}`),
    }),
  ).toBeFocused();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
