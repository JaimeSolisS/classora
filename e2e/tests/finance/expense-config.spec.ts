import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';

// Expense categories and vendors (M7-11b F3), on the demo Owner's shared
// session: 0 logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN
// stays 50/50). Nothing can be deleted (#114 E5), so each run's names carry
// their own suffix. Administrators and Finance Staff have no E2E session:
// tests/authz/matrix.toml and Vitest cover them.

const finance = common.finance;

test('the Owner adds, renames, deactivates and reactivates a category', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Categoría E2E ${suffix}`;
  const renamed = `Categoría renombrada ${suffix}`;
  const labels = finance.categories;
  const main = page.getByRole('main');
  const status = main.getByRole('status');

  // The Owner's /expenses opens the expense list (M7-12b); categories are
  // the second tab.
  await page.goto('/expenses/categories');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();
  await expect(
    main
      .getByRole('navigation', { name: finance.tabs.label })
      .getByRole('link', { name: finance.tabs.categories }),
  ).toHaveAttribute('aria-current', 'page');

  // 1. Add.
  await main.getByRole('button', { name: labels.add, exact: true }).click();
  const add = page.getByRole('dialog', { name: labels.dialog.addTitle });
  await add.getByLabel(labels.dialog.name, { exact: true }).fill(name);
  await expectNoAxeViolations(page);
  await add.getByRole('button', { name: finance.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toHaveText(labels.saved);
  const table = main.locator('[data-categories-table]');
  await expect(table.getByRole('row', { name })).toBeVisible();

  // 2. Rename.
  await table
    .getByRole('button', { name: fill(labels.renameFor, { name }) })
    .click();
  const rename = page.getByRole('dialog', { name: labels.dialog.renameTitle });
  const field = rename.getByLabel(labels.dialog.name, { exact: true });
  await expect(field).toHaveValue(name);
  await field.fill(renamed);
  await rename.getByRole('button', { name: finance.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const row = table.getByRole('row', { name: renamed });
  await expect(row).toBeVisible();

  // 3. Deactivate, behind the confirmation.
  await row
    .getByRole('button', {
      name: fill(labels.deactivateFor, { name: renamed }),
    })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: labels.deactivate, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toHaveText(labels.deactivated);
  await expect(row).toContainText(labels.states.inactive);

  // 4. Reactivate.
  await row
    .getByRole('button', {
      name: fill(labels.reactivateFor, { name: renamed }),
    })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: labels.reactivate, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toHaveText(labels.reactivated);
  await expect(row).toContainText(labels.states.active);
  await expectNoAxeViolations(page);
  expect(await cspViolations(page)).toEqual([]);
});

test('the Owner adds a vendor, finds it by search and deactivates it', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Proveedor E2E ${suffix}`;
  const labels = finance.vendors;
  const main = page.getByRole('main');

  await page.goto('/expenses/categories');
  await main
    .getByRole('navigation', { name: finance.tabs.label })
    .getByRole('link', { name: finance.tabs.vendors })
    .click();
  await expect(page).toHaveURL('/expenses/vendors');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();

  // 1. Add, with a two-line contact.
  await main
    .getByRole('button', { name: labels.add, exact: true })
    .first()
    .click();
  const add = page.getByRole('dialog', { name: labels.dialog.addTitle });
  await add.getByLabel(labels.dialog.name, { exact: true }).fill(name);
  await add
    .getByLabel(labels.dialog.contact, { exact: true })
    .fill('compras@proveedor.example\n55 0000 0000');
  await expectNoAxeViolations(page);
  await add.getByRole('button', { name: finance.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toHaveText(labels.saved);

  // 2. Find it: the search goes to the URL.
  await main.getByLabel(labels.search, { exact: true }).fill(suffix);
  await expect(page).toHaveURL(new RegExp(`[?&]q=${suffix}`));
  const table = main.locator('[data-vendors-table]');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  const row = table.getByRole('row', { name: new RegExp(name) });
  await expect(row).toContainText('compras@proveedor.example');

  // 3. Deactivate it.
  await row
    .getByRole('button', { name: fill(labels.deactivateFor, { name }) })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: labels.deactivate, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toHaveText(labels.deactivated);
  await expect(row).toContainText(labels.states.inactive);
  await expectNoAxeViolations(page);
  expect(await cspViolations(page)).toEqual([]);
});
