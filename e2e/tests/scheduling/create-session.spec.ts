import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { switchColorScheme } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import {
  comboboxField,
  comboboxOptions,
  pickOption,
} from '../support/combobox';
import { createStudentViaApi } from '../support/students';
import { fillTime } from '../support/scheduling';
import { createSubjectViaApi } from '../support/tutors';

// Scheduling a one-off session (#48 G6). The demo Owner (the shared state;
// schedule.manage) creates a student and a subject through the API, opens
// "Schedule" (the calendar, #57), then "Schedule a session", and schedules that student, without tutor or classroom, on the
// first weekday at least 14 days ahead, 10:00–11:00: the success panel says
// "Unassigned". "Schedule another" with the same student, 10:30–11:30: the
// conflict panel lists the student overlap, and "Suggested times" (#50 D7)
// lists 11:00–12:00 first. The page has no axe violations with both shown;
// choosing that time fills in the form, and scheduling it is "Unassigned".
// The student field is the type-ahead combobox (#545 D4): a click lists
// students without typing, and "lucia agenda …" finds "Lucía Agenda …".
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (two API creates with the shared Owner session; no tutor
// is invited). It never
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
  await pickOption(
    page.getByRole('main'),
    common.scheduling.fields.student,
    familyNames,
    name,
  );
}

/**
 * The type-ahead student field (#545 D4): a click on the empty field lists
 * students without typing; lower-case text without the accent finds the
 * student; ArrowDown and Enter choose it. Local or server mode alike.
 */
async function typeAheadStudent(page: Page, query: string, name: string) {
  const main = page.getByRole('main');
  const label = common.scheduling.fields.student;
  const field = comboboxField(main, label);
  const list = comboboxOptions(main, label);
  await field.click();
  await expect(field).toHaveAttribute('aria-expanded', 'true');
  await expect(list.getByRole('option').first()).toBeVisible();
  await field.fill(query);
  await expect(list.getByRole('option', { name, exact: true })).toBeVisible();
  // The keyboard reaches it (first in relevance order; at most 10 steps).
  for (let step = 0; step < 10; step += 1) {
    await field.press('ArrowDown');
    const active = await field.getAttribute('aria-activedescendant');
    if (
      active &&
      (await page.locator(`[id="${active}"]`).textContent()) === name
    ) {
      break;
    }
  }
  await field.press('Enter');
  await expect(field).toHaveAttribute('aria-expanded', 'false');
  await expect(field).toHaveValue(name);
}

async function fillWhen(page: Page, date: string, start: string, end: string) {
  const main = page.getByRole('main');
  await main
    .getByLabel(common.scheduling.fields.date, { exact: true })
    .fill(date);
  await fillTime(
    main.getByLabel(common.scheduling.fields.start, { exact: true }),
    start,
  );
  await fillTime(
    main.getByLabel(common.scheduling.fields.end, { exact: true }),
    end,
  );
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

  // 1. "Schedule" opens the calendar (#57), whose "Schedule a session" link
  // opens the form.
  await page
    .getByRole('link', { name: common.nav.schedule, exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/schedule$/);
  await main
    .getByRole('link', {
      name: common.scheduling.calendar.scheduleSession,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/schedule\/new$/);
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();

  // 2. No tutor, no classroom, 10:00-11:00: unassigned. The student comes
  // from the type-ahead list (#545): "lucia agenda …" finds "Lucía Agenda …".
  const date = firstWeekdayAhead();
  await typeAheadStudent(page, `lucia agenda ${suffix}`, studentName);
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  // HF-13 (#598): submitted without a date or times, then filled in, the
  // "required" messages go without another submit. The start is picked
  // from its list; the end follows it (start + 1 h).
  const submit = main.getByRole('button', {
    name: common.scheduling.submit,
    exact: true,
  });
  await submit.click();
  const required = (key: 'dateRequired' | 'startRequired' | 'endRequired') =>
    main.getByText(common.scheduling.validation[key], { exact: true });
  await expect(required('startRequired')).toBeVisible();
  await expect(required('endRequired')).toBeVisible();
  await main
    .getByLabel(common.scheduling.fields.date, { exact: true })
    .fill(date);
  const start = main.getByLabel(common.scheduling.fields.start, {
    exact: true,
  });
  await start.click();
  await page.getByRole('option', { name: '10:00', exact: true }).click();
  await expect(start).toHaveValue('10:00');
  await expect(
    main.getByLabel(common.scheduling.fields.end, { exact: true }),
  ).toHaveValue('11:00');
  await expect(required('dateRequired')).toHaveCount(0);
  await expect(required('startRequired')).toHaveCount(0);
  await expect(required('endRequired')).toHaveCount(0);
  await expect(start).not.toHaveAttribute('aria-invalid', 'true');
  await submit.click();
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

  // 4. "Suggested times": the nearest free time first, 11:00-12:00.
  const suggestions = main.getByRole('region', {
    name: common.scheduling.suggestions.heading,
  });
  const first = suggestions.getByRole('button').first();
  await expect(first).toHaveText(
    new RegExp(
      `^${common.scheduling.suggestions.slot
        .replace('{{date}}', '.+')
        .replace('{{start}}', '11:00')
        .replace('{{end}}', '12:00')}$`,
    ),
  );

  // 5. Accessibility of the page with the conflict panel and the
  // suggestions, light and dark. The switch waits for the colour
  // transitions to settle before axe runs (#241, support/a11y.ts).
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

  // 6. Choosing the time fills in the form; scheduling it is unassigned.
  await first.click();
  await expect(
    main.getByLabel(common.scheduling.fields.start, { exact: true }),
  ).toHaveValue('11:00');
  await expect(
    main.getByLabel(common.scheduling.fields.end, { exact: true }),
  ).toHaveValue('12:00');
  await expect(main.getByRole('alert')).toHaveCount(0);
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();
  await expect(
    main.getByText(common.scheduling.success.unassigned, { exact: true }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
