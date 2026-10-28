import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, errors } from '../support/catalog';
import { createSubjectViaApi } from '../support/tutors';

// The price catalog (#363 F4): the demo Owner's shared session (no login,
// no redemption, no registration) adds a subject through the API with a
// unique per-run name, then from Billing → Prices adds an individual,
// per-session, 60-minute price for it (any level, from today, 350.00),
// schedules a change one week ahead (380.00), sees the current price and
// the next change on the list, deletes the upcoming change and meets
// service_price_exists (with its link) when it adds the same key again.

const prices = common.billing.prices;

// Chromium logs every 4xx resource as a console error: the one expected
// 409 answer (service_price_exists).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

/** `YYYY-MM-DD` plus `days` (calendar arithmetic, no zone). */
function addDays(date: string, days: number): string {
  const [year, month, day] = date.split('-').map(Number) as [
    number,
    number,
    number,
  ];
  return new Date(Date.UTC(year, month - 1, day + days))
    .toISOString()
    .slice(0, 10);
}

function versionRow(page: Page, status: string) {
  return page
    .getByRole('main')
    .getByRole('table', { name: prices.versions.title })
    .getByRole('row')
    .filter({ has: page.getByText(status, { exact: true }) });
}

/** Opens "Add price" and fills the individual, per-session price. */
async function fillAddDialog(page: Page, subjectName: string) {
  await page
    .getByRole('main')
    .getByRole('button', { name: prices.add })
    .first()
    .click();
  const dialog = page.getByRole('dialog', { name: prices.add });
  await dialog
    .getByLabel(prices.fields.classType, { exact: true })
    .selectOption('individual');
  await dialog
    .getByLabel(prices.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await dialog.getByLabel(prices.fields.duration, { exact: true }).fill('60');
  await dialog.getByLabel(prices.fields.amount, { exact: true }).fill('350.00');
  return dialog;
}

test('managing the price catalog', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const subjectName = `Precios ${suffix}`;
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
  const main = page.getByRole('main');

  // 1. A subject of its own, through the API (the page on the app origin).
  await page.goto('/');
  await createSubjectViaApi(page, 'Secundaria', subjectName);

  // 2. Billing → Prices from the navigation.
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.billing })
    .click();
  await expect(page).toHaveURL('/billing/prices');
  await expect(
    main.getByRole('heading', { level: 1, name: prices.title }),
  ).toBeVisible();

  // 3. Add the price: the item's page shows it in effect.
  let dialog = await fillAddDialog(page, subjectName);
  const today = await dialog
    .getByLabel(prices.fields.effectiveFrom, { exact: true })
    .inputValue();
  expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  await dialog.getByRole('button', { name: prices.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing\/prices\/[0-9a-f-]{36}$/);
  const itemUrl = page.url();
  await expect(versionRow(page, prices.status.current)).toContainText(
    '$350.00',
  );

  // 4. Schedule a change one week ahead: Upcoming.
  await main.getByRole('button', { name: prices.schedule.title }).click();
  dialog = page.getByRole('dialog', { name: prices.schedule.title });
  await dialog
    .getByLabel(prices.fields.effectiveFrom, { exact: true })
    .fill(addDays(today, 7));
  await dialog.getByLabel(prices.fields.amount, { exact: true }).fill('380.00');
  await dialog.getByRole('button', { name: prices.schedule.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(versionRow(page, prices.status.upcoming)).toContainText(
    '$380.00',
  );

  // 5. Back to the list: the current price and the next change.
  await main.getByRole('link', { name: prices.back }).click();
  await expect(page).toHaveURL('/billing/prices');
  const listRow = main.getByRole('row').filter({ hasText: subjectName });
  await expect(listRow).toContainText('$350.00');
  await expect(listRow).toContainText('$380.00');

  // 6. Delete the upcoming change: it is gone.
  await page.goto(itemUrl);
  await versionRow(page, prices.status.upcoming).getByRole('button').click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: prices.deleteDialog.confirm })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(versionRow(page, prices.status.upcoming)).toHaveCount(0);
  await expect(versionRow(page, prices.status.current)).toContainText(
    '$350.00',
  );

  // 7. The same key again: service_price_exists, linking to the item.
  await main.getByRole('link', { name: prices.back }).click();
  dialog = await fillAddDialog(page, subjectName);
  await dialog.getByRole('button', { name: prices.save }).click();
  await expect(dialog.getByRole('alert')).toContainText(
    errors.service_price_exists,
  );
  await expect(
    dialog.getByRole('link', { name: prices.viewExisting }),
  ).toHaveAttribute('href', new URL(itemUrl).pathname);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
