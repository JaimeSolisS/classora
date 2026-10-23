import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import {
  createClassroomViaApi,
  createSubjectViaApi,
  inviteTutorViaApi,
} from '../support/tutors';

// Tutor availability (#45 G1). The demo Owner (the shared state; tutor.manage)
// invites a tutor through the API, opens their profile, sets Monday
// 16:00–20:00 and Wednesday 16:00–18:00 (saved), adds time off for a date
// range (listed), deletes it (gone), and reloads: the windows persist.
// #197 D1: it also sets a daily limit of 4 h, a weekly limit of 20 h, a
// 15 min break and a preferred classroom created through the API (saved,
// and shown after the reload).
//
// #63 I6 (second test): with the shared Owner session, `demo.tutor` (the
// #195 account: active, so a session can be offered to them) gets extra
// hours on a Tuesday about nine weeks ahead and a session offered there
// (API; overrides for the tutor's missing qualification). In the UI the
// Owner adds time off over that day: "Enviado para aprobación" and the
// pending change (axe on the section); in Approvals the decision dialog
// lists the affected class, the Owner approves; the session page shows
// "Sin asignar" and "Requiere reasignación". Both exceptions are deleted
// at the end (API), so a rerun starts clean. No other spec uses that date.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (an invitation, a classroom, a student, a subject and a
// session, with the shared Owner session).

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
  const roomName = `E2E Aula ${suffix}`;
  await createClassroomViaApi(page, roomName);
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
    .getByRole('button', { name: common.tutors.availability.save, exact: true })
    .click();
  await expect(
    section
      .getByRole('status')
      .filter({ hasText: common.tutors.availability.saved }),
  ).toBeVisible();

  // 2. Teaching limits and a preferred classroom (#197): saved.
  const settings = common.tutors.availability.settings;
  const limit = (name: string) => section.getByRole('group', { name });
  const daily = limit(settings.daily);
  await daily.getByLabel(settings.noLimit, { exact: true }).uncheck();
  await daily.getByLabel(settings.hoursLabel, { exact: true }).fill('4');
  const weekly = limit(settings.weekly);
  await weekly.getByLabel(settings.noLimit, { exact: true }).uncheck();
  await weekly.getByLabel(settings.hoursLabel, { exact: true }).fill('20');
  const pause = limit(settings.break);
  await pause.getByLabel(settings.noBreak, { exact: true }).uncheck();
  await pause.getByLabel(settings.minutesLabel, { exact: true }).fill('15');
  await section.getByLabel(roomName, { exact: true }).check();
  await section
    .getByRole('button', { name: settings.save, exact: true })
    .click();
  await expect(
    section.getByRole('status').filter({ hasText: settings.saved }),
  ).toBeVisible();

  // 3. Time off for a date range: listed.
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

  // 4. Delete it: gone.
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

  // 5. Reload: the windows, limits and classroom persist.
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
  const after = (name: string) =>
    availability(page).getByRole('group', { name });
  await expect(
    after(settings.daily).getByLabel(settings.hoursLabel, { exact: true }),
  ).toHaveValue('4');
  await expect(
    after(settings.weekly).getByLabel(settings.hoursLabel, { exact: true }),
  ).toHaveValue('20');
  await expect(
    after(settings.break).getByLabel(settings.minutesLabel, { exact: true }),
  ).toHaveValue('15');
  await expect(
    availability(page).getByLabel(roomName, { exact: true }),
  ).toBeChecked();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const ZONE = 'America/Mexico_City';
const DEMO_TUTOR = 'Demo Tutor';
const OVERRIDE_CODES = [
  'tutor_not_qualified',
  'outside_operating_hours',
  'center_closed',
];

/** The Tuesday at least 63 days after today (center zone), YYYY-MM-DD. */
function tuesdayAhead(): string {
  const [year, month, day] = new Intl.DateTimeFormat('en-CA', {
    timeZone: ZONE,
  })
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 63, 12));
  while (date.getUTCDay() !== 2) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

async function demoTutorId(page: Page): Promise<string> {
  const options = await api(
    page,
    `/api/v1/schedule/tutor-options?q=${encodeURIComponent(DEMO_TUTOR)}`,
  );
  expect(options.status).toBe(200);
  const items = (
    options.body as { items: { id: string; display_name: string }[] }
  ).items;
  const found = items.find((item) => item.display_name === DEMO_TUTOR);
  expect(found).toBeDefined();
  return found!.id;
}

/** Deletes the tutor's exceptions on `date`, time off first (deleting it
 * never needs approval; then the extra hours cover no offered or confirmed
 * class). Best effort: it only tidies up for a rerun. */
async function deleteExceptionsOn(page: Page, tutorId: string, date: string) {
  const read = await api(page, `/api/v1/tutors/${tutorId}/availability`);
  if (read.status !== 200) {
    return;
  }
  const exceptions = (
    read.body as {
      exceptions: { id: string; kind: string; starts_on: string }[];
    }
  ).exceptions
    .filter((item) => item.starts_on === date)
    .sort(
      (a, b) =>
        Number(b.kind === 'unavailable') - Number(a.kind === 'unavailable'),
    );
  const token = await csrf(page);
  for (const item of exceptions) {
    await api(
      page,
      `/api/v1/tutors/${tutorId}/availability/exceptions/${item.id}`,
      { method: 'DELETE', headers: { 'X-CSRF-Token': token } },
    );
  }
}

test('time off over a scheduled class waits for approval (#63)', async ({
  page,
}, testInfo) => {
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

  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const tutorId = await demoTutorId(page);
  const date = tuesdayAhead();
  const subject = `E2E Disponibilidad ${suffix}`;
  const subjectId = await createSubjectViaApi(page, 'Primaria', subject);
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Aprobación',
    familyNames: `E2E ${suffix}`,
  });
  await deleteExceptionsOn(page, tutorId, date);
  try {
    // Extra hours that day, and a class offered in them.
    const extra = await api(
      page,
      `/api/v1/tutors/${tutorId}/availability/exceptions`,
      {
        method: 'POST',
        json: {
          kind: 'available',
          starts_on: date,
          ends_on: date,
          starts_at: '16:00',
          ends_at: '18:00',
        },
        headers: { 'X-CSRF-Token': await csrf(page) },
      },
    );
    expect(extra.status, JSON.stringify(extra.body)).toBe(201);
    const created = await api(page, '/api/v1/class-sessions', {
      method: 'POST',
      json: {
        kind: 'individual',
        student_id: studentId,
        subject_id: subjectId,
        tutor_user_id: tutorId,
        starts_at: `${date}T16:00:00-06:00`,
        ends_at: `${date}T17:00:00-06:00`,
        override: {
          codes: OVERRIDE_CODES,
          reason: 'Prueba E2E de disponibilidad: sin calificación.',
        },
      },
      headers: { 'X-CSRF-Token': await csrf(page) },
    });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    const sessionId = (created.body as { id: string }).id;

    // 1. Time off over that day: sent for approval.
    await page.goto(`/tutors/${tutorId}`);
    const section = availability(page);
    await section
      .getByRole('button', { name: common.tutors.availability.add })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByLabel(common.tutors.availability.startsOn, { exact: true })
      .fill(date);
    await dialog
      .getByLabel(common.tutors.availability.endsOn, { exact: true })
      .fill(date);
    await dialog
      .getByRole('button', { name: common.tutors.availability.send })
      .click();
    await expect(dialog).toBeHidden();
    await expect(
      section
        .getByRole('status')
        .filter({ hasText: common.tutors.availability.changes.sent_one }),
    ).toBeVisible();
    // The newest pending change is last (oldest first).
    const pending = section
      .getByRole('list', {
        name: common.tutors.availability.changes.pendingTitle,
      })
      .locator('[data-change-request-id]')
      .last();
    await expect(pending).toBeVisible();
    const change = (await pending.locator('p').first().textContent()) ?? '';
    const axe = await new AxeBuilder({ page })
      .include('#availability')
      .withTags(WCAG_TAGS)
      .analyze();
    expect(axe.violations.map((v) => v.id)).toEqual([]);

    // 2. Approvals: the affected class, approved.
    await page.goto('/approvals?type=staff.availability_change');
    const row = page.getByRole('row').filter({ hasText: change });
    await row
      .getByRole('button', {
        name: new RegExp(`^${common.approvals.actions.approve}`),
      })
      .click();
    const decision = page.getByRole('dialog');
    await expect(
      decision.getByText(common.approvals.availabilityChange.affectedTitle),
    ).toBeVisible();
    await expect(decision.getByText(subject, { exact: false })).toBeVisible();
    await decision
      .getByRole('button', {
        name: common.approvals.actions.approve,
        exact: true,
      })
      .click();
    await expect(decision).toBeHidden();

    // 3. The session: unassigned, needs reassignment.
    await page.goto(`/schedule/sessions/${sessionId}`);
    await expect(
      page.getByRole('heading', {
        name: common.scheduling.session.reassignment.title,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(common.scheduling.states.unassigned).first(),
    ).toBeVisible();
  } finally {
    await deleteExceptionsOn(page, tutorId, date);
  }

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
