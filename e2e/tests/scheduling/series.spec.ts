import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { switchColorScheme } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// A weekly series (#54b E4). The demo Owner (the shared state;
// schedule.manage) creates a student and a subject through the API, then on
// "Schedule a session" turns on "Repeats weekly": Monday and Wednesday,
// 10:00-11:00, no tutor and no classroom, from the first Monday at least 14
// days ahead, ending after 6 sessions. The success panel links to the series
// page, where:
// 1. the second occurrence moves to 12:00 and shows "Edited";
// 2. "Edit series", this and following from the third occurrence, 11:00-12:00:
//    the page of the new series says what moved and "Continues from…";
// 3. that page has no axe violations, light and dark;
// 4. "Cancel future occurrences", all of them, with a reason: "Ended".
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (two API creates with the shared Owner session; no tutor
// is invited). It never changes the zone, the scheduling policy or the
// closures.

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const series = common.scheduling.series;

/** The first Monday at least 14 days after today, in the center zone. */
function firstMondayAhead(): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 14, 12));
  while (date.getUTCDay() !== 1) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

function plusDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split('-').map(Number) as [
    number,
    number,
    number,
  ];
  return new Date(Date.UTC(year, month - 1, day + days, 12))
    .toISOString()
    .slice(0, 10);
}

function fill(text: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replace(`{{${key}}}`, String(value)),
    text,
  );
}

async function pickStudent(page: Page, familyNames: string, name: string) {
  const main = page.getByRole('main');
  await main
    .getByLabel(common.scheduling.fields.student, { exact: true })
    .fill(familyNames);
  const option = main.getByRole('radio', { name });
  await expect(option).toBeVisible();
  await option.check();
}

test('an Owner schedules, edits and cancels a weekly series', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Serie ${suffix}`;
  const studentName = `Mateo ${familyNames}`;
  const subjectName = `Materia serie ${suffix}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createStudentViaApi(page, { givenNames: 'Mateo', familyNames });
  await createSubjectViaApi(page, 'Primaria', subjectName);

  // Schedule the series: Monday (pre-checked) and Wednesday, 6 sessions.
  const monday = firstMondayAhead();
  await page.goto('/schedule/new');
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();
  await pickStudent(page, familyNames, studentName);
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await main.getByRole('switch', { name: series.repeats }).check();
  await main.getByLabel(series.startsOn, { exact: true }).fill(monday);
  await main
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('10:00');
  await main
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('11:00');
  await expect(
    main.getByRole('checkbox', { name: common.weeklyWindows.weekdays['1'] }),
  ).toBeChecked();
  await main
    .getByRole('checkbox', { name: common.weeklyWindows.weekdays['3'] })
    .check();
  await main.getByRole('radio', { name: series.endsOptions.count }).check();
  await main.getByLabel(series.count, { exact: true }).fill('6');
  await main.getByRole('button', { name: series.submit, exact: true }).click();
  await expect(
    main.getByRole('heading', { name: series.success.heading }),
  ).toBeVisible();
  await main
    .getByRole('link', { name: series.success.openSeries, exact: true })
    .click();
  await expect(page).toHaveURL(/\/schedule\/series\/[0-9a-f-]+$/);
  const firstUrl = page.url();
  await expect(
    main.getByRole('heading', { level: 1, name: series.page.title }),
  ).toBeVisible();
  const rows = main.locator('ol > li');
  await expect(rows).toHaveCount(6);

  // 1. The second occurrence (Wednesday) moves to 12:00-13:00: "Edited".
  await rows
    .nth(1)
    .getByRole('button', { name: new RegExp(`^${series.page.editOccurrence}`) })
    .click();
  const occurrenceDialog = page.getByRole('dialog', {
    name: series.occurrence.title,
  });
  const start = occurrenceDialog.getByLabel(common.scheduling.fields.start, {
    exact: true,
  });
  await expect(start).toHaveValue('10:00');
  await start.fill('12:00');
  await occurrenceDialog
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('13:00');
  await occurrenceDialog
    .getByRole('button', { name: series.occurrence.submit, exact: true })
    .click();
  await expect(occurrenceDialog).toBeHidden();
  await expect(rows.nth(1)).toContainText(series.page.edited);
  await expect(rows.nth(0)).not.toContainText(series.page.edited);

  // 2. Edit series: this and following from the third occurrence (the next
  // Monday), 11:00-12:00. The end stays on the last of the 6 sessions.
  await main
    .getByRole('button', { name: series.page.editSeries, exact: true })
    .click();
  const editDialog = page.getByRole('dialog', { name: series.edit.title });
  await expect(
    editDialog.getByRole('radio', { name: series.scope.following }),
  ).toBeChecked();
  await editDialog
    .getByLabel(series.scope.fromDate, { exact: true })
    .fill(plusDays(monday, 7));
  await editDialog
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('11:00');
  await editDialog
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('12:00');
  await expect(
    editDialog.getByLabel(series.untilDate, { exact: true }),
  ).toHaveValue(plusDays(monday, 16));
  await editDialog
    .getByRole('button', { name: series.edit.submit, exact: true })
    .click();
  await expect(editDialog).toBeHidden();
  await expect(page).not.toHaveURL(firstUrl);
  await expect(main.getByRole('status')).toContainText(
    fill(series.summary.moved_other, { count: 4 }),
  );
  await expect(
    main.getByRole('link', { name: series.page.continuesFrom, exact: true }),
  ).toBeVisible();
  await expect(rows).toHaveCount(4);

  // 3. Accessibility of the series page, light and dark (#241 switch).
  for (const colorScheme of ['light', 'dark'] as const) {
    await switchColorScheme(page, colorScheme);
    const results = await new AxeBuilder({ page })
      .withTags(WCAG_TAGS)
      .analyze();
    expect(
      results.violations.map(
        (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
      ),
    ).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: 'light' });

  // 4. Cancel every future occurrence with a reason: the series has ended.
  await main
    .getByRole('button', { name: series.page.cancelSeries, exact: true })
    .click();
  const cancelDialog = page.getByRole('dialog', { name: series.cancel.title });
  await cancelDialog.getByRole('radio', { name: series.scope.all }).check();
  await cancelDialog
    .getByLabel(series.cancel.reason, { exact: true })
    .fill('La familia cambió de horario.');
  await cancelDialog
    .getByRole('button', { name: series.cancel.submit, exact: true })
    .click();
  await expect(cancelDialog).toBeHidden();
  await expect(main.getByRole('status')).toContainText(
    fill(series.cancel.done_other, { count: 4 }),
  );
  await expect(
    main.getByText(series.page.ended, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByRole('button', { name: series.page.editSeries }),
  ).toHaveCount(0);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
