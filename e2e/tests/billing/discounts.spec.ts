import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createFamilyViaApi } from '../support/families';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Discounts and custom prices (#367 G3): the demo Owner's shared session
// (no login, no redemption, no registration). Through the API, with
// per-run unique names: a subject, an individual per-session catalog price
// for it (350.00 from today), a student and a family. Then from Billing:
// a 50 % scholarship for that subject, assigned to the student through the
// picker and removed again; a sibling discount (minimum 2, also only for
// that subject) listed as "everyone who qualifies"; the scholarship
// deactivated; a family custom price from tomorrow (300.00) listed as
// upcoming, an overlapping one refused, and the upcoming one deleted. No
// charge is posted: billing stays off in the shared E2E database (#366).

const billing = common.billing;
const discounts = billing.discounts;
const customPrices = billing.customPrices;

// Chromium logs every 4xx resource as a console error: the one expected
// 409 answer (custom_price_overlap).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

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

/** The center's today and a catalog price for the subject from it. */
async function createCatalogPrice(
  page: Page,
  subjectId: string,
): Promise<{ today: string; priceId: string }> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const zone = (me.body as { center_time_zone: string }).center_time_zone;
  const today = new Intl.DateTimeFormat('en-CA', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const created = await api(page, '/api/v1/billing/service-prices', {
    method: 'POST',
    json: {
      class_type: 'individual',
      pricing_model: 'per_session',
      subject_id: subjectId,
      effective_from: today,
      amount: '350.00',
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return { today, priceId: (created.body as { id: string }).id };
}

/** Fills "Add discount" with a kind, a name, a percentage and the subject. */
async function addDiscount(
  page: Page,
  kind: string,
  name: string,
  percent: string,
  subjectName: string,
) {
  await page
    .getByRole('main')
    .getByRole('button', { name: discounts.add })
    .first()
    .click();
  const dialog = page.getByRole('dialog', { name: discounts.add });
  await dialog
    .getByRole('radio', { name: new RegExp(`^${escape(kind)}\\b`) })
    .first()
    .check();
  await dialog.getByLabel(discounts.fields.name, { exact: true }).fill(name);
  await dialog
    .getByLabel(discounts.fields.percent, { exact: true })
    .fill(percent);
  await dialog
    .getByRole('group', { name: discounts.fields.subjects })
    .getByRole('checkbox', { name: subjectName, exact: true })
    .check();
  return dialog;
}

test('managing discounts and custom prices', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const subjectName = `Descuentos ${suffix}`;
  const studentGiven = 'Becaria';
  const studentFamily = `E2E ${suffix}`;
  const studentName = `${studentGiven} ${studentFamily}`;
  const familyName = `Familia Descuentos ${suffix}`;
  const scholarshipName = `Beca ${suffix}`;
  const siblingName = `Hermanos ${suffix}`;
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

  // 1. The setup, through the API (the page on the app origin).
  await page.goto('/');
  const subjectId = await createSubjectViaApi(page, 'Secundaria', subjectName);
  const { today, priceId } = await createCatalogPrice(page, subjectId);
  await createStudentViaApi(page, {
    givenNames: studentGiven,
    familyNames: studentFamily,
  });
  const familyId = await createFamilyViaApi(page, familyName);

  // 2. Billing → Discounts.
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.billing })
    .click();
  await main
    .getByRole('link', { name: billing.tabs.discounts, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/discounts');
  await expect(
    main.getByRole('heading', { level: 1, name: discounts.title }),
  ).toBeVisible();

  // 3. A 50 % scholarship for the subject: its page opens.
  let dialog = await addDiscount(
    page,
    discounts.kinds.scholarship,
    scholarshipName,
    '50',
    subjectName,
  );
  await expect(
    dialog.getByRole('radio', { name: discounts.audience.assigned }),
  ).toBeChecked();
  await dialog.getByRole('button', { name: discounts.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing\/discounts\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: scholarshipName }),
  ).toBeVisible();

  // 4. Assign the student through the picker, then remove the assignment.
  await main.getByRole('button', { name: discounts.assign.title }).click();
  dialog = page.getByRole('dialog', { name: discounts.assign.title });
  await dialog
    .getByLabel(billing.targets.search.student, { exact: true })
    .fill(studentFamily);
  await dialog.getByRole('radio', { name: studentName, exact: true }).check();
  await dialog.getByRole('button', { name: discounts.assign.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const assignments = main.getByRole('table', {
    name: discounts.assignments.title,
  });
  await expect(
    assignments.getByRole('row').filter({ hasText: studentName }),
  ).toHaveCount(1);
  await main
    .getByRole('button', {
      name: discounts.assignments.removeFor.replace('{{name}}', studentName),
    })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: discounts.assignments.remove })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByText(studentName, { exact: true })).toHaveCount(0);

  // 5. Deactivate the scholarship: inactive.
  await main.getByRole('button', { name: discounts.deactivate }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: discounts.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.locator('dd').getByText(discounts.status.inactive, { exact: true }),
  ).toBeVisible();

  // 6. A sibling discount (minimum 2): everyone who qualifies.
  await main.getByRole('link', { name: discounts.back }).click();
  await expect(page).toHaveURL('/billing/discounts');
  dialog = await addDiscount(
    page,
    discounts.kinds.sibling,
    siblingName,
    '10',
    subjectName,
  );
  await dialog
    .getByLabel(discounts.fields.minFamilyStudents, { exact: true })
    .fill('2');
  await dialog.getByRole('button', { name: discounts.save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await main.getByRole('link', { name: discounts.back }).click();
  await main.getByLabel(discounts.filters.q, { exact: true }).fill(siblingName);
  await expect(page).toHaveURL(/[?&]q=/);
  const siblingRow = main.getByRole('row').filter({ hasText: siblingName });
  await expect(siblingRow).toContainText(discounts.audience.all);
  await expect(siblingRow).toContainText(discounts.status.current);

  // 7. Custom prices, for the family (a deep link preselects it).
  await main
    .getByRole('link', { name: billing.tabs.customPrices, exact: true })
    .click();
  await expect(page).toHaveURL('/billing/custom-prices');
  await expect(
    main.getByRole('heading', { level: 1, name: customPrices.title }),
  ).toBeVisible();
  await page.goto(`/billing/custom-prices?family_id=${familyId}`);
  await expect(main.getByText(familyName, { exact: true })).toBeVisible();

  async function addCustomPrice() {
    await main.getByRole('button', { name: customPrices.add }).first().click();
    const added = page.getByRole('dialog', { name: customPrices.add });
    await expect(
      added.getByRole('radio', {
        name: billing.targets.kinds.family,
        exact: true,
      }),
    ).toBeChecked();
    await added
      .getByLabel(customPrices.fields.service, { exact: true })
      .selectOption(priceId);
    await added
      .getByLabel(customPrices.fields.amount, { exact: true })
      .fill('300.00');
    await added
      .getByLabel(customPrices.fields.effectiveFrom, { exact: true })
      .fill(addDays(today, 1));
    await added.getByRole('button', { name: billing.prices.save }).click();
    return added;
  }

  await addCustomPrice();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const customRow = main.getByRole('row').filter({ hasText: subjectName });
  await expect(customRow).toHaveCount(1);
  await expect(customRow).toContainText('$300.00');
  await expect(customRow).toContainText('$350.00');
  await expect(customRow).toContainText(billing.prices.status.upcoming);

  // 8. The same dates again: the overlap, inside the dialog.
  dialog = await addCustomPrice();
  await expect(dialog.getByRole('alert')).toContainText(
    customPrices.overlap.family,
  );
  await dialog.getByRole('button', { name: billing.prices.cancel }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 9. Delete the upcoming one: it is gone.
  await customRow
    .getByRole('button', {
      name: new RegExp(
        `^${escape(customPrices.deleteFor.split('{{name}}')[0]!)}${escape(familyName)}`,
      ),
    })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: customPrices.delete })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(customRow).toHaveCount(0);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
