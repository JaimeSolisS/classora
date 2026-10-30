import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common } from '../support/catalog';
import { demoOwner } from '../support/credentials';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// Corrections, reversals and refunds (#386 F3). The demo Owner signs in in
// a fresh context (1 login, no redemption, no registration): the password
// dialog of a correction reauthenticates the session it runs in, and the
// shared session is never reauthenticated (_docs/testing-guidelines.md,
// #28 H2, #29 L1; settings.spec.ts relies on it). Through the API, with
// per-run unique names: a student in a new family (no other spec sees its
// account). Then on the family's account page:
//   1. "Add charge" posts a manual charge (no password);
//   2. its detail → "Adjust" → a credit with a reason → the password
//      dialog (demo Owner password) → the net is lower and the reason is
//      in the detail;
//   3. a payment recorded through the API pays the charge; "Reverse
//      payment" (inside the 5-minute reauthentication window: no second
//      password) → "Reversed", the charge open again;
//   4. a second payment kept as credit; "Refund" part of it → the refund
//      is listed and the account credit is lower.
// The rows stay in the shared E2E database; billing stays off (#366).

const billing = common.billing;
const accounts = billing.accounts;
const ZONE = 'America/Mexico_City';
const TEST_TIMEOUT_MS = 90_000;

// Chromium logs every 4xx resource as a console error: the expected 403
// reauth_required before the password dialog.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 403/;

test.use({ storageState: SIGNED_OUT_STATE });

/** Today's date in the center's zone (YYYY-MM-DD). */
function centerToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(
    new Date(),
  );
}

/** "$1,234.50", as the app formats money (es-MX in both languages). */
function money(cents: number): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
  }).format(cents / 100);
}

async function csrf(page: Page): Promise<string> {
  return csrfTokenOf(await api(page, '/api/v1/auth/me'));
}

/** Records a cash payment through the API; returns its id. */
async function recordPaymentViaApi(
  page: Page,
  familyId: string,
  amount: string,
  mode: 'oldest_first' | 'none',
  key: string,
): Promise<string> {
  const recorded = await api(
    page,
    `/api/v1/families/${familyId}/billing-account/payments`,
    {
      method: 'POST',
      json: {
        amount,
        received_on: centerToday(),
        payment_method_code: 'cash',
        allocation: { mode },
      },
      headers: {
        'X-CSRF-Token': await csrf(page),
        'Idempotency-Key': key.padEnd(16, '0'),
      },
    },
  );
  expect(recorded.status).toBe(201);
  return (recorded.body as { payment: { id: string } }).payment.id;
}

test('corrections, reversals and refunds on a family account', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyName = `Familia Correcciones ${suffix}`;
  const description = `Material E2E ${suffix}`;
  const reason = `Captura E2E ${suffix}`;
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

  // 0. The demo Owner signs in in this fresh context (the one login).
  await page.goto('/login');
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(demoOwner.username);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(demoOwner.password);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(page).not.toHaveURL(/\/login/, {
    timeout: FIRST_RENDER_TIMEOUT_MS,
  });

  // The setup, through the API.
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Corregido',
    familyNames: `Correcciones ${suffix}`,
  });
  const familyId = await createFamilyViaApi(page, familyName);
  await addToFamilyViaApi(page, familyId, 'students', studentId);

  await page.goto(`/billing/accounts/${familyId}`);
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: accounts.page.title.replace('{{name}}', familyName),
    }),
  ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });

  // 1. A manual charge of $500.00 (no password).
  await main
    .getByRole('button', { name: accounts.charges.add, exact: true })
    .click();
  let dialog = page.getByRole('dialog', {
    name: accounts.manualCharge.title,
    exact: true,
  });
  await dialog
    .getByLabel(accounts.manualCharge.fields.student, { exact: true })
    .selectOption(studentId);
  await dialog
    .getByLabel(accounts.manualCharge.fields.description, { exact: true })
    .fill(description);
  await dialog
    .getByLabel(accounts.manualCharge.fields.amount, { exact: true })
    .fill('500');
  await dialog
    .getByRole('button', { name: accounts.manualCharge.submit, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const charge = main.locator('[data-charge-id]');
  await expect(charge).toHaveCount(1);
  await expect(charge).toContainText(description);
  await expect(charge).toContainText(money(50_000));

  // 2. Its detail → Adjust → a $120.00 credit → the password dialog.
  await charge
    .getByRole('button', {
      name: new RegExp(`^${accounts.charges.view} `),
    })
    .click();
  const detail = page.getByRole('dialog', {
    name: accounts.chargeDetail.title,
    exact: true,
  });
  await expect(detail.locator('[data-charge-net="500.00"]')).toBeVisible();
  await detail
    .getByRole('button', { name: accounts.adjust.action, exact: true })
    .click();
  dialog = page.getByRole('dialog', {
    name: accounts.adjust.title,
    exact: true,
  });
  await dialog
    .getByLabel(accounts.adjust.fields.amount, { exact: true })
    .fill('120');
  await dialog
    .getByLabel(accounts.adjust.fields.reason, { exact: true })
    .fill(reason);
  await dialog
    .getByRole('button', { name: accounts.adjust.submit, exact: true })
    .click();
  const reauth = page.getByRole('dialog', {
    name: common.auth.reauth.title,
    exact: true,
  });
  await expect(reauth).toBeVisible();
  await reauth
    .getByLabel(common.auth.reauth.password, { exact: true })
    .fill(demoOwner.password);
  await reauth
    .getByRole('button', { name: common.auth.reauth.confirm, exact: true })
    .click();
  await expect(reauth).toHaveCount(0);
  await expect(dialog).toHaveCount(0);
  await expect(detail.locator('[data-charge-net="380.00"]')).toBeVisible();
  await expect(
    detail.locator('[data-charge-adjustment="manual"]'),
  ).toContainText(reason);
  // The footer's "Close" (the corner icon has the same accessible name).
  await detail
    .locator('[data-slot="dialog-footer"]')
    .getByRole('button', { name: accounts.chargeDetail.close, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(charge).toContainText(money(38_000));

  // 3. A payment through the API pays the charge; reverse it in the UI.
  const paidId = await recordPaymentViaApi(
    page,
    familyId,
    '380.00',
    'oldest_first',
    `e2e-corr-a-${suffix}`,
  );
  await page.reload();
  const paidRow = main.locator(`[data-payment-id="${paidId}"]`);
  await expect(paidRow).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await expect(charge).toContainText(billing.tuition.chargeStatus.paid);
  await paidRow
    .getByRole('button', { name: new RegExp(`^${accounts.payments.view} `) })
    .click();
  const payment = page.getByRole('dialog', {
    name: accounts.payments.detailTitle,
    exact: true,
  });
  await payment
    .getByRole('button', { name: accounts.reverse.action, exact: true })
    .click();
  dialog = page.getByRole('dialog', {
    name: accounts.reverse.title,
    exact: true,
  });
  await dialog
    .getByLabel(accounts.reverse.fields.reason, { exact: true })
    .fill(reason);
  await dialog
    .getByRole('button', { name: accounts.reverse.submit, exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(payment.locator('[data-payment-reversal]')).toContainText(
    reason,
  );
  await payment
    .locator('[data-slot="dialog-footer"]')
    .getByRole('button', { name: accounts.payments.close, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(paidRow).toContainText(accounts.payments.reversed);
  await expect(charge).toContainText(billing.tuition.chargeStatus.pending);

  // 4. A second payment kept as credit; refund $400.00 of it.
  const creditId = await recordPaymentViaApi(
    page,
    familyId,
    '1000.00',
    'none',
    `e2e-corr-b-${suffix}`,
  );
  await page.reload();
  const creditRow = main.locator(`[data-payment-id="${creditId}"]`);
  await expect(creditRow).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
  const credit = main
    .locator('dl > div')
    .filter({ hasText: accounts.summary.accountCredit });
  await expect(credit).toContainText(money(100_000));
  await creditRow
    .getByRole('button', { name: new RegExp(`^${accounts.payments.view} `) })
    .click();
  await payment
    .getByRole('button', { name: accounts.refund.action, exact: true })
    .click();
  dialog = page.getByRole('dialog', {
    name: accounts.refund.title,
    exact: true,
  });
  await dialog
    .getByLabel(accounts.refund.fields.amount, { exact: true })
    .fill('400');
  await dialog
    .getByLabel(accounts.refund.fields.method, { exact: true })
    .selectOption('cash');
  await dialog
    .getByLabel(accounts.refund.fields.reason, { exact: true })
    .fill(reason);
  await dialog
    .getByRole('button', { name: accounts.refund.submit, exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(payment.locator('[data-payment-refund]')).toHaveCount(1);
  await expect(payment.locator('[data-payment-refund]')).toContainText(
    money(40_000),
  );
  await payment
    .locator('[data-slot="dialog-footer"]')
    .getByRole('button', { name: accounts.payments.close, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(credit).toContainText(money(60_000));

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
