import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// The calendar (#57 E7). The demo Owner (the shared state; schedule.view and
// schedule.manage) creates a student, a subject and one unassigned session
// through the API on the first weekday at least 14 days ahead, 10:00–11:00.
// "Schedule" opens the calendar; the spec goes to that week, filters by the
// new student (other specs' sessions stay out), finds the event by its
// accessible name ("Unassigned"), switches to Day, Month and List, opens the
// event's dialog with the keyboard and closes it (focus returns to the
// event), and runs axe on Week and List.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (three API creates with the shared Owner session). It
// never changes the zone, the policy or the closures.

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;

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

/** One unassigned session (no tutor, no room) through the API. Mexico City
 * has no DST, so the offset is always -06:00. */
async function createSessionViaApi(
  page: Page,
  studentId: string,
  subjectId: string,
  date: string,
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/class-sessions', {
    method: 'POST',
    json: {
      kind: 'individual',
      student_id: studentId,
      subject_id: subjectId,
      starts_at: `${date}T10:00:00-06:00`,
      ends_at: `${date}T11:00:00-06:00`,
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(
    results.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
    ),
  ).toEqual([]);
}

test('an Owner finds a session on the calendar in every view', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Calendario ${suffix}`;
  const studentName = `Marta ${familyNames}`;
  const subjectName = `Materia Cal ${suffix}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const calendar = main.getByRole('region', {
    name: common.scheduling.calendar.regionLabel,
  });
  const loaded = () => expect(calendar).toHaveAttribute('aria-busy', 'false');
  const event = calendar.getByRole('button', {
    name: new RegExp(
      `${subjectName}.*${common.scheduling.states['unassigned']}`,
    ),
  });

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Marta',
    familyNames,
  });
  const subjectId = await createSubjectViaApi(page, 'Primaria', subjectName);
  const date = firstWeekdayAhead();
  await createSessionViaApi(page, studentId, subjectId, date);

  // 1. "Schedule" opens the calendar; go to the session's week.
  await page
    .getByRole('link', { name: common.nav.schedule, exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/schedule$/);
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: common.scheduling.calendar.title,
    }),
  ).toBeVisible();
  await page.goto(`/schedule?view=week&date=${date}`);
  await loaded();

  // 2. Filter by the new student: the event is there, named "Unassigned".
  await main
    .getByLabel(common.scheduling.calendar.filters.student, { exact: true })
    .fill(familyNames);
  const option = main.getByRole('radio', { name: studentName });
  await expect(option).toBeVisible();
  await option.click();
  await expect(page).toHaveURL(new RegExp(`student=${studentId}`));
  await loaded();
  await expect(event).toHaveCount(1);
  await expectNoAxeViolations(page);

  // 3. Keyboard: the event takes focus, Enter opens the dialog, Escape
  // closes it and the focus goes back to the event.
  await expect(event).toHaveAttribute('tabindex', '0');
  await event.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: subjectName });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(studentName);
  await expect(dialog).toContainText(common.scheduling.states['unassigned']!);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(event).toBeFocused();

  // 4. Day, Month and List show it too.
  for (const view of ['day', 'month', 'list'] as const) {
    await main
      .getByRole('button', {
        name: common.scheduling.calendar.views[view],
        exact: true,
      })
      .click();
    await expect(
      main.getByRole('button', {
        name: common.scheduling.calendar.views[view],
        exact: true,
      }),
    ).toHaveAttribute('aria-pressed', 'true');
    await loaded();
    await expect(event).toHaveCount(1);
  }
  await expectNoAxeViolations(page);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
