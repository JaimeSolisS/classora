import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// The KPI page (M8-09b E3), on the demo Owner's shared session: 0 logins,
// 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN stays 50/50,
// registration.ip 10/10) and no password step. The shared E2E database
// isn't empty, so only the structure is checked: no figure is asserted
// (#126's integration tests and the Vitest oracle fixtures cover them), and
// role visibility is covered by Vitest.

const labels = common.reports;
const kpis = labels.kpis;
const profitability = labels.profitability;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

test('the Owner reads the six KPI sections, by range and basis', async ({
  page,
}) => {
  await recordCspViolations(page);
  const main = page.getByRole('main');
  const sectionTitles = [
    kpis.revenueTrend.title,
    kpis.balances.title,
    kpis.courseProfitability.title,
    kpis.rooms.title,
    kpis.retention.title,
    kpis.tutors.title,
  ];

  // 1. From the nav: /reports opens the KPIs, the last 3 months.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.reports, exact: true })
    .click();
  await expect(page).toHaveURL('/reports/kpis');
  await expect(
    main.getByRole('heading', { level: 1, name: kpis.title }),
  ).toBeVisible();
  await expect(
    main.getByRole('button', {
      name: profitability.presets.last3Months,
      exact: true,
    }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(main.getByRole('heading', { level: 2 })).toHaveText(
    sectionTitles,
  );
  // Every section has loaded (no skeleton, no error).
  await expect(main.locator('[data-kpi-section]')).toHaveCount(6);
  await expect(
    main.locator('[data-kpi-section]:not([data-kpi-status="ready"])'),
  ).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);

  // 2. A preset writes the range to the URL.
  await main
    .getByRole('button', { name: profitability.presets.lastMonth, exact: true })
    .click();
  await expect(page).toHaveURL(/[?&]from=\d{4}-\d{2}-01&to=\d{4}-\d{2}-\d{2}/);
  await expect(
    main.locator('[data-kpi-section]:not([data-kpi-status="ready"])'),
  ).toHaveCount(0);

  // 3. The basis switch on K1: cash, kept by a reload.
  const trend = main.locator('[data-kpi-section="revenue-trend"]');
  await trend
    .getByRole('button', { name: profitability.basis.cash, exact: true })
    .click();
  await expect(page).toHaveURL(/[?&]basis=cash/);
  await expect(
    trend.locator('[data-kpi-basis-shown="cash"], [data-kpi-empty]'),
  ).toHaveCount(1);
  await page.reload();
  await expect(
    main
      .locator('[data-kpi-section="revenue-trend"]')
      .getByRole('button', { name: profitability.basis.cash, exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    main.locator('[data-kpi-section]:not([data-kpi-status="ready"])'),
  ).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);
  expect(await cspViolations(page)).toEqual([]);
});
