import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// Recurring expense templates (M7-13b H3), on the demo Owner's shared
// session: 0 logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN
// stays 50/50) and no password step. A template can't be deleted (#116
// E10), so each run's name carries its own suffix, and its first period is
// in the future so the run never depends on the worker. Generation is
// covered by #116's integration tests.

const finance = common.finance;
const labels = finance.templates;
const DAY_MS = 24 * 60 * 60 * 1000;

/** The text before an interpolation, e.g. "…empieza el {{date}}." */
const prefix = (text: string) => text.split('{{')[0]!;

test('the Owner sets up a monthly recurring expense, pauses and resumes it', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Renta E2E ${suffix}`;
  // 40 days ahead: inside the 366-day window, after the center's today in
  // any zone.
  const startsOn = new Date(Date.now() + 40 * DAY_MS)
    .toISOString()
    .slice(0, 10);
  const main = page.getByRole('main');
  const status = main.locator('[data-template-announcement]');
  const statusField = main.locator('[data-template-field="status"] dd');

  // The second tab of Expenses.
  await page.goto('/expenses/templates');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();
  await expect(
    main
      .getByRole('navigation', { name: finance.tabs.label })
      .getByRole('link', { name: finance.tabs.templates }),
  ).toHaveAttribute('aria-current', 'page');

  // 1. A monthly template starting in the future.
  await main
    .getByRole('button', { name: labels.add, exact: true })
    .first()
    .click();
  const create = page.getByRole('dialog', { name: labels.dialog.createTitle });
  await create.getByLabel(labels.dialog.name, { exact: true }).fill(name);
  await create
    .getByLabel(labels.dialog.category, { exact: true })
    .selectOption({ index: 1 });
  await create.getByLabel(labels.dialog.amount, { exact: true }).fill('8500');
  await create
    .getByLabel(labels.dialog.startsOn, { exact: true })
    .fill(startsOn);
  await expectNoAxeViolations(page);
  await create
    .getByRole('button', { name: labels.dialog.submitCreate, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/expenses\/templates\/[0-9a-f-]{36}$/);
  await expect(main.getByRole('heading', { level: 1, name })).toBeVisible();
  await expect(status).toHaveText(labels.detail.created);
  await expect(statusField).toHaveText(labels.statuses.active);
  await expectNoAxeViolations(page);

  // 2. Pause it.
  await main
    .getByRole('button', { name: labels.detail.actions.pause, exact: true })
    .click();
  const pause = page.getByRole('dialog');
  await pause
    .getByRole('button', { name: labels.pause.confirm, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toHaveText(labels.detail.paused);
  await expect(statusField).toHaveText(labels.statuses.paused);

  // 3. Resume it: the answer's next period is announced.
  await main
    .getByRole('button', { name: labels.detail.actions.resume, exact: true })
    .click();
  const resume = page.getByRole('dialog');
  await resume
    .getByRole('button', { name: labels.resume.confirm, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toContainText(prefix(labels.detail.resumed));
  await expect(statusField).toHaveText(labels.statuses.active);

  // 4. The list shows it active, with its next period.
  await page.goto('/expenses/templates');
  const row = main
    .locator('[data-templates-table] tbody tr')
    .filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(labels.statuses.active);
  await expect(row.locator('td').nth(5)).not.toHaveText('—');
  await expectNoAxeViolations(page);
  expect(await cspViolations(page)).toEqual([]);
});
