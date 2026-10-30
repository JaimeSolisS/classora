import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';

// Due dates and overdue accounts (#382 F3): the demo Owner's shared session
// (no login, no redemption, no registration). Through the API, with
// per-run unique names: a student in a new family and one charge for it (a
// package sold to the student, the #377/#379 path). Then on the family's
// account: change the charge's due date to a past date with a reason (the
// charge becomes overdue and shows the "changed" marker); on Billing →
// Overdue, find the family (91+ days) and open its account from there;
// finally set the family's due-date offset to 5. This posts a charge, one
// due-date change and an offset on a family no other spec uses; billing
// stays off (#366), so no reminder is sent.

const billing = common.billing;
const dueDates = billing.dueDates;
const ZONE = 'America/Mexico_City';
/** A past due date: the family is then among the longest overdue. */
const PAST_DUE_DATE = '2001-01-15';

/** Today's date in the center's zone (YYYY-MM-DD). */
function centerToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(
    new Date(),
  );
}

async function csrf(page: Page): Promise<string> {
  return csrfTokenOf(await api(page, '/api/v1/auth/me'));
}

/** Sells a new 5-session package to the student; returns the charge id. */
async function chargeStudentViaApi(
  page: Page,
  familyId: string,
  studentId: string,
  suffix: string,
): Promise<string> {
  const product = await api(page, '/api/v1/billing/package-products', {
    method: 'POST',
    json: {
      name: `Vencimiento ${suffix}`,
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
      'Idempotency-Key': `e2e-due-dates-${suffix}`.padEnd(16, '0'),
    },
  });
  expect(sold.status).toBe(201);
  const charges = await api(
    page,
    `/api/v1/families/${familyId}/billing-account/charges`,
  );
  expect(charges.status).toBe(200);
  const items = (charges.body as { items: { id: string }[] }).items;
  expect(items).toHaveLength(1);
  return items[0]!.id;
}

test('changing due dates and finding overdue accounts', async ({
  page,
}, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyName = `Familia Vencida ${suffix}`;
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
    givenNames: 'Moroso',
    familyNames: `Vencido ${suffix}`,
  });
  const familyId = await createFamilyViaApi(page, familyName);
  await addToFamilyViaApi(page, familyId, 'students', studentId);
  const chargeId = await chargeStudentViaApi(page, familyId, studentId, suffix);

  // 2. On the account, move the due date into the past with a reason.
  await page.goto(`/billing/accounts/${familyId}`);
  const charge = main.locator(`[data-charge-id="${chargeId}"]`);
  await expect(charge).toContainText(billing.tuition.chargeStatus.pending);
  await expect(charge.locator('[data-due-date-changed]')).toHaveCount(0);
  await charge
    .getByRole('button', { name: new RegExp(`^${dueDates.change.action}: `) })
    .click();
  const dialog = page.getByRole('dialog', { name: dueDates.change.title });
  await dialog
    .getByLabel(dueDates.change.newDate, { exact: true })
    .fill(PAST_DUE_DATE);
  await dialog
    .getByLabel(dueDates.change.reason, { exact: true })
    .fill('Prueba E2E de vencimiento');
  await dialog
    .getByRole('button', { name: dueDates.change.submit, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toContainText(
    billing.tuition.chargeStatus.overdue,
  );
  await expect(charge).toContainText(billing.tuition.chargeStatus.overdue);
  await expect(charge.locator('[data-due-date-changed]')).toHaveCount(1);

  // 3. Billing → Overdue lists the family; open its account from there.
  await main
    .getByRole('link', { name: billing.tabs.overdue, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/overdue');
  await main
    .getByLabel(billing.overdue.filters.minDays, { exact: true })
    .selectOption('91');
  await expect(page).toHaveURL('/billing/overdue?min_days_overdue=91');
  await main.getByRole('link', { name: familyName, exact: true }).click();
  await expect(page).toHaveURL(`/billing/accounts/${familyId}?status=overdue`);
  await expect(main.locator(`[data-charge-id="${chargeId}"]`)).toBeVisible();

  // 4. The family's due-date offset: 5 days.
  await main.getByRole('button', { name: dueDates.offset.editLabel }).click();
  const offset = page.getByRole('dialog', { name: dueDates.offset.title });
  await offset.getByLabel(dueDates.offset.field, { exact: true }).fill('5');
  await offset
    .getByRole('button', { name: dueDates.offset.save, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.locator('[data-due-offset]')).toHaveText(
    dueDates.offset.label_other.replace('{{count}}', '5'),
  );

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
