import { expect, test } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';

// Packages (#377 G3): the demo Owner's shared session (no login, no
// redemption, no registration). Through the API, with per-run unique
// names: a student in a new family (a dedicated student, so no other spec
// sees this run's purchase charge). Then from Billing → Packages: a
// 10-session product (Products), sold to that student (its page shows
// the "Granted" movement), the sold list with 10 / 10 and Active, and the
// product deactivated. The sale posts one package_purchase charge in the
// shared E2E database; billing stays off (#366), so no session charge is
// posted.

const billing = common.billing;
const packages = billing.packages;

test('selling a session package', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const productName = `Paquete ${suffix}`;
  const familyNames = `Paquete ${suffix}`;
  const studentName = `Comprador ${familyNames}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const main = page.getByRole('main');

  // 1. The setup, through the API (the page on the app origin).
  await page.goto('/');
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Comprador',
    familyNames,
  });
  const familyId = await createFamilyViaApi(page, `Familia ${familyNames}`);
  await addToFamilyViaApi(page, familyId, 'students', studentId);

  // 2. Billing → Packages → Products: a 10-session product.
  await page.goto('/billing/packages');
  await expect(
    main.getByRole('link', { name: billing.tabs.packages, exact: true }),
  ).toHaveAttribute('aria-current', 'page');
  await main
    .getByRole('link', { name: packages.nav.products, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/package-products');
  await main
    .getByRole('button', { name: packages.products.add })
    .first()
    .click();
  let dialog = page.getByRole('dialog', { name: packages.products.add });
  await dialog
    .getByLabel(packages.fields.name, { exact: true })
    .fill(productName);
  await expect(
    dialog.getByRole('radio', { name: packages.unitNames.sessions }),
  ).toBeChecked();
  await dialog
    .getByLabel(packages.fields.quantitySessions, { exact: true })
    .fill('10');
  await dialog.getByLabel(packages.fields.validity, { exact: true }).fill('90');
  await dialog.getByLabel(packages.fields.amount, { exact: true }).fill('2000');
  await dialog.getByRole('button', { name: packages.products.create }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing\/package-products\/[0-9a-f-]{36}$/);
  const productUrl = page.url();
  const productId = productUrl.split('/').at(-1)!;
  await expect(
    main.getByRole('heading', { level: 1, name: productName }),
  ).toBeVisible();

  // 3. Sold → Sell package: the dedicated student, the new product.
  await main
    .getByRole('link', { name: packages.nav.sold, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/packages');
  await main
    .getByRole('button', { name: packages.sell.action })
    .first()
    .click();
  dialog = page.getByRole('dialog', { name: packages.sell.title });
  await dialog
    .getByLabel(packages.fields.student, { exact: true })
    .fill(studentName);
  await dialog.getByRole('radio', { name: studentName, exact: true }).check();
  await dialog
    .getByLabel(packages.fields.product, { exact: true })
    .selectOption(productId);
  await dialog.getByRole('button', { name: packages.sell.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 4. The package page: the "Granted" movement.
  await expect(page).toHaveURL(/\/billing\/packages\/[0-9a-f-]{36}$/);
  await expect(
    main.locator('[data-package-transaction="grant"]'),
  ).toContainText(packages.history.kinds.grant);

  // 5. The sold list (this student's only), 10 / 10 and Active.
  await page.goto(`/billing/packages?student_id=${studentId}`);
  const row = main.getByRole('row').filter({ hasText: studentName });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(
    packages.units.sessionsBalance_other
      .replace('{{remaining}}', '10')
      .replace('{{count}}', '10'),
  );
  await expect(row).toContainText(packages.status.active);
  await expect(row).toContainText(productName);

  // 6. Deactivate the product.
  await page.goto(productUrl);
  await main.getByRole('button', { name: packages.product.deactivate }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: packages.product.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.getByRole('button', { name: packages.product.activate }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
