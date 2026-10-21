import { expect, test, type Page } from '@playwright/test';
import { api } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, errors, fill } from '../support/catalog';

// Business settings (#37 I1): the demo Owner's shared session (no login, so
// no login budget) renames the center and opens Sunday, sees both kept after
// a reload, then picks another time zone: the reauthentication dialog
// appears, and after "Cancel" the stored zone is still Mexico City. Finally
// the name and a closed Sunday are restored.
//
// The settings record is a singleton: this is the only spec that edits it,
// in one serial test, and it never changes the time zone, because every spec
// runs in America/Mexico_City (_docs/testing-guidelines.md, E2E data rules).
// The shared session is never reauthenticated (tests that confirm a
// password sign in on their own, #28 H2), so the zone change always asks
// for the password here.
//
// Scheduling policies (#61 H1): the same test opens the "Scheduling
// policies" tab, checks the version in force (24 h notice, 48 h to answer
// an offer), publishes a version "From date" today + 60 days with a 12-hour
// cancellation notice, sees it under "Upcoming changes" and deletes it. It
// never publishes an immediate version, so no other spec sees a changed
// policy (_docs/testing-guidelines.md, E2E data rules).

const DEFAULT_ZONE = 'America/Mexico_City';

// Chromium logs every 4xx resource as a console error: the expected 403
// reauth_required before the dialog.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 403/;

const sunday = common.settings.weekdays.sunday;

function nameField(page: Page) {
  return page
    .getByRole('main')
    .getByLabel(common.settings.brand.centerName, { exact: true });
}

function sundayOpen(page: Page) {
  return page.getByRole('main').getByRole('checkbox', {
    name: fill(common.settings.hours.openOn, { day: sunday }),
  });
}

function sundayTime(page: Page, which: 'dayOpensAt' | 'dayClosesAt') {
  return page
    .getByRole('main')
    .getByLabel(fill(common.settings.hours[which], { day: sunday }), {
      exact: true,
    });
}

function zoneSelect(page: Page) {
  return page
    .getByRole('main')
    .getByLabel(common.settings.timeZone.label, { exact: true });
}

async function saveAndWait(page: Page) {
  const main = page.getByRole('main');
  await main.getByRole('button', { name: common.settings.save }).click();
  await expect(main.getByRole('status')).toHaveText(common.settings.saved);
}

/** `YYYY-MM-DD` in the center zone, `days` after today. */
function centerDatePlus(days: number): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: DEFAULT_ZONE }).format(
    new Date(Date.now() + days * 86_400_000),
  );
}

async function editSchedulingPolicy(page: Page) {
  const policies = common.settings.schedulingPolicies;
  const main = page.getByRole('main');
  await main
    .getByRole('navigation', { name: common.settings.tabs.label })
    .getByRole('link', { name: common.settings.tabs.schedulingPolicies })
    .click();
  await expect(page).toHaveURL('/settings/scheduling-policies');
  await expect(
    main.getByRole('heading', { level: 1, name: policies.title }),
  ).toBeVisible();
  const hours = (count: number) => fill(policies.units.hours, { count });
  const current = main.getByRole('region', { name: policies.current.title });
  await expect(current).toContainText(
    fill(policies.rules.changeNotice, { duration: hours(24) }),
  );
  await expect(current).toContainText(
    fill(policies.rules.response, { duration: hours(48) }),
  );
  const upcoming = main.getByRole('region', { name: policies.upcoming.title });
  const before = await api(page, '/api/v1/scheduling-policies');
  const startingUpcoming = (before.body as { upcoming: unknown[] }).upcoming
    .length;

  // A version from today + 60 days with a 12-hour cancellation notice.
  await main
    .getByLabel(policies.fields.changeNoticeHours, { exact: true })
    .fill('12');
  await main
    .getByRole('radio', { name: policies.takesEffect.date, exact: true })
    .check();
  await main
    .getByLabel(policies.takesEffect.dateLabel, { exact: true })
    .fill(centerDatePlus(60));
  await main.getByRole('button', { name: policies.form.save }).click();
  await expect(main.getByRole('status')).toHaveText(policies.saved);
  const rows = upcoming.getByRole('row');
  await expect(rows).toHaveCount(startingUpcoming + 2); // header + new row
  await expect(upcoming).toContainText(`${hours(24)} → ${hours(12)}`);

  // Delete it again.
  await upcoming.getByRole('button').last().click();
  const dialog = page.getByRole('dialog', {
    name: policies.deleteDialog.title,
  });
  await dialog
    .getByRole('button', { name: policies.deleteDialog.confirm })
    .click();
  await expect(dialog).toBeHidden();
  await expect(main.getByRole('status')).toHaveText(policies.deleted);
  if (startingUpcoming === 0) {
    await expect(upcoming).toContainText(policies.upcoming.empty);
  }
  const after = await api(page, '/api/v1/scheduling-policies');
  expect((after.body as { upcoming: unknown[] }).upcoming).toHaveLength(
    startingUpcoming,
  );
  await expect(current).toContainText(
    fill(policies.rules.changeNotice, { duration: hours(24) }),
  );
}

async function openSettings(page: Page) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.settings })
    .click();
  await expect(page).toHaveURL('/settings');
  await expect(
    page
      .getByRole('main')
      .getByRole('heading', { level: 1, name: common.settings.title }),
  ).toBeVisible();
  await expect(nameField(page)).toBeVisible();
}

test('editing the business settings', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const newName = `Classora E2E ${suffix}`;
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

  // 1. Settings from the navigation, with the stored values.
  await page.goto('/');
  await openSettings(page);
  const originalName = await nameField(page).inputValue();
  await expect(zoneSelect(page)).toHaveValue(DEFAULT_ZONE);
  await expect(sundayOpen(page)).not.toBeChecked();
  await expect(sundayTime(page, 'dayOpensAt')).toBeDisabled();

  // 2. A new name and Sunday open 10:00-13:00.
  await nameField(page).fill(newName);
  await sundayOpen(page).click();
  await sundayTime(page, 'dayOpensAt').fill('10:00');
  await sundayTime(page, 'dayClosesAt').fill('13:00');
  await saveAndWait(page);

  // 3. Kept after a reload.
  await page.reload();
  await expect(nameField(page)).toHaveValue(newName);
  await expect(sundayOpen(page)).toBeChecked();
  await expect(sundayTime(page, 'dayOpensAt')).toHaveValue('10:00');
  await expect(sundayTime(page, 'dayClosesAt')).toHaveValue('13:00');

  // 4. Another zone asks for the password; "Cancel" changes nothing.
  await zoneSelect(page).selectOption('America/Cancun');
  await main.getByRole('button', { name: common.settings.save }).click();
  const dialog = page.getByRole('dialog', { name: common.auth.reauth.title });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: common.auth.reauth.cancel }).click();
  await expect(dialog).toBeHidden();
  await expect(main.getByRole('alert')).toContainText(errors.reauth_required);
  await page.reload();
  await expect(zoneSelect(page)).toHaveValue(DEFAULT_ZONE);
  const stored = await api(page, '/api/v1/business-settings');
  expect((stored.body as { time_zone: string }).time_zone).toBe(DEFAULT_ZONE);

  // 5. Restore the name and a closed Sunday.
  await nameField(page).fill(originalName);
  await sundayOpen(page).click();
  await expect(sundayTime(page, 'dayOpensAt')).toBeDisabled();
  await saveAndWait(page);
  await page.reload();
  await expect(nameField(page)).toHaveValue(originalName);
  await expect(sundayOpen(page)).not.toBeChecked();

  // 6. Scheduling policies (#61 H1): an upcoming version, then deleted.
  await editSchedulingPolicy(page);

  expect(consoleErrors).toEqual([]);
  expect(await cspViolations(page)).toEqual([]);
});
