import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from '../support/a11y';
import { api } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// Payroll periods (#110b H3), on the demo Owner's shared session: 0 logins,
// 0 redemptions, 0 registrations; LOGIN_ATTEMPTS_PER_RUN stays 50/50. The
// Owner opens the Periods tab, sees the list's empty state, filters it, and
// opens a period id that doesn't exist. CSP and axe on each.
//
// This spec can't reach a real period: no spec sets `payroll_starts_on`,
// periods come only from the 07:50 UTC generation job, and Finance Staff
// (who prepares) has no E2E session. Recalculate, prepare, return and
// finalize with the password are covered by #110's route tests
// (tests/integration/test_payroll_workflow*.py), the page's Vitest
// (apps/web/src/features/payroll/periods.test.tsx) and QA's required
// hands-on run on a live stack.

const payroll = common.payroll;
const MISSING_PERIOD = '00000000-0000-4000-8000-00000000e2e0';

test('the Owner opens the payroll periods', async ({ page }) => {
  await recordCspViolations(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const settings = await api(page, '/api/v1/payroll/settings');
  expect(settings.status).toBe(200);
  const off =
    (settings.body as { payroll_starts_on: string | null })
      .payroll_starts_on === null;

  // 1. The tab, first in the bar.
  const main = page.getByRole('main');
  await page.goto('/payroll');
  await expect(page).toHaveURL('/payroll/tutors');
  const tabs = main
    .getByRole('navigation', { name: payroll.tabs.label })
    .getByRole('link');
  await expect(tabs.first()).toHaveText(payroll.tabs.periods);
  await tabs.first().click();
  await expect(page).toHaveURL('/payroll/periods');
  await expect(
    main.getByRole('heading', { level: 1, name: payroll.periods.title }),
  ).toBeVisible();

  // 2. The empty state: payroll is off in the shared stack.
  if (off) {
    await expect(main.getByText(payroll.periods.payrollOff)).toBeVisible();
    await expect(
      main.getByRole('link', { name: payroll.periods.payrollOffLink }),
    ).toHaveAttribute('href', '/payroll/settings');
  }
  await expectNoAxeViolations(page);

  // 3. A filter goes to the URL.
  await main
    .getByLabel(payroll.periods.filters.status, { exact: true })
    .selectOption('prepared');
  await expect(page).toHaveURL('/payroll/periods?status=prepared');
  if (off) {
    await expect(main.getByText(payroll.periods.emptyFiltered)).toBeVisible();
  }
  await expectNoAxeViolations(page);

  // 4. A period that doesn't exist is not found.
  await page.goto(`/payroll/periods/${MISSING_PERIOD}`);
  await expect(
    main.getByRole('heading', { level: 1, name: common.notFound.title }),
  ).toBeVisible();
  await expectNoAxeViolations(page);
  expect(await cspViolations(page)).toEqual([]);
});
