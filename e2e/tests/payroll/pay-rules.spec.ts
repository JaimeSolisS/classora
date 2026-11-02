import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// Pay rules (#444 D5), on the demo Owner's shared session, which holds
// payroll.manage: 0 logins, 0 redemptions, 0 registrations. Finance Staff has
// no E2E session. Open the page, add a makeup version dated in the future, see
// it upcoming, delete it. A version must be later than every other, so the
// spec removes what it adds, and clears any upcoming makeup version a failed
// run left, through the API. No other spec reads the rules.

const rules = common.payroll.payRules;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

interface Overview {
  today: string;
  makeup: { upcoming: { id: string }[] };
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

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** The overview, with no upcoming makeup version left behind. */
async function clearUpcomingMakeup(page: Page): Promise<Overview> {
  const read = await api(page, '/api/v1/payroll/pay-rules');
  expect(read.status).toBe(200);
  const overview = read.body as Overview;
  const me = await api(page, '/api/v1/auth/me');
  for (const { id } of [...overview.makeup.upcoming].reverse()) {
    const removed = await api(page, `/api/v1/payroll/pay-rules/${id}`, {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': csrfTokenOf(me) },
    });
    expect(removed.status).toBe(204);
  }
  return overview;
}

test('the Owner adds a pay rule version and deletes it', async ({ page }) => {
  await recordCspViolations(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const { today } = await clearUpcomingMakeup(page);

  try {
    // 1. From the Payroll tabs to the page.
    const main = page.getByRole('main');
    await page.goto('/payroll/tutors');
    await main
      .getByRole('link', { name: common.payroll.tabs.payRules, exact: true })
      .click();
    await expect(page).toHaveURL('/payroll/pay-rules');
    await expect(
      main.getByRole('heading', { level: 1, name: rules.title }),
    ).toBeVisible();
    const makeup = main.locator('[data-pay-rule-event="makeup"]');
    await expect(
      makeup.locator('[data-version-status="current"]'),
    ).toBeVisible();
    expect(await axeViolations(page)).toEqual([]);

    // 2. A new version, dated in the future.
    await makeup
      .getByRole('button', {
        name: rules.newVersionFor.replace('{{event}}', rules.events.makeup),
      })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    expect(await axeViolations(page)).toEqual([]);
    await dialog
      .getByLabel(rules.fields.mode, { exact: true })
      .selectOption('fixed');
    await dialog
      .getByLabel(rules.fields.amount, { exact: true })
      .fill('150.00');
    await dialog
      .getByLabel(rules.fields.effectiveFrom, { exact: true })
      .fill(addDays(today, 30));
    await dialog.getByRole('button', { name: rules.save }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    // 3. It shows as upcoming.
    const upcoming = makeup.locator('[data-version-status="upcoming"]');
    await expect(upcoming).toContainText('$150.00');
    await expect(upcoming).toContainText(common.payroll.status.upcoming);

    // 4. Delete it.
    await upcoming.getByRole('button').click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: common.payroll.deleteDialog.confirm })
      .click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(upcoming).toHaveCount(0);
    expect(await cspViolations(page)).toEqual([]);
  } finally {
    await clearUpcomingMakeup(page);
  }
});
