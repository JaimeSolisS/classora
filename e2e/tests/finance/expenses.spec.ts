import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// Operating expenses (M7-12b F3), on the demo Owner's shared session: 0
// logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN stays
// 50/50) and no password step. An expense can't be deleted (#115 E3), so each
// run's description carries its own suffix. Adjustments, voids and reversals
// need the password and are covered by Vitest and QA hands-on.

const finance = common.finance;
const labels = finance.expenses;

test('the Owner records an expense and pays it in two payments', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const description = `Gasto E2E ${suffix}`;
  const main = page.getByRole('main');
  const status = main.locator('[data-expense-announcement]');

  // /expenses opens the expense list, the first tab.
  await page.goto('/expenses');
  await expect(page).toHaveURL('/expenses/records');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();
  await expect(
    main
      .getByRole('navigation', { name: finance.tabs.label })
      .getByRole('link', { name: finance.tabs.records }),
  ).toHaveAttribute('aria-current', 'page');

  // 1. A new expense, with an active seeded category.
  await main
    .getByRole('button', { name: labels.add, exact: true })
    .first()
    .click();
  const create = page.getByRole('dialog', { name: labels.dialog.create.title });
  await create
    .getByLabel(labels.dialog.create.category, { exact: true })
    .selectOption({ index: 1 });
  await create
    .getByLabel(labels.dialog.create.description, { exact: true })
    .fill(description);
  await create
    .getByLabel(labels.dialog.create.amount, { exact: true })
    .fill('1000');
  await expectNoAxeViolations(page);
  await create
    .getByRole('button', { name: labels.dialog.create.submit })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/\/expenses\/records\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: description }),
  ).toBeVisible();
  const statusField = main.locator('[data-expense-field="status"] dd');
  await expect(statusField).toHaveText(labels.statuses.unpaid);

  // 2. A partial payment.
  const recordButton = main.getByRole('button', {
    name: labels.detail.actions.record,
    exact: true,
  });
  await recordButton.click();
  const pay = page.getByRole('dialog', { name: labels.dialog.payment.title });
  await pay
    .getByLabel(labels.dialog.payment.amount, { exact: true })
    .fill('400');
  await pay
    .getByLabel(labels.dialog.payment.method, { exact: true })
    .selectOption({ label: common.billing.paymentMethods.cash });
  await expectNoAxeViolations(page);
  await pay.getByRole('button', { name: labels.dialog.payment.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(status).toHaveText(labels.detail.saved.payment);
  await expect(statusField).toHaveText(labels.statuses.partially_paid);

  // 3. The rest: the amount defaults to what's open.
  await expect(recordButton).toBeEnabled();
  await recordButton.click();
  const rest = page.getByRole('dialog', { name: labels.dialog.payment.title });
  await expect(
    rest.getByLabel(labels.dialog.payment.amount, { exact: true }),
  ).toHaveValue('600.00');
  await rest
    .getByLabel(labels.dialog.payment.method, { exact: true })
    .selectOption({ label: common.billing.paymentMethods.cash });
  await rest
    .getByRole('button', { name: labels.dialog.payment.submit })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(statusField).toHaveText(labels.statuses.paid);
  await expect(recordButton).toHaveCount(0);
  await expect(main.locator('[data-payments-table] tbody tr')).toHaveCount(2);

  // 4. The list finds it by its description, paid.
  await page.goto(`/expenses/records?q=${encodeURIComponent(suffix)}`);
  const row = main.locator('[data-expenses-table] tbody tr');
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(labels.statuses.paid);
  await expectNoAxeViolations(page);
  expect(await cspViolations(page)).toEqual([]);
});
