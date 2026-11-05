import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// CSV export of expenses (M8-07b E4), on the demo Owner's shared session:
// 0 logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN stays
// 50/50, registration.ip 10/10) and no password step (expenses aren't
// personal; Vitest covers the reauthentication branch). From the nav, the
// Owner requests an expenses CSV, waits for the worker (the page polls every
// 3 s), downloads it and checks the UTF-8 BOM and the header row, which
// must be the column labels the page shows (both come from the API in the
// Owner's stored language). The export's own row is found by the id the
// request returned, so earlier runs' exports never match; nothing named is
// created, so no suffix is needed.
//
// M8-08 adds a saved setup on the same session (still 0 logins, 0
// registrations): the Owner saves an expenses CSV setup under a name with a
// UUID suffix, reloads, runs it from "Configuraciones guardadas", waits for
// the export to be ready and deletes the setup (so reruns stay far below the
// 50-setup limit).

const TEST_TIMEOUT_MS = 120_000;
const READY_TIMEOUT_MS = 60_000;
const labels = common.exports;

test('the Owner exports expenses as CSV and downloads the file', async ({
  page,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await recordCspViolations(page);
  const main = page.getByRole('main');

  // 1. From the nav to the exports page.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.exports, exact: true })
    .click({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await expect(page).toHaveURL('/exports');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();

  // 2. Expenses, CSV, every column: request it.
  await main
    .getByRole('radio', { name: labels.datasets.expenses, exact: true })
    .check();
  await expect(
    main.getByRole('radio', { name: labels.formats.csv, exact: true }),
  ).toBeChecked();
  const columns = main.locator('li[data-column] label');
  await expect(columns.first()).toBeVisible();
  const headers = (await columns.allTextContents()).map((text) => text.trim());
  expect(headers.length).toBeGreaterThan(0);

  const created = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/exports',
  );
  await main
    .getByRole('button', { name: labels.form.submit, exact: true })
    .click();
  const response = await created;
  expect(response.status()).toBe(202);
  const job = (await response.json()) as { id: string; file_name: string };
  await expect(main.locator('p[aria-live="polite"]').first()).toHaveText(
    fill(labels.form.requested, { dataset: labels.datasets.expenses }),
  );

  // 3. The worker finishes it: the row becomes ready with a link.
  const row = main.locator(`tr[data-export-id="${job.id}"]`);
  await expect(row).toHaveAttribute('data-export-status', 'ready', {
    timeout: READY_TIMEOUT_MS,
  });
  await expect(row).toContainText(labels.status.ready);
  const link = row.getByRole('link', {
    name: fill(labels.history.downloadFor, { file: job.file_name }),
    exact: true,
  });

  // 4. Download: a UTF-8 BOM, then the header row in the page's labels.
  const downloading = page.waitForEvent('download');
  await link.click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(job.file_name);
  const bytes = readFileSync(await download.path());
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const text = bytes.subarray(3).toString('utf8');
  const [header] = text.split('\r\n');
  expect(header).toBe(headers.join(','));

  expect(await cspViolations(page)).toEqual([]);
});

test('the Owner saves an expenses setup, runs it after a reload and deletes it', async ({
  page,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await recordCspViolations(page);
  const main = page.getByRole('main');
  const name = `E2E gastos ${randomUUID().replaceAll('-', '').slice(0, 12)}`;

  // 1. Expenses, CSV: save the setup.
  await page.goto('/exports');
  await main
    .getByRole('radio', { name: labels.datasets.expenses, exact: true })
    .check({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await main
    .getByRole('button', { name: labels.setups.save, exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByRole('textbox', { name: labels.setups.name, exact: true })
    .fill(name);
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/report-setups',
  );
  await dialog
    .getByRole('button', { name: labels.setups.saveNew, exact: true })
    .click();
  const saveResponse = await saved;
  expect(saveResponse.status()).toBe(201);
  const setup = (await saveResponse.json()) as {
    id: string;
    parameters: unknown;
  };
  expect(setup.parameters).toEqual({
    dataset: 'expenses',
    format: 'csv',
    filters: {},
  });
  await expect(main.locator('p[aria-live="polite"]').first()).toHaveText(
    fill(labels.setups.created, { name }),
  );

  // 2. Reload: the setup is listed; run it.
  await page.reload();
  const item = main.locator(`li[data-setup-id="${setup.id}"]`);
  await expect(item).toContainText(name, { timeout: FIRST_RENDER_TIMEOUT_MS });
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/exports',
  );
  await item
    .getByRole('button', { name: fill(labels.setups.runFor, { name }) })
    .click();
  const response = await created;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON()).toEqual(setup.parameters);
  const job = (await response.json()) as { id: string };
  await expect(main.locator(`tr[data-export-id="${job.id}"]`)).toHaveAttribute(
    'data-export-status',
    'ready',
    { timeout: READY_TIMEOUT_MS },
  );

  // 3. Delete it (confirmed).
  await item
    .getByRole('button', { name: fill(labels.setups.deleteFor, { name }) })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: labels.setups.delete, exact: true })
    .click();
  await expect(item).toHaveCount(0);
  await expect(main.locator('p[aria-live="polite"]').first()).toHaveText(
    fill(labels.setups.deleted, { name }),
  );

  expect(await cspViolations(page)).toEqual([]);
});
