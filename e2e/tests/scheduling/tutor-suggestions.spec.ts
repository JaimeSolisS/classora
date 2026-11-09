import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { pickOption } from '../support/combobox';
import { CENTER_ZONE } from '../support/scheduling';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Tutor suggestions while scheduling, and subjects on tutor profiles
// (HF-05b E4). With the shared Owner session:
// 1. through the API: a subject (unique suffix) and a student; demo.tutor's
//    id (GET /tutors?q=demo.tutor); D = the first Monday at least 9 weeks
//    ahead (no other spec uses it); a 1-hour time on Monday that none of
//    demo.tutor's weekly windows covers, and an `available` exception on D
//    for that time (adding availability never needs approval);
// 2. on demo.tutor's profile, "Asignar materia" grants the subject: the row
//    reads Aprobada (axe with the dialog open);
// 3. on /tutors, demo.tutor's Materias cell holds the subject;
// 4. on /schedule/new, the student, subject, D and the time, without
//    touching Tutor: the panel lists demo.tutor under the summary line;
//    "Elegir" sets Tutor, the row reads Elegido and the availability line
//    shows (axe);
// 5. "Se repite cada semana" with 2 sessions: nobody can (the exception
//    covers D only, so demo.tutor isn't available on D + 7); switched off;
// 6. "Buscar horarios libres" shows "Otros horarios libres de …" with slots
//    or the empty text (other specs may change the tutor's other windows,
//    so no count);
// 7. "Programar sesión" shows the success panel.
// CSP is clean. No fixed sleeps.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 0 logins, 0 redemptions and
// 0 registrations (two API creates, one exception, one grant and one
// session, all with the shared Owner session). Each `make e2e` run starts
// from a fresh database; the exceptions on D are deleted first, so a rerun
// on the same database starts clean too.

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const DEMO_TUTOR = 'demo.tutor';

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** The first Monday at least 63 days after today (center zone). */
function mondayAhead(): string {
  const [year, month, day] = new Intl.DateTimeFormat('en-CA', {
    timeZone: CENTER_ZONE,
  })
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 63, 12));
  while (date.getUTCDay() !== 1) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

const hhmm = (hour: number) => `${String(hour).padStart(2, '0')}:00`;

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

type Availability = {
  weekly: { weekday: number; starts_at: string; ends_at: string }[];
  exceptions: { id: string; kind: string; starts_on: string }[];
};

test('suggested tutors follow the subject and time, and a granted subject', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Sugerencia ${suffix}`;
  const studentName = `Iris ${familyNames}`;
  const subjectName = `Materia Sugerida ${suffix}`;
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
  const suggestions = common.scheduling.tutorSuggestions;
  const grant = common.tutors.qualifications.grant;

  // 1. The data, through the API.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createStudentViaApi(page, { givenNames: 'Iris', familyNames });
  await createSubjectViaApi(page, 'Primaria', subjectName);
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
  const tutorId = tutor!.user_id;
  const tutorName = tutor!.display_name;
  const day = mondayAhead();
  const read = await api(page, `/api/v1/tutors/${tutorId}/availability`);
  expect(read.status).toBe(200);
  const availability = read.body as Availability;
  const token = await csrf(page);
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
  // A Monday hour no weekly window touches, inside usual opening hours.
  const mondays = availability.weekly.filter((w) => w.weekday === 1);
  const hour = [10, 11, 12, 13, 14, 15, 16, 9, 8, 17, 18, 19].find(
    (candidate) =>
      mondays.every(
        (w) =>
          w.ends_at <= hhmm(candidate) || w.starts_at >= hhmm(candidate + 1),
      ),
  );
  expect(hour, 'a Monday hour outside the weekly windows').toBeDefined();
  const start = hhmm(hour!);
  const end = hhmm(hour! + 1);
  const extra = await api(
    page,
    `/api/v1/tutors/${tutorId}/availability/exceptions`,
    {
      method: 'POST',
      json: {
        kind: 'available',
        starts_on: day,
        ends_on: day,
        starts_at: start,
        ends_at: end,
      },
      headers: { 'X-CSRF-Token': token },
    },
  );
  expect(extra.status, JSON.stringify(extra.body)).toBe(201);

  // 2. Grant the subject on the profile.
  await page.goto(`/tutors/${tutorId}`);
  const qualifications = page.locator('#qualifications');
  await qualifications
    .getByRole('button', { name: grant.button, exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(
    dialog.getByRole('heading', { name: grant.title }),
  ).toBeVisible();
  await dialog
    .getByLabel(common.tutors.qualifications.levelLabel, { exact: true })
    .selectOption({ label: 'Primaria' });
  const subjectSelect = dialog.getByLabel(
    common.tutors.qualifications.subjectLabel,
    { exact: true },
  );
  await expect(
    subjectSelect.getByRole('option', { name: subjectName, exact: true }),
  ).toBeAttached();
  await subjectSelect.selectOption({ label: subjectName });
  expect(await axeViolations(page)).toEqual([]);
  await dialog.getByRole('button', { name: grant.submit, exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(
    qualifications.getByRole('row').filter({ hasText: subjectName }),
  ).toContainText(common.tutors.qualifications.statuses.approved);

  // 3. The tutor list's Materias column.
  await page.goto(`/tutors?q=${encodeURIComponent(DEMO_TUTOR)}`);
  // The exact username cell: other demo tutors' usernames contain
  // "demo.tutor" as a substring.
  const row = main.getByRole('row').filter({
    has: page.getByRole('cell', { name: DEMO_TUTOR, exact: true }),
  });
  await expect(row).toContainText(subjectName);

  // 4. The panel loads by itself; choosing keeps the list.
  await page.goto('/schedule/new');
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();
  await pickOption(main, fields.student, familyNames, studentName);
  await field(fields.subject).selectOption({ label: subjectName });
  await field(fields.date).fill(day);
  await field(fields.start).fill(start);
  await field(fields.end).fill(end);
  const panel = main.getByRole('region', { name: suggestions.heading });
  // The summary line up to the date (its format is the app's).
  await expect(panel).toContainText(
    fill(suggestions.summary, {
      subject: subjectName,
      date: '\u0000',
      start,
      end,
    })
      .split('\u0000')[0]!
      .trim(),
  );
  // The exact name, not a substring ("Demo Tutor 2" would match too).
  const tutorRow = panel.getByRole('listitem').filter({
    has: page.getByText(tutorName, { exact: true }),
  });
  await expect(tutorRow).toBeVisible();
  await expect(field(fields.tutor)).toHaveValue('');
  await tutorRow
    .getByRole('button', {
      name: fill(suggestions.chooseTutor, { name: tutorName }),
    })
    .click();
  await expect(field(fields.tutor)).toHaveValue(tutorId);
  await expect(
    tutorRow.getByRole('button', {
      name: fill(suggestions.chosenTutor, { name: tutorName }),
    }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    main.getByText(common.scheduling.tutorAvailability.exceptions),
  ).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);

  // 5. A weekly series of 2: D + 7 has no exception, so nobody can.
  const series = common.scheduling.series;
  await main.getByRole('switch', { name: series.repeats }).check();
  await main.getByRole('radio', { name: series.endsOptions.count }).check();
  await field(series.count).fill('2');
  await expect(panel).toContainText(
    fill(suggestions.unavailablePart_one, { count: 1 }),
  );
  await expect(panel).toContainText(
    fill(suggestions.inSeries_other, { text: '', count: 2 }).trim(),
  );
  await main.getByRole('switch', { name: series.repeats }).uncheck();
  await expect(tutorRow).toBeVisible();

  // 6. The chosen tutor's other free times.
  await main
    .getByRole('button', { name: common.scheduling.freeTimes.button })
    .click();
  const freeTimes = main.getByRole('region', {
    name: fill(common.scheduling.freeTimes.heading, { name: tutorName }),
  });
  await expect(freeTimes).toBeVisible();
  await expect(
    freeTimes
      .getByRole('listitem')
      .first()
      .or(
        freeTimes.getByText(
          fill(common.scheduling.freeTimes.empty, { name: tutorName }),
        ),
      ),
  ).toBeVisible();

  // 7. Schedule.
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
