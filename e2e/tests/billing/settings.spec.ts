import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';

// The billing start date (#366 E3): the demo Owner's shared session (no
// login, no redemption, no registration) opens Billing → Settings, sees
// billing off, turns it on from a date ten years ahead (so no session of
// the shared E2E database is ever charged), sees the date, finds the
// Unbilled tab's "everything is billed" state, then turns billing off
// again from the dialog. Whatever happens, the spec leaves billing off for
// the later specs (through the API when the UI didn't get there).

const billing = common.billing;
const settings = billing.settings;

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** The text of a `{{date}}` message, with any date. */
function withAnyDate(message: string): RegExp {
  const [before, after] = message.split('{{date}}') as [string, string];
  return new RegExp(`^${escape(before)}.+${escape(after)}$`);
}

/** `YYYY-MM-DD` ten years later (29 February becomes the 28th). */
function tenYearsAfter(date: string): string {
  const [year, month, day] = date.split('-') as [string, string, string];
  const later = String(Number(year) + 10);
  return `${later}-${month}-${month === '02' && day === '29' ? '28' : day}`;
}

/** Turns billing off through the API when it is on. */
async function ensureBillingOff(page: Page): Promise<void> {
  const current = await api(page, '/api/v1/billing/settings');
  expect(current.status).toBe(200);
  const body = current.body as {
    billing_starts_on: string | null;
    version: number;
  };
  if (body.billing_starts_on === null) {
    return;
  }
  const me = await api(page, '/api/v1/auth/me');
  const saved = await api(page, '/api/v1/billing/settings', {
    method: 'PATCH',
    json: { version: body.version, billing_starts_on: null },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(saved.status).toBe(200);
}

test('turning billing on from a date and off again', async ({ page }) => {
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const tabs = main.getByRole('navigation', { name: billing.tabs.label });

  await page.goto('/');
  try {
    // 1. Billing → Settings: billing is off.
    await page
      .getByRole('banner')
      .getByRole('link', { name: common.nav.billing })
      .click();
    // Billing opens Accounts, its first tab since #379.
    await expect(page).toHaveURL('/billing/accounts');
    await tabs.getByRole('link', { name: billing.tabs.settings }).click();
    await expect(page).toHaveURL('/billing/settings');
    await expect(
      main.getByRole('heading', { level: 1, name: settings.title }),
    ).toBeVisible();
    await expect(main.getByText(settings.status.off)).toBeVisible();

    // 2. Turn billing on from a date ten years ahead.
    await main.getByRole('button', { name: settings.change }).click();
    let dialog = page.getByRole('dialog', { name: settings.dialog.title });
    const date = dialog.getByLabel(settings.dialog.startsOn, { exact: true });
    const today = await date.inputValue();
    expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const startsOn = tenYearsAfter(today);
    await date.fill(startsOn);
    await dialog.getByRole('button', { name: settings.save }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(main.getByText(settings.saved)).toBeVisible();
    const on = main.getByText(withAnyDate(settings.status.on));
    await expect(on).toBeVisible();
    await expect(on).toContainText(startsOn.slice(0, 4));

    // 3. Unbilled: nothing since that date is unbilled.
    await tabs.getByRole('link', { name: billing.tabs.unbilled }).click();
    await expect(page).toHaveURL('/billing/unbilled');
    await expect(
      main.getByRole('heading', { level: 1, name: billing.unbilled.title }),
    ).toBeVisible();
    await expect(
      main.getByText(withAnyDate(billing.unbilled.emptyOn)),
    ).toContainText(startsOn.slice(0, 4));

    // 4. Back to Settings: turn billing off again.
    await tabs.getByRole('link', { name: billing.tabs.settings }).click();
    await main.getByRole('button', { name: settings.change }).click();
    dialog = page.getByRole('dialog', { name: settings.dialog.title });
    await dialog
      .getByRole('radio', { name: settings.dialog.modes.off })
      .check();
    await dialog.getByRole('button', { name: settings.save }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(main.getByText(settings.status.off)).toBeVisible();
  } finally {
    await ensureBillingOff(page);
  }

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
