import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { inviteTutorViaApi } from '../support/tutors';

// Bonuses and deductions (#454 F3), on the demo Owner's shared session: 0
// logins, 0 redemptions, 0 registrations. A tutor invited through the API
// (never redeemed) gets a recurring item of a new type, which is ended. Types
// can't be deleted, so each run's type has its own name; the tutor is new, so
// nothing else reads the item. The spec never sets `payroll_starts_on`, so no
// period exists and the item only says what it will do. Finance Staff has no
// E2E session: tests/authz/matrix.toml and Vitest cover it.

const payroll = common.payroll;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

test('the Owner adds a type and a recurring item, then ends it', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const typeName = `Bono E2E ${suffix}`;
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const tutorId = await inviteTutorViaApi(page, {
    username: `e2e.items.${suffix}`,
    displayName: `Conceptos E2E ${suffix}`,
  });

  // 1. A type, from the Payroll tabs.
  const main = page.getByRole('main');
  await page.goto('/payroll/tutors');
  await main
    .getByRole('link', { name: payroll.tabs.payItemTypes, exact: true })
    .click();
  await expect(page).toHaveURL('/payroll/pay-item-types');
  await expect(
    main.getByRole('heading', { level: 1, name: payroll.payItemTypes.title }),
  ).toBeVisible();
  await main.getByRole('button', { name: payroll.payItemTypes.add }).click();
  const typeDialog = page.getByRole('dialog', {
    name: payroll.payItemTypes.dialog.addTitle,
  });
  await typeDialog
    .getByLabel(payroll.payItemTypes.dialog.name, { exact: true })
    .fill(typeName);
  await typeDialog
    .getByLabel(payroll.payItemTypes.dialog.direction, { exact: true })
    .selectOption('bonus');
  expect(await axeViolations(page)).toEqual([]);
  await typeDialog
    .getByRole('button', { name: payroll.payItemTypes.dialog.submit })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByText(typeName).first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);

  // 2. A recurring item on the tutor's pay page.
  await page.goto(`/payroll/tutors/${tutorId}`);
  const section = main.locator('[data-pay-items-section]');
  await expect(
    section.getByRole('heading', { name: payroll.payItems.title }),
  ).toBeVisible();
  await section.getByRole('button', { name: payroll.payItems.add }).click();
  const dialog = page.getByRole('dialog', {
    name: payroll.payItems.dialog.title,
  });
  const labels = payroll.payItems.dialog;
  const type = dialog.getByLabel(labels.type, { exact: true });
  const option = `${typeName} (${payroll.payItems.directions.bonus})`;
  await expect(type.locator('option', { hasText: typeName })).toBeAttached();
  await type.selectOption({ label: option });
  await dialog
    .getByLabel(labels.description, { exact: true })
    .fill('Apoyo de transporte');
  await dialog.getByLabel(labels.amount, { exact: true }).fill('300');
  await dialog
    .getByLabel(labels.recurrence, { exact: true })
    .selectOption('recurring');
  expect(await axeViolations(page)).toEqual([]);
  await dialog.getByRole('button', { name: labels.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 3. See it.
  const table = section.locator('[data-pay-items-table]');
  await expect(table).toContainText('Apoyo de transporte');
  await expect(table).toContainText('$300.00');
  await expect(table).toContainText(payroll.payItems.recurrences.recurring);
  expect(await axeViolations(page)).toEqual([]);

  // 4. End it. The date starts at today.
  await table
    .getByRole('button', { name: new RegExp(`^${payroll.payItems.end}`) })
    .click();
  const end = page.getByRole('dialog', {
    name: payroll.payItems.endDialog.title,
  });
  await expect(
    end.getByLabel(payroll.payItems.endDialog.date, { exact: true }),
  ).not.toHaveValue('');
  await end
    .getByRole('button', { name: payroll.payItems.endDialog.submit })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    table.getByRole('button', { name: new RegExp(`^${payroll.payItems.end}`) }),
  ).toHaveCount(0);
  expect(await cspViolations(page)).toEqual([]);
});
