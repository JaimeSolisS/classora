import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations } from '../support/a11y';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { inviteTutorViaApi } from '../support/tutors';

// Tutor pay and payroll settings (#425 H3), on the demo Owner's shared
// session: 0 logins, 0 redemptions, 0 registrations. A tutor invited through
// the API (never redeemed, so `pending`) is found in Payroll. The Owner adds
// monthly terms with a salary and a per-hour rate, adds a dated second rate
// and sees "Vigente" and "Próxima", then deletes the upcoming one. Axe on
// the list, the page and a dialog.
//
// Settings: the page shows the current state, and the basis is changed and
// restored. The spec never sets `payroll_starts_on`, so no period is opened
// in the shared stack, and changing the basis locks nothing (#106 addendum).
// Whatever happens, the spec restores the basis through the API.
//
// Finance Staff has no E2E session: it is covered by tests/authz/matrix.toml,
// #105's access tests and Vitest. The tutor's own view is a step in
// scheduling/offers.spec.ts, on the existing demo.tutor context.

const payroll = common.payroll;

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

function versionIn(page: Page, kind: string, status: string) {
  return page
    .locator(`[data-class-kind="${kind}"] [data-version-status="${status}"]`)
    .filter({ hasText: payroll.status[status as 'current'] });
}

/** Opens "Add rate", fills it for an individual per-hour rate. */
async function fillRate(page: Page, rate: string, from?: string) {
  await page
    .getByRole('main')
    .getByRole('button', { name: payroll.rateDialog.title })
    .click();
  const dialog = page.getByRole('dialog', { name: payroll.rateDialog.title });
  await dialog
    .getByLabel(payroll.fields.classKind, { exact: true })
    .selectOption('individual');
  if (from) {
    await dialog
      .getByLabel(payroll.fields.effectiveFrom, { exact: true })
      .fill(from);
  }
  await dialog
    .getByLabel(payroll.fields.model, { exact: true })
    .selectOption('per_hour');
  await dialog.getByLabel(payroll.fields.rate, { exact: true }).fill(rate);
  return dialog;
}

/** Puts the basis back through the API when a failure left it changed. */
async function restoreBasis(page: Page, basis: string): Promise<void> {
  const current = await api(page, '/api/v1/payroll/settings');
  expect(current.status).toBe(200);
  const body = current.body as {
    version: number;
    payroll_starts_on: string | null;
    biweekly_anchor_on: string;
    salary_proration_basis: string;
  };
  if (body.salary_proration_basis === basis) {
    return;
  }
  const me = await api(page, '/api/v1/auth/me');
  const saved = await api(page, '/api/v1/payroll/settings', {
    method: 'PATCH',
    json: {
      version: body.version,
      payroll_starts_on: body.payroll_starts_on,
      biweekly_anchor_on: body.biweekly_anchor_on,
      salary_proration_basis: basis,
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(saved.status).toBe(200);
}

test('the Owner sets a tutor pay terms and rates in Payroll', async ({
  page,
}, testInfo) => {
  await recordCspViolations(page);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Pago E2E ${suffix}`;
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const tutorId = await inviteTutorViaApi(page, {
    username: `e2e.pay.${suffix}`,
    displayName: name,
  });
  const read = await api(
    page,
    `/api/v1/payroll/tutors/${tutorId}/compensation`,
  );
  expect(read.status).toBe(200);
  const { today } = read.body as { today: string };

  // 1. Find the tutor: an invited tutor is pending, not in the default list.
  const main = page.getByRole('main');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.payroll, exact: true })
    .click();
  await expect(page).toHaveURL('/payroll/tutors');
  await expect(
    main.getByRole('heading', { level: 1, name: payroll.list.title }),
  ).toBeVisible();
  await main
    .getByLabel(payroll.list.state, { exact: true })
    .selectOption('pending');
  await main.getByLabel(payroll.list.search, { exact: true }).fill(name);
  const link = main.getByRole('link', { name, exact: true });
  await expect(link).toBeVisible();
  await expectNoAxeViolations(page);
  await link.click();
  await expect(page).toHaveURL(`/payroll/tutors/${tutorId}`);
  await expect(main.getByRole('heading', { level: 1, name })).toBeVisible();

  // 2. Monthly terms with a salary.
  await main.getByRole('button', { name: payroll.termsDialog.title }).click();
  const terms = page.getByRole('dialog', { name: payroll.termsDialog.title });
  await terms
    .getByLabel(payroll.fields.frequency, { exact: true })
    .selectOption('monthly');
  await terms
    .getByLabel(payroll.fields.salary, { exact: true })
    .fill('18000.00');
  await terms.getByRole('button', { name: payroll.termsDialog.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.locator('[data-version-status="current"]').first(),
  ).toContainText('$18,000.00');

  // 3. A per-hour rate from today (axe with the dialog open).
  const first = await fillRate(page, '250.1250');
  await expectNoAxeViolations(page);
  await first.getByRole('button', { name: payroll.rateDialog.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(versionIn(page, 'individual', 'current')).toContainText(
    '$250.125',
  );

  // 4. A dated second version: "Vigente" and "Próxima".
  const second = await fillRate(page, '275.00', addDays(today, 30));
  await second.getByRole('button', { name: payroll.rateDialog.submit }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(versionIn(page, 'individual', 'upcoming')).toContainText(
    '$275.00',
  );
  await expect(versionIn(page, 'individual', 'current')).toContainText(
    '$250.125',
  );
  await expectNoAxeViolations(page);

  // 5. Delete the upcoming one.
  await versionIn(page, 'individual', 'upcoming').getByRole('button').click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: payroll.deleteDialog.confirm })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(versionIn(page, 'individual', 'upcoming')).toHaveCount(0);
  await expect(versionIn(page, 'individual', 'current')).toContainText(
    '$250.125',
  );
  expect(await cspViolations(page)).toEqual([]);
});

test('the Owner changes the proration basis and restores it', async ({
  page,
}) => {
  await recordCspViolations(page);
  const main = page.getByRole('main');
  await page.goto('/payroll/settings');
  await expect(
    main.getByRole('heading', { level: 1, name: payroll.settings.title }),
  ).toBeVisible();
  const before = await api(page, '/api/v1/payroll/settings');
  expect(before.status).toBe(200);
  const basis = (before.body as { salary_proration_basis: 'weekdays' })
    .salary_proration_basis;
  const other = basis === 'weekdays' ? 'calendar_days' : 'weekdays';
  const shown = main.locator('[data-payroll-basis]');
  await expect(shown).toHaveText(payroll.settings.bases[basis]);

  async function chooseBasis(value: string) {
    await main.getByRole('button', { name: payroll.settings.change }).click();
    const dialog = page.getByRole('dialog', {
      name: payroll.settings.dialogTitle,
    });
    await dialog
      .getByLabel(payroll.settings.basis, { exact: true })
      .selectOption(value);
    await dialog.getByRole('button', { name: payroll.settings.save }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  }

  try {
    await chooseBasis(other);
    await expect(shown).toHaveText(payroll.settings.bases[other]);
    await chooseBasis(basis);
    await expect(shown).toHaveText(payroll.settings.bases[basis]);
  } finally {
    await restoreBasis(page, basis);
  }
  expect(await cspViolations(page)).toEqual([]);
});
