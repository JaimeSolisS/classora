import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { inviteTutorViaApi } from '../support/tutors';

// Tutor availability (#45 G1). The demo Owner (the shared state; tutor.manage)
// invites a tutor through the API, opens their profile, sets Monday
// 16:00–20:00 and Wednesday 16:00–18:00 (saved), adds time off for a date
// range (listed), deletes it (gone), and reloads: the windows persist.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (an invitation only, with the shared Owner session).

const NOTE = 'Vacaciones E2E';

// Under machine load the profile and its three sections take a while.
const TEST_TIMEOUT_MS = 90_000;

function numbered(text: string, number: number): string {
  return text.replace('{{number}}', String(number));
}

const availability = (page: Page) =>
  page.getByRole('main').locator('#availability');

async function addWindow(
  page: Page,
  number: number,
  values: { weekday: string; start: string; end: string },
) {
  const section = availability(page);
  await section.getByRole('button', { name: common.weeklyWindows.add }).click();
  const day = section.getByLabel(
    numbered(common.weeklyWindows.weekdayLabel, number),
    { exact: true },
  );
  await expect(day).toBeFocused();
  await day.selectOption(values.weekday);
  await section
    .getByLabel(numbered(common.weeklyWindows.startsAtLabel, number), {
      exact: true,
    })
    .fill(values.start);
  await section
    .getByLabel(numbered(common.weeklyWindows.endsAtLabel, number), {
      exact: true,
    })
    .fill(values.end);
}

test('a tutor availability is set by an Owner', async ({ page }, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });

  // The app origin first, so the API call carries the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const tutorId = await inviteTutorViaApi(page, {
    username: `e2e.tutor.availability.${suffix}`,
    displayName: `E2E Tutor Disponibilidad ${suffix}`,
  });
  await page.goto(`/tutors/${tutorId}`);
  const section = availability(page);
  await expect(
    section.getByRole('heading', {
      level: 2,
      name: common.tutors.availability.title,
    }),
  ).toBeVisible();
  await expect(
    section.getByText(common.tutors.availability.exceptionsEmpty),
  ).toBeVisible();

  // 1. Weekly windows: Monday 16:00–20:00, Wednesday 16:00–18:00.
  await addWindow(page, 1, { weekday: '1', start: '16:00', end: '20:00' });
  await addWindow(page, 2, { weekday: '3', start: '16:00', end: '18:00' });
  await section
    .getByRole('button', { name: common.tutors.availability.save })
    .click();
  await expect(
    section
      .getByRole('status')
      .filter({ hasText: common.tutors.availability.saved }),
  ).toBeVisible();

  // 2. Time off for a date range: listed.
  await section
    .getByRole('button', { name: common.tutors.availability.add })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog
    .getByLabel(common.tutors.availability.startsOn, { exact: true })
    .fill('2030-12-21');
  await dialog
    .getByLabel(common.tutors.availability.endsOn, { exact: true })
    .fill('2030-12-31');
  await dialog
    .getByLabel(common.tutors.availability.noteLabel, { exact: true })
    .fill(NOTE);
  await dialog
    .getByRole('button', { name: common.tutors.availability.send })
    .click();
  await expect(dialog).toBeHidden();
  const timeOff = section.getByRole('row').filter({ hasText: NOTE });
  await expect(timeOff).toBeVisible();
  await expect(
    timeOff.getByRole('cell', {
      name: common.tutors.availability.kinds.unavailable,
      exact: true,
    }),
  ).toBeVisible();

  // 3. Delete it: gone.
  await timeOff
    .getByRole('button', {
      name: new RegExp(common.tutors.availability.delete),
    })
    .click();
  const confirm = page.getByRole('dialog');
  await confirm
    .getByRole('button', {
      name: common.tutors.availability.deleteConfirm,
      exact: true,
    })
    .click();
  await expect(confirm).toBeHidden();
  await expect(timeOff).toHaveCount(0);
  await expect(
    section.getByText(common.tutors.availability.exceptionsEmpty),
  ).toBeVisible();

  // 4. Reload: the windows persist.
  await page.reload();
  const day = (number: number) =>
    availability(page).getByLabel(
      numbered(common.weeklyWindows.weekdayLabel, number),
      { exact: true },
    );
  const time = (label: string, number: number) =>
    availability(page).getByLabel(numbered(label, number), { exact: true });
  await expect(day(1)).toHaveValue('1');
  await expect(time(common.weeklyWindows.startsAtLabel, 1)).toHaveValue(
    '16:00',
  );
  await expect(time(common.weeklyWindows.endsAtLabel, 1)).toHaveValue('20:00');
  await expect(day(2)).toHaveValue('3');
  await expect(time(common.weeklyWindows.endsAtLabel, 2)).toHaveValue('18:00');
  await expect(day(3)).toHaveCount(0);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
