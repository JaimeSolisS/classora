import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';

// Family accounts and payments (#379 G3): the demo Owner's shared session
// (no login, no redemption, no registration). Through the API, with
// per-run unique names: a student in a new family, and one charge for it
// (a package sold to the student, the #377 path). Then from Billing →
// Accounts: find the family, record a payment larger than the charge
// "Oldest first" (the charge is then Paid, the difference is account
// credit, the payment and its allocation are in the history), record a
// second payment kept as credit, and "Apply account credit" is offered.
// This posts a charge, two payments and an allocation in the shared E2E
// database on a family no other spec uses; billing stays off (#366).

const billing = common.billing;
const accounts = billing.accounts;
const ZONE = 'America/Mexico_City';

/** Today's date in the center's zone (YYYY-MM-DD). */
function centerToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(
    new Date(),
  );
}

/** Whole cents of an API amount ("1800.00" → 180000). */
function cents(amount: string): number {
  const [whole, fraction = ''] = amount.split('.');
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
}

function amountOf(value: number): string {
  return `${Math.trunc(value / 100)}.${String(value % 100).padStart(2, '0')}`;
}

/** "$1,234.50", as the app formats money (es-MX in both languages). */
function money(value: number): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
  }).format(value / 100);
}

async function csrf(page: Page): Promise<string> {
  return csrfTokenOf(await api(page, '/api/v1/auth/me'));
}

/** Sells a new 5-session package to the student; returns the charge's net. */
async function chargeStudentViaApi(
  page: Page,
  familyId: string,
  studentId: string,
  suffix: string,
): Promise<number> {
  const product = await api(page, '/api/v1/billing/package-products', {
    method: 'POST',
    json: {
      name: `Cobro ${suffix}`,
      unit: 'sessions',
      quantity: 5,
      validity_days: 30,
      amount: '1000.00',
      effective_from: centerToday(),
    },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(product.status).toBe(201);
  const sold = await api(page, '/api/v1/billing/packages', {
    method: 'POST',
    json: {
      student_id: studentId,
      package_product_id: (product.body as { id: string }).id,
    },
    headers: {
      'X-CSRF-Token': await csrf(page),
      'Idempotency-Key': `e2e-payments-${suffix}`.padEnd(16, '0'),
    },
  });
  expect(sold.status).toBe(201);
  // The net after any discount the shared database applies.
  const charges = await api(
    page,
    `/api/v1/families/${familyId}/billing-account/charges`,
  );
  expect(charges.status).toBe(200);
  const items = (charges.body as { items: { net: string }[] }).items;
  expect(items).toHaveLength(1);
  return cents(items[0]!.net);
}

test('recording payments on a family account', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyName = `Familia Pagos ${suffix}`;
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
    givenNames: 'Pagador',
    familyNames: `Pagos ${suffix}`,
  });
  const familyId = await createFamilyViaApi(page, familyName);
  await addToFamilyViaApi(page, familyId, 'students', studentId);
  const net = await chargeStudentViaApi(page, familyId, studentId, suffix);
  const extra = 15_000;

  // 2. Billing opens Accounts; find the family.
  await page.goto('/billing');
  await expect(page).toHaveURL('/billing/accounts');
  await expect(
    main.getByRole('link', { name: billing.tabs.accounts, exact: true }),
  ).toHaveAttribute('aria-current', 'page');
  await main
    .getByLabel(accounts.list.search, { exact: true })
    .fill(`Pagos ${suffix}`);
  await main.getByRole('link', { name: familyName, exact: true }).click();
  await expect(page).toHaveURL(`/billing/accounts/${familyId}`);
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: accounts.page.title.replace('{{name}}', familyName),
    }),
  ).toBeVisible();

  // 3. A payment larger than the charge, oldest first (the default).
  await main.getByRole('button', { name: accounts.record.action }).click();
  let dialog = page.getByRole('dialog', { name: accounts.record.title });
  await dialog
    .getByLabel(accounts.record.fields.amount, { exact: true })
    .fill(amountOf(net + extra));
  await dialog
    .getByLabel(accounts.record.fields.method, { exact: true })
    .selectOption('cash');
  await expect(
    dialog.getByRole('radio', { name: accounts.record.modes.oldest_first }),
  ).toBeChecked();
  await dialog.getByRole('button', { name: accounts.record.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toContainText(money(net + extra));

  // 4. The charge is paid, the difference is account credit, and the
  // payment and its allocation are in the history.
  const charges = main.locator('[data-charge-id]');
  await expect(charges).toHaveCount(1);
  await expect(charges).toContainText(billing.tuition.chargeStatus.paid);
  const credit = main
    .locator('dl > div')
    .filter({ hasText: accounts.summary.accountCredit });
  await expect(credit).toContainText(money(extra));
  await expect(main.locator('[data-ledger-entry="payment"]')).toHaveCount(1);
  await expect(main.locator('[data-ledger-entry="allocation"]')).toHaveCount(1);
  await expect(main.locator('[data-payment-id]')).toHaveCount(1);

  // 5. A second payment kept as credit; applying credit is offered.
  await main.getByRole('button', { name: accounts.record.action }).click();
  dialog = page.getByRole('dialog', { name: accounts.record.title });
  await dialog
    .getByLabel(accounts.record.fields.amount, { exact: true })
    .fill('50');
  await dialog
    .getByLabel(accounts.record.fields.method, { exact: true })
    .selectOption('transfer');
  await dialog.getByRole('radio', { name: accounts.record.modes.none }).check();
  await dialog.getByRole('button', { name: accounts.record.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.locator('[data-payment-id]')).toHaveCount(2);
  await expect(credit).toContainText(money(extra + 5_000));
  await expect(
    main.getByRole('button', { name: accounts.credit.action }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
