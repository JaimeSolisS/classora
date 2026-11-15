import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { pickOption } from '../support/combobox';
import { CENTER_ZONE, fillTime } from '../support/scheduling';
import { createStudentViaApi } from '../support/students';

// The built-in General subject (HF-12, #597 AC8). With the shared Owner
// session:
// 1. through the API: a student; demo.tutor's id; D = the first Thursday at
//    least 11 weeks ahead (no other spec uses it) and a 1-hour time on D
//    that demo.tutor is available for (a Thursday weekly window, or else an
//    `available` exception on D; adding availability never needs approval).
//    demo.tutor holds no qualification for General;
// 2. "Schedule a session" opens with General selected in Subject;
// 3. the student, demo.tutor, D and the time: "Schedule session" shows the
//    success panel at once, without any conflict (no "not qualified");
// 4. the session is on the calendar's week of D, named "General".
// CSP is clean. No fixed sleeps.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 0 logins, 0 redemptions and
// 0 registrations (API calls and the session with the shared Owner
// session). A rerun on the same database deletes its exceptions on D first.

const TEST_TIMEOUT_MS = 90_000;
const DEMO_TUTOR = 'demo.tutor';
const GENERAL = 'General';
const THURSDAY = 4;

/** The first Thursday at least 77 days after today (center zone). */
function thursdayAhead(): string {
  const [year, month, day] = new Intl.DateTimeFormat('en-CA', {
    timeZone: CENTER_ZONE,
  })
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 77, 12));
  while (date.getUTCDay() !== THURSDAY) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

const hhmm = (hour: number) => `${String(hour).padStart(2, '0')}:00`;

type Availability = {
  weekly: { weekday: number; starts_at: string; ends_at: string }[];
  exceptions: { id: string; kind: string; starts_on: string }[];
};

/** A 1-hour start on `day` that demo.tutor is available for. */
async function availableHour(
  page: Page,
  tutorId: string,
  day: string,
): Promise<number> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const token = csrfTokenOf(me);
  const read = await api(page, `/api/v1/tutors/${tutorId}/availability`);
  expect(read.status).toBe(200);
  const availability = read.body as Availability;
  // A rerun on the same database: time off first, never needs approval.
  for (const item of availability.exceptions
    .filter((exception) => exception.starts_on === day)
    .sort(
      (a, b) =>
        Number(b.kind === 'unavailable') - Number(a.kind === 'unavailable'),
    )) {
    await api(
      page,
      `/api/v1/tutors/${tutorId}/availability/exceptions/${item.id}`,
      { method: 'DELETE', headers: { 'X-CSRF-Token': token } },
    );
  }
  const thursdays = availability.weekly.filter((w) => w.weekday === THURSDAY);
  const candidates = [10, 11, 12, 13, 14, 15, 16, 17, 18, 9];
  const covered = candidates.find((hour) =>
    thursdays.some(
      (w) => w.starts_at <= hhmm(hour) && w.ends_at >= hhmm(hour + 1),
    ),
  );
  if (covered !== undefined) {
    return covered;
  }
  const free = candidates.find((hour) =>
    thursdays.every(
      (w) => w.ends_at <= hhmm(hour) || w.starts_at >= hhmm(hour + 1),
    ),
  );
  expect(free, 'a Thursday hour outside the weekly windows').toBeDefined();
  const extra = await api(
    page,
    `/api/v1/tutors/${tutorId}/availability/exceptions`,
    {
      method: 'POST',
      json: {
        kind: 'available',
        starts_on: day,
        ends_on: day,
        starts_at: hhmm(free!),
        ends_at: hhmm(free! + 1),
      },
      headers: { 'X-CSRF-Token': token },
    },
  );
  expect(extra.status, JSON.stringify(extra.body)).toBe(201);
  return free!;
}

test('General is preselected and needs no qualification', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `General ${suffix}`;
  const studentName = `Elisa ${familyNames}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const fields = common.scheduling.fields;
  const field = (label: string) => main.getByLabel(label, { exact: true });

  // 1. The data, through the API.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Elisa',
    familyNames,
  });
  const found = await api(
    page,
    `/api/v1/tutors?q=${encodeURIComponent(DEMO_TUTOR)}`,
  );
  expect(found.status).toBe(200);
  const tutor = (
    found.body as {
      items: { user_id: string; username: string; display_name: string }[];
    }
  ).items.find((item) => item.username === DEMO_TUTOR);
  expect(tutor).toBeDefined();
  const day = thursdayAhead();
  const hour = await availableHour(page, tutor!.user_id, day);

  // 2. The form opens with General selected.
  await page.goto('/schedule/new');
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();
  const subject = field(fields.subject);
  await expect(subject.locator('option:checked')).toHaveText(GENERAL);

  // 3. An unqualified tutor: created at once, no conflict panel.
  await pickOption(main, fields.student, familyNames, studentName);
  await field(fields.tutor).selectOption({ label: tutor!.display_name });
  await field(fields.date).fill(day);
  await fillTime(field(fields.start), hhmm(hour));
  await fillTime(field(fields.end), hhmm(hour + 1));
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();
  await expect(main.getByRole('alert')).toHaveCount(0);

  // 4. On the calendar's week of D, named after General.
  await page.goto(`/schedule?view=week&date=${day}&student=${studentId}`);
  const calendar = main.getByRole('region', {
    name: common.scheduling.calendar.regionLabel,
  });
  await expect(calendar).toHaveAttribute('aria-busy', 'false');
  await expect(
    calendar.getByRole('button', { name: new RegExp(`^${GENERAL}, `) }),
  ).toHaveCount(1);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
