import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { addToFamilyViaApi, createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Tuition plans (#374 H3): the demo Owner's shared session (no login, no
// redemption, no registration). Through the API, with per-run unique
// names: a subject, two students and a family with both. Then from
// Billing: a monthly per-period price for the subject (Prices), an
// open-ended individual plan for the first student (its page opens),
// paused from the next period, resumed, ended after the current period;
// and a second plan (made through the API for the second student) deleted
// from its page. Billing stays off in the shared E2E database (#366), so
// no charge is posted: the spec asserts the UI states only.

const billing = common.billing;
const prices = billing.prices;
const tuition = billing.tuition;

/** The center's today, `YYYY-MM-DD`. */
async function centerToday(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const zone = (me.body as { center_time_zone: string }).center_time_zone;
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

/** A plan through the API (for the plan the spec deletes). */
async function createPlanViaApi(
  page: Page,
  body: { student_id: string; service_price_id: string; starts_on: string },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  const created = await api(page, '/api/v1/billing/tuition-plans', {
    method: 'POST',
    json: { ...body, due_offset_days: 0 },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** The text of the summary's value for a term. */
function summary(page: Page, term: string) {
  return page
    .getByRole('main')
    .locator('dt', { hasText: new RegExp(`^${term}$`) })
    .locator('xpath=following-sibling::dd[1]');
}

test('managing tuition plans', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const subjectName = `Colegiatura ${suffix}`;
  const familyNames = `Plan ${suffix}`;
  const firstName = `Primera ${familyNames}`;
  const secondName = `Segunda ${familyNames}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const main = page.getByRole('main');

  // 1. The setup, through the API (the page on the app origin).
  await page.goto('/');
  await createSubjectViaApi(page, 'Secundaria', subjectName);
  const firstId = await createStudentViaApi(page, {
    givenNames: 'Primera',
    familyNames,
  });
  const secondId = await createStudentViaApi(page, {
    givenNames: 'Segunda',
    familyNames,
  });
  const familyId = await createFamilyViaApi(page, `Familia ${familyNames}`);
  await addToFamilyViaApi(page, familyId, 'students', firstId);
  await addToFamilyViaApi(page, familyId, 'students', secondId);
  const today = await centerToday(page);

  // 2. Billing → Prices: a monthly per-period price for the subject.
  await page.goto('/billing/prices');
  await main.getByRole('button', { name: prices.add }).first().click();
  let dialog = page.getByRole('dialog', { name: prices.add });
  await dialog
    .getByLabel(prices.fields.classType, { exact: true })
    .selectOption('individual');
  await dialog
    .getByLabel(prices.fields.pricingModel, { exact: true })
    .selectOption('per_period');
  await dialog
    .getByLabel(prices.fields.periodFrequency, { exact: true })
    .selectOption('monthly');
  await dialog
    .getByLabel(prices.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await dialog
    .getByLabel(prices.fields.amount, { exact: true })
    .fill('1500.00');
  await dialog.getByRole('button', { name: prices.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing\/prices\/[0-9a-f-]{36}$/);
  const priceId = page.url().split('/').at(-1)!;

  // 3. Billing → Tuition plans: an open-ended individual plan.
  await main
    .getByRole('link', { name: billing.tabs.tuitionPlans, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/tuition-plans');
  await expect(
    main.getByRole('heading', { level: 1, name: tuition.title }),
  ).toBeVisible();
  await main.getByRole('button', { name: tuition.add }).first().click();
  dialog = page.getByRole('dialog', { name: tuition.add });
  await dialog
    .getByLabel(tuition.fields.student, { exact: true })
    .fill(firstName);
  await dialog.getByRole('radio', { name: firstName, exact: true }).check();
  await dialog
    .getByLabel(tuition.fields.price, { exact: true })
    .selectOption(priceId);
  await expect(
    dialog.getByLabel(tuition.fields.startsOn, { exact: true }),
  ).toHaveValue(today);
  await dialog.getByRole('button', { name: tuition.create }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing\/tuition-plans\/[0-9a-f-]{36}$/);
  await expect(summary(page, tuition.columns.endsOn)).toHaveText(
    tuition.openEnded,
  );
  await expect(main.getByText(tuition.pauses.empty)).toBeVisible();

  // 4. Pause from the next period, until resumed.
  await main.getByRole('button', { name: tuition.pause.action }).click();
  dialog = page.getByRole('dialog', { name: tuition.pause.title });
  await dialog
    .getByLabel(tuition.pause.from, { exact: true })
    .selectOption({ index: 1 });
  await dialog.getByRole('button', { name: tuition.pause.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.locator('[data-pause-id]')).toHaveCount(1);
  await expect(main.locator('[data-pause-id]')).toContainText(
    tuition.untilResumed,
  );
  await expect(main.locator('[data-upcoming-period]').nth(1)).toContainText(
    tuition.periodStates.paused,
  );

  // 5. Resume on the pause's first period: the pause is gone.
  await main.getByRole('button', { name: tuition.resume.action }).click();
  dialog = page.getByRole('dialog', { name: tuition.resume.title });
  await dialog.getByRole('button', { name: tuition.resume.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.locator('[data-pause-id]')).toHaveCount(0);
  await expect(main.getByText(tuition.pauses.empty)).toBeVisible();

  // 6. End after the current period.
  await main.getByRole('button', { name: tuition.end.action }).click();
  dialog = page.getByRole('dialog', { name: tuition.end.title });
  const lastDay = dialog.getByLabel(tuition.end.lastDay, { exact: true });
  const endLabel = (await lastDay.locator('option').first().textContent())!;
  await lastDay.selectOption({ index: 0 });
  await dialog.getByRole('button', { name: tuition.end.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(summary(page, tuition.columns.endsOn)).toHaveText(endLabel);
  await expect(main.locator('[data-upcoming-period]')).toHaveCount(1);

  // 7. A second plan (through the API) deleted from its page.
  await createPlanViaApi(page, {
    student_id: secondId,
    service_price_id: priceId,
    starts_on: today,
  });
  await main.getByRole('link', { name: tuition.back }).click();
  await expect(page).toHaveURL('/billing/tuition-plans');
  await expect(
    main.getByRole('row').filter({ hasText: firstName }),
  ).toHaveCount(1);
  // The second student's plans only (the shared database keeps other runs').
  await page.goto(`/billing/tuition-plans?student_id=${secondId}`);
  const secondRow = main.getByRole('row').filter({ hasText: secondName });
  await expect(secondRow).toHaveCount(1);
  await secondRow
    .getByRole('link', { name: new RegExp(`^${tuition.view}`) })
    .click();
  await expect(page).toHaveURL(/\/billing\/tuition-plans\/[0-9a-f-]{36}$/);
  await main.getByRole('button', { name: tuition.delete }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: tuition.delete })
    .click();
  // Back on the list, with its filter.
  await expect(page).toHaveURL(`/billing/tuition-plans?student_id=${secondId}`);
  await expect(main.getByText(tuition.emptyFiltered)).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
