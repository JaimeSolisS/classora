import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// The financial reports page (M7-14b E3), on the demo Owner's shared
// session: 0 logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN
// stays 50/50) and no password step. The shared E2E database isn't empty,
// so no amount is asserted; #117's integration tests and the Vitest oracle
// fixtures cover the figures.

const labels = common.reports;
const profitability = labels.profitability;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const DAY_MS = 24 * 60 * 60 * 1000;

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

test('the Owner reads the profitability and the balances, by basis and range', async ({
  page,
}) => {
  await recordCspViolations(page);
  const main = page.getByRole('main');
  const classRows = main.locator(
    '[data-report-class-table] tbody tr[data-class-type]',
  );

  // 1. From the nav: the accrual report for this month, and the balances.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.reports, exact: true })
    .click();
  await expect(page).toHaveURL('/reports/finance');
  await expect(
    main.getByRole('heading', { level: 1, name: labels.title }),
  ).toBeVisible();
  await expect(classRows).toHaveCount(4);
  await expect(classRows.locator('th')).toHaveText([
    profitability.classTypes.individual,
    profitability.classTypes.group,
    profitability.classTypes.course,
    profitability.classTypes.unallocated,
  ]);
  for (const card of [
    labels.balances.receivables.title,
    labels.balances.payables.title,
    labels.balances.deferred.title,
  ]) {
    await expect(
      main.getByRole('heading', { level: 3, name: card, exact: true }),
    ).toBeVisible();
  }
  expect(await axeViolations(page)).toEqual([]);

  // 2. Cash: center totals only, no class rows.
  await main
    .getByRole('button', { name: profitability.basis.cash, exact: true })
    .click();
  await expect(page).toHaveURL(/[?&]basis=cash/);
  await expect(main.locator('[data-report-cash]')).toBeVisible();
  await expect(main.locator('[data-report-class-table]')).toHaveCount(0);

  // 3. A custom range over 366 days: the bound, and no figures.
  const to = await main
    .getByLabel(profitability.range.to, { exact: true })
    .inputValue();
  const from = new Date(Date.parse(`${to}T12:00:00Z`) - 400 * DAY_MS)
    .toISOString()
    .slice(0, 10);
  await main.getByLabel(profitability.range.from, { exact: true }).fill(from);
  await expect(
    main.getByText(profitability.range.errors.tooLong, { exact: true }),
  ).toBeVisible();
  await expect(main.locator('[data-report-basis-shown]')).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`[?&]from=${from}`));

  // 4. A reload keeps the basis and the range.
  await page.reload();
  await expect(page).toHaveURL(/[?&]basis=cash/);
  await expect(
    main.getByLabel(profitability.range.from, { exact: true }),
  ).toHaveValue(from);
  await expect(
    main.getByLabel(profitability.range.to, { exact: true }),
  ).toHaveValue(to);
  await expect(
    main.getByText(profitability.range.errors.tooLong, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByRole('button', { name: profitability.basis.cash, exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(await axeViolations(page)).toEqual([]);
  expect(await cspViolations(page)).toEqual([]);
});
