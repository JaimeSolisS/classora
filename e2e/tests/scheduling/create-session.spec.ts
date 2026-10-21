import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Scheduling a one-off session (#48 G6). The demo Owner (the shared state;
// schedule.manage) creates a student and a subject through the API, opens
// "Schedule" and schedules that student, without tutor or classroom, on the
// first weekday at least 14 days ahead, 10:00–11:00: the success panel says
// "Unassigned". "Schedule another" with the same student, 10:30–11:30: the
// conflict panel lists the student overlap. The page has no axe violations.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (two API creates with the shared Owner session). It never
// changes the zone or the scheduling policy; the stored session blocks zone
// changes from then on (#47), which no spec makes.

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;

// Chromium logs every 4xx resource as a console error: the expected 409.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

/** The first Monday-Friday at least 14 days after today, in the center zone. */
function firstWeekdayAhead(): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 14, 12));
  while (date.getUTCDay() === 0 || date.getUTCDay() === 6) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
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

async function fillWhen(page: Page, date: string, start: string, end: string) {
  const main = page.getByRole('main');
  await main
    .getByLabel(common.scheduling.fields.date, { exact: true })
    .fill(date);
  await main
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill(start);
  await main
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill(end);
}

test('an Owner schedules a session and meets a student overlap', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Agenda ${suffix}`;
  const studentName = `Lucía ${familyNames}`;
  const subjectName = `Materia E2E ${suffix}`;
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

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createStudentViaApi(page, { givenNames: 'Lucía', familyNames });
  await createSubjectViaApi(page, 'Primaria', subjectName);

  // 1. "Schedule" opens the form.
  await page
    .getByRole('link', { name: common.nav.schedule, exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/schedule\/new$/);
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();

  // 2. No tutor, no classroom, 10:00-11:00: unassigned.
  const date = firstWeekdayAhead();
  await pickStudent(page, familyNames, studentName);
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await fillWhen(page, date, '10:00', '11:00');
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();
  await expect(
    main.getByText(common.scheduling.success.unassigned, { exact: true }),
  ).toBeVisible();

  // 3. The same student, 10:30-11:30: the student overlap.
  await main
    .getByRole('button', {
      name: common.scheduling.success.another,
      exact: true,
    })
    .click();
  await expect(
    main.getByLabel(common.scheduling.fields.date, { exact: true }),
  ).toHaveValue(date);
  await pickStudent(page, familyNames, studentName);
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await fillWhen(page, date, '10:30', '11:30');
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  const alert = main.getByRole('alert');
  await expect(alert).toContainText(
    common.scheduling.conflicts.student_overlap,
  );
  await expect(alert).toContainText(common.scheduling.severity.hard);
  await expect(alert.getByRole('heading')).toBeFocused();

  // 4. Accessibility of the page with the conflict panel, light and dark.
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme });
    const results = await new AxeBuilder({ page })
      .withTags(WCAG_TAGS)
      .analyze();
    expect(
      results.violations.map(
        (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
      ),
    ).toEqual([]);
  }

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
