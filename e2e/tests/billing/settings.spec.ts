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
//
// The cancellation charges card (#385 D3), same shared session (0 logins,
// 0 redemptions, 0 registrations): the seeded rule (late cancellations
// charged), a change scheduled ten years ahead with late cancellations
// credited, seen under the upcoming changes, then deleted. Whatever
// happens, the spec deletes every upcoming version through the API, so the
// shared database keeps only the seeded one.

const billing = common.billing;
const settings = billing.settings;
const cancellation = settings.cancellation;

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

/** The text of a `{{date}}` message, with any date, anywhere. */
function containingAnyDate(message: string): RegExp {
  const [before, after] = message.split('{{date}}') as [string, string];
  return new RegExp(`${escape(before)}.+${escape(after)}`);
}

/** Deletes every upcoming cancellation rule version through the API. */
async function deleteUpcomingRules(page: Page): Promise<void> {
  const overview = await api(page, '/api/v1/billing/cancellation-rules');
  expect(overview.status).toBe(200);
  const { upcoming } = overview.body as { upcoming: { id: string }[] };
  if (upcoming.length === 0) {
    return;
  }
  const me = await api(page, '/api/v1/auth/me');
  // Newest first: each deletion leaves the others upcoming.
  for (const rule of [...upcoming].reverse()) {
    const removed = await api(
      page,
      `/api/v1/billing/cancellation-rules/${rule.id}`,
      { method: 'DELETE', headers: { 'X-CSRF-Token': csrfTokenOf(me) } },
    );
    expect(removed.status).toBe(204);
  }
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
    await main
      .getByRole('button', { name: settings.change, exact: true })
      .click();
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
    await main
      .getByRole('button', { name: settings.change, exact: true })
      .click();
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

test('scheduling a cancellation charges change and deleting it', async ({
  page,
}) => {
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const late = `${cancellation.classes.late}: ${cancellation.outcomes.credit}`;

  await page.goto('/billing/settings');
  try {
    // 1. The seeded rule: late cancellations are charged.
    const card = main.getByRole('region', { name: cancellation.title });
    const lateRow = card.locator('[data-cancellation-class="late_outcome"]');
    await expect(lateRow).toContainText(cancellation.classes.late);
    await expect(lateRow).toContainText(cancellation.outcomes.charge);
    await expect(card.getByText(cancellation.upcoming.empty)).toBeVisible();

    // 2. Schedule a change ten years ahead: late cancellations credited.
    await card
      .getByRole('button', { name: cancellation.schedule, exact: true })
      .click();
    const dialog = page.getByRole('dialog', {
      name: cancellation.dialog.title,
    });
    await dialog
      .getByLabel(cancellation.classes.late, { exact: true })
      .selectOption('credit');
    const date = dialog.getByLabel(cancellation.dialog.effectiveDate, {
      exact: true,
    });
    const tomorrow = await date.getAttribute('min');
    expect(tomorrow).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const effective = tenYearsAfter(tomorrow!);
    await date.fill(effective);
    await dialog.getByRole('button', { name: settings.save }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(main.getByText(cancellation.saved)).toBeVisible();

    // 3. It is under the upcoming changes; the rule in force is unchanged.
    const scheduled = card
      .locator('li[data-rule-id]')
      .filter({ hasText: late })
      .filter({ hasText: effective.slice(0, 4) });
    await expect(scheduled).toHaveCount(1);
    await expect(scheduled).toContainText(
      containingAnyDate(cancellation.upcoming.takesEffect),
    );
    await expect(lateRow).toContainText(cancellation.outcomes.charge);

    // 4. Delete it.
    await scheduled
      .getByRole('button', {
        name: containingAnyDate(cancellation.upcoming.deleteFor),
      })
      .click();
    const confirm = page.getByRole('dialog', {
      name: cancellation.deleteDialog.title,
    });
    await confirm
      .getByRole('button', {
        name: cancellation.deleteDialog.confirm,
        exact: true,
      })
      .click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(main.getByText(cancellation.deleted)).toBeVisible();
    await expect(card.getByText(cancellation.upcoming.empty)).toBeVisible();
  } finally {
    await deleteUpcomingRules(page);
  }

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
