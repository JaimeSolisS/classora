import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { pickOption } from '../support/combobox';
import { CENTER_ZONE, firstWeekdayAhead } from '../support/scheduling';
import { createStudentViaApi } from '../support/students';
import { createClassroomViaApi, createSubjectViaApi } from '../support/tutors';

// Scheduling from the calendar (HF-02 F3). The demo Owner (the shared state;
// schedule.manage) creates a student, a subject and a classroom through the
// API, and opens the Week of D, the first weekday at least 14 days ahead,
// filtered by the new student (other specs' sessions stay out, so every
// press lands on an empty time):
// 1. a drag in D's lane from 10:00 to 11:30 opens "Schedule a session" at
//    /schedule/new?date=D&start=10:00&end=11:30 with those values; the
//    student and subject are picked and saved; "View in calendar" shows the
//    new session at 10:00–11:30;
// 2. a click at 14:00 in D's lane opens the form at 14:00–15:00 (one hour);
// 3. with the room filter on the new classroom, a click fills Classroom;
// 4. on today's lane, a click at 00:00 (past) opens nothing, and a click at
//    23:00 opens 23:00–23:45 (the cap); skipped near midnight center time;
// 5. axe is clean on the calendar and the pre-filled form; CSP is clean.
//
// Gestures use page.mouse on the rendered FullCalendar v7 DOM, as
// reschedule.spec.ts: the day lanes' `data-date` and the slot rows'
// `data-time` (30-minute rows, 15-minute snap). No fixed sleeps.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 0 logins, 0 redemptions and
// 0 registrations (three API creates and one session saved with the shared
// Owner session). It never changes the zone, the opening hours, the policy
// or closures. Each `make e2e` run starts from a fresh database: no cleanup.

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;

test.use({ viewport: { width: 1280, height: 900 } });

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** Today and the wall-clock minutes now, in the center zone. */
function centerNow(): { today: string; minutes: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: CENTER_ZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  );
  return {
    today: `${parts.year}-${parts.month}-${parts.day}`,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

/**
 * A point in `date`'s lane inside the `time` row (HH:MM, a 30-minute row):
 * `fraction` 0.25 is that row's first 15-minute snap, 0.75 its second.
 */
async function pointAt(
  calendar: Locator,
  date: string,
  time: string,
  fraction: number,
): Promise<{ x: number; y: number }> {
  const lane = calendar.locator(`[role="cell"][data-date="${date}"]`).last();
  const row = calendar.locator(`[data-time="${time}:00"]`).last();
  await expect(lane).toBeVisible();
  const laneBox = (await lane.boundingBox())!;
  const rowBox = (await row.boundingBox())!;
  return {
    x: laneBox.x + laneBox.width / 2,
    y: rowBox.y + rowBox.height * fraction,
  };
}

/** Scrolls the `time` row to the middle of the viewport. */
async function centreRow(calendar: Locator, time: string): Promise<void> {
  await calendar
    .locator(`[data-time="${time}:00"]`)
    .last()
    .evaluate((element) =>
      element.scrollIntoView({ block: 'center', inline: 'nearest' }),
    );
}

async function clickAt(
  page: Page,
  calendar: Locator,
  date: string,
  time: string,
): Promise<void> {
  await centreRow(calendar, time);
  const point = await pointAt(calendar, date, time, 0.25);
  await page.mouse.click(point.x, point.y);
}

test('an Owner schedules by selecting times in the calendar', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Rango ${suffix}`;
  const studentName = `Teo ${familyNames}`;
  const subjectName = `Materia Rango ${suffix}`;
  const roomName = `Aula Rango ${suffix}`;
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
  const field = (label: string) => main.getByLabel(label, { exact: true });
  const fields = common.scheduling.fields;

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Teo',
    familyNames,
  });
  await createSubjectViaApi(page, 'Primaria', subjectName);
  const roomId = await createClassroomViaApi(page, roomName);
  const day = firstWeekdayAhead();
  const week = `/schedule?view=week&date=${day}&student=${studentId}`;

  // 1. Drag 10:00 → 11:30 in D's lane.
  await page.goto(week);
  await loaded();
  expect(await axeViolations(page)).toEqual([]);
  await centreRow(calendar, '10:30');
  const from = await pointAt(calendar, day, '10:00', 0.25);
  const to = await pointAt(calendar, day, '11:00', 0.75);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 12 });
  await page.mouse.up();
  await expect(page).toHaveURL(
    `/schedule/new?date=${day}&start=10:00&end=11:30`,
  );
  await expect(field(fields.date)).toHaveValue(day);
  await expect(field(fields.start)).toHaveValue('10:00');
  await expect(field(fields.end)).toHaveValue('11:30');
  expect(await axeViolations(page)).toEqual([]);
  await pickOption(main, fields.student, familyNames, studentName);
  await field(fields.subject).selectOption({ label: subjectName });
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();
  await main
    .getByRole('link', { name: common.scheduling.success.viewInCalendar })
    .click();
  await expect(page).toHaveURL(`/schedule?view=week&date=${day}`);
  await loaded();
  await expect(
    calendar.locator('[data-session-id]').filter({ hasText: subjectName }),
  ).toContainText('10:00–11:30');

  // 2. A click at 14:00 in D's lane: one hour.
  await page.goto(week);
  await loaded();
  await clickAt(page, calendar, day, '14:00');
  await expect(page).toHaveURL(
    `/schedule/new?date=${day}&start=14:00&end=15:00`,
  );
  await expect(field(fields.start)).toHaveValue('14:00');
  await expect(field(fields.end)).toHaveValue('15:00');

  // 3. The room filter fills Classroom.
  await page.goto(week);
  await loaded();
  await main
    .getByLabel(common.scheduling.calendar.filters.room, { exact: true })
    .selectOption({ label: roomName });
  await expect(page).toHaveURL(new RegExp(`room=${roomId}`));
  await loaded();
  await clickAt(page, calendar, day, '16:00');
  await expect(page).toHaveURL(
    `/schedule/new?date=${day}&start=16:00&end=17:00&room=${roomId}`,
  );
  await expect(field(fields.classroom)).toHaveValue(roomId);

  // 4. Today: a past time opens nothing; a later one opens, capped at 23:45.
  const now = centerNow();
  if (now.minutes >= 30 && now.minutes < 22 * 60 + 45) {
    await page.goto(
      `/schedule?view=week&date=${now.today}&student=${studentId}`,
    );
    await loaded();
    await clickAt(page, calendar, now.today, '00:00');
    await expect(calendar).toBeVisible();
    await clickAt(page, calendar, now.today, '23:00');
    // Had the past click navigated, the calendar would be gone and this
    // URL could not be the 23:00 one.
    await expect(page).toHaveURL(
      `/schedule/new?date=${now.today}&start=23:00&end=23:45`,
    );
  }

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
