import AxeBuilder from '@axe-core/playwright';
import {
  expect,
  test,
  type Browser,
  type Page,
  type TestInfo,
} from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, fill, signedInAs } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createApprovedTutor, createSubjectViaApi } from '../support/tutors';

// Assignment offers (#52 I6). The demo Owner (the shared state;
// schedule.manage, schedule.assign and schedule.override) creates a student
// and two subjects through the API and two sessions offered to `demo.tutor`
// (the #195 demo account), on the first weekday at least 21 days ahead,
// 16:00–17:00 and 17:00–18:00, with an override for the tutor's missing
// availability and qualification (so no setup of either is needed). The
// tutor signs in, the bell has the offers, "Mis ofertas" lists them; the
// tutor accepts the first (it moves to "Respondidas") and declines the
// second with a note. The Owner opens Approvals, finds the reassignment
// item of the declined session, follows its link to the session page and
// sees "Sin asignar" and the note in the history. Axe on /offers and the
// session page.
//
// Series offers (#233 I6), in the same test and logins: the signed-in tutor
// requests a qualification for a third subject and sets a weekly window
// through the API; the Owner approves the qualification and creates a
// tutorless weekly series of two sessions (six weeks ahead, 16:00–17:00)
// through the API, then opens its first session's page and offers "Esta y
// las siguientes sesiones" to the tutor in the UI. The tutor sees the series
// card in "Mis ofertas" ("2 sesiones programadas…"; axe) and accepts it;
// both sessions are then confirmed (API). Series offers take no override,
// so the tutor's availability and qualification are real here.
//
// Substitution and cancellation (#55 I7), in the same test: a second
// active tutor (createApprovedTutor: a self-registration the Owner approves,
// then a role change in the Owner's own reauthenticated API context; the
// substitute never signs in). On the accepted session's page the Owner
// finds a substitute, overrides the substitute's missing availability and
// qualification, sees "Suplencia pendiente", withdraws it (the history shows
// "Retirada") and cancels the session with a reason ("Cancelada" banner).
// The signed-in tutor's bell then has the cancellation. Axe on the page with
// the substitute panel and with the cancel dialog.
//
// Attendance (#76 H9), in the same test and logins: `run.sh` inserts a
// started confirmed group session of demo.tutor with two students (the
// subject "E2E Attendance"; tests.factories.e2e_attendance). The signed-in
// tutor, on a phone (390×844), finds it through their schedule API, opens
// it from My schedule's list, "Tomar asistencia", marks the first student
// Presente and the second Retardo, saves and sees both after a reload (axe).
// The Owner (shared session) corrects the second to Ausente with a reason
// on the session page and sees the "Corregida" badge and the history (axe).
//
// Budgets (#27, ADR-0008; see support/auth.ts): 2 logins (demo.tutor, and
// the reauthenticated Owner context of createApprovedTutor, #55), no
// one-time-link redemption, 1 registration (the substitute, #55); the
// attendance steps add none (#76). It never
// changes the zone, the
// opening hours or the scheduling policy. No other spec offers sessions to
// demo.tutor; the dates are three and six weeks ahead (other scheduling
// specs use the first weekday two weeks ahead, 10:00–12:00).

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 150_000;
const TUTOR = 'demo.tutor';
const TUTOR_NAME = 'Demo Tutor';
const TUTOR_PASSWORD = 'classora-demo-tutor';
const LEVEL = 'Primaria';
/** The subject of the session `run.sh` inserts for attendance (#76). */
const ATTENDANCE_SUBJECT = 'E2E Attendance';
/** Every soft rule the demo tutor can break here (unlisted ones are ignored). */
const OVERRIDE_CODES = [
  'tutor_unavailable',
  'tutor_not_qualified',
  'outside_operating_hours',
  'center_closed',
];

/** The first Monday-Friday at least `days` days after today, center zone. */
function firstWeekdayAhead(days: number): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + days, 12));
  while (date.getUTCDay() === 0 || date.getUTCDay() === 6) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

// Chromium logs every 4xx resource as a console error: the tutor's signed-out
// /auth/me (401) before the login is expected.
const SIGNED_OUT_ME =
  /Failed to load resource: the server responded with a status of 401/;

function watchConsole(page: Page, expected?: RegExp): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (
      message.type() === 'error' &&
      !(expected !== undefined && expected.test(message.text()))
    ) {
      errors.push(message.text());
    }
  });
  return errors;
}

async function demoTutorId(page: Page): Promise<string> {
  const options = await api(
    page,
    `/api/v1/schedule/tutor-options?q=${encodeURIComponent(TUTOR_NAME)}`,
  );
  expect(options.status).toBe(200);
  const items = (
    options.body as { items: { id: string; display_name: string }[] }
  ).items;
  const found = items.find((item) => item.display_name === TUTOR_NAME);
  expect(found).toBeDefined();
  return found!.id;
}

/** One session offered to the tutor (Mexico City: always -06:00). */
async function offerViaApi(
  page: Page,
  values: {
    studentId: string;
    subjectId: string;
    tutorId: string;
    date: string;
    start: string;
    end: string;
  },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/class-sessions', {
    method: 'POST',
    json: {
      kind: 'individual',
      student_id: values.studentId,
      subject_id: values.subjectId,
      tutor_user_id: values.tutorId,
      starts_at: `${values.date}T${values.start}:00-06:00`,
      ends_at: `${values.date}T${values.end}:00-06:00`,
      override: {
        codes: OVERRIDE_CODES,
        reason: 'Prueba E2E de ofertas: sin disponibilidad ni calificación.',
      },
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const body = created.body as { id: string; state: string };
  expect(body.state).toBe('offered');
  return body.id;
}

interface AttendanceSession {
  id: string;
  startsAt: string;
  students: string[];
}

/** The started session `run.sh` inserted for demo.tutor (#76 H9). */
async function attendanceSession(
  page: Page,
  tutorId: string,
): Promise<AttendanceSession> {
  const to = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const from = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
  const read = await api(
    page,
    `/api/v1/tutors/${tutorId}/schedule?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
  );
  expect(read.status, JSON.stringify(read.body)).toBe(200);
  const items = (
    read.body as {
      items: {
        id: string;
        starts_at: string;
        subject: { name: string };
        participants: { display_name: string }[];
      }[];
    }
  ).items.filter((item) => item.subject.name === ATTENDANCE_SUBJECT);
  expect(items).toHaveLength(1);
  const [item] = items;
  return {
    id: item!.id,
    startsAt: item!.starts_at,
    students: item!.participants.map((p) => p.display_name),
  };
}

/** The local date (YYYY-MM-DD) of an instant in the center zone. */
function localDateOf(instant: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(
    new Date(instant),
  );
}

async function signInTutor(browser: Browser) {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const consoleErrors = watchConsole(page, SIGNED_OUT_ME);
  await page.goto('/login');
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(TUTOR);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(TUTOR_PASSWORD);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, TUTOR_NAME)),
  ).toBeVisible();
  await acceptPrivacyNoticeIfShown(page);
  return { context, page, consoleErrors };
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// The Owner's substitution is first refused with a 409 (the conflict panel),
// which Chromium logs as a failed resource.
const CONFLICT_409 =
  /Failed to load resource: the server responded with a status of 409/;

/** The text of a message template before its first placeholder. */
const textBefore = (template: string) => template.split('{{')[0]!;

/** The ISO weekday (1 = Monday) of a YYYY-MM-DD date. */
function isoWeekday(date: string): number {
  const day = new Date(`${date}T12:00:00Z`).getUTCDay();
  return day === 0 ? 7 : day;
}

async function csrfOf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

/**
 * The tutor (their own page) asks to teach `subjectId` and makes sure a
 * weekly 16:00–17:00 window exists on `weekday`; the Owner approves the
 * qualification.
 */
async function qualifyAndMakeAvailable(
  owner: Page,
  tutor: Page,
  values: {
    tutorId: string;
    subjectId: string;
    subjectName: string;
    weekday: number;
  },
): Promise<void> {
  const requested = await api(
    tutor,
    `/api/v1/tutors/${values.tutorId}/qualifications`,
    {
      method: 'POST',
      json: { subject_id: values.subjectId },
      headers: { 'X-CSRF-Token': await csrfOf(tutor) },
    },
  );
  expect(requested.status, JSON.stringify(requested.body)).toBe(201);
  const current = await api(
    tutor,
    `/api/v1/tutors/${values.tutorId}/availability`,
  );
  expect(current.status).toBe(200);
  const availability = current.body as {
    version: number;
    weekly: { weekday: number; starts_at: string; ends_at: string }[];
  };
  const window = {
    weekday: values.weekday,
    starts_at: '16:00',
    ends_at: '17:00',
  };
  const has = availability.weekly.some(
    (w) =>
      w.weekday === window.weekday &&
      w.starts_at <= window.starts_at &&
      w.ends_at >= window.ends_at,
  );
  if (!has) {
    const others = availability.weekly.filter(
      (w) =>
        w.weekday !== window.weekday ||
        w.ends_at <= window.starts_at ||
        w.starts_at >= window.ends_at,
    );
    const saved = await api(
      tutor,
      `/api/v1/tutors/${values.tutorId}/availability/weekly`,
      {
        method: 'PUT',
        json: { version: availability.version, windows: [...others, window] },
        headers: { 'X-CSRF-Token': await csrfOf(tutor) },
      },
    );
    expect(saved.status, JSON.stringify(saved.body)).toBe(200);
  }
  const pending = await api(
    owner,
    '/api/v1/approval-requests?request_type=staff.qualification&status=pending&limit=100',
  );
  expect(pending.status).toBe(200);
  const item = (
    pending.body as {
      items: { id: string; subject: { subject_name?: string } }[];
    }
  ).items.find((row) => row.subject.subject_name === values.subjectName);
  expect(item).toBeTruthy();
  const approved = await api(
    owner,
    `/api/v1/approval-requests/${item!.id}/approve`,
    { method: 'POST', headers: { 'X-CSRF-Token': await csrfOf(owner) } },
  );
  expect(approved.status, JSON.stringify(approved.body)).toBe(200);
}

/** A tutorless weekly series of two sessions, 16:00–17:00 from `date`. */
async function createSeriesViaApi(
  page: Page,
  values: { studentId: string; subjectId: string; date: string },
): Promise<{ id: string; occurrences: { id: string }[] }> {
  const created = await api(page, '/api/v1/class-series', {
    method: 'POST',
    json: {
      kind: 'individual',
      student_id: values.studentId,
      subject_id: values.subjectId,
      starts_on: values.date,
      start_time: '16:00',
      duration_minutes: 60,
      recurrence: { weekdays: [isoWeekday(values.date)], count: 2 },
    },
    headers: { 'X-CSRF-Token': await csrfOf(page) },
  });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const body = created.body as { id: string; occurrences: { id: string }[] };
  expect(body.occurrences).toHaveLength(2);
  return body;
}

test('a tutor accepts one offer and declines another; staff see the reassignment', async ({
  page,
  browser,
  playwright,
  baseURL,
}, testInfo: TestInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Oferta ${suffix}`;
  const accepted = `Aceptada E2E ${suffix}`;
  const declined = `Rechazada E2E ${suffix}`;
  const weekly = `Serie E2E ${suffix}`;
  const note = 'Ese día tengo otra clase.';
  const date = firstWeekdayAhead(21 + 7 * testInfo.retry);
  // Six weeks ahead (and never on a retry's one-off date).
  const seriesDate = firstWeekdayAhead(42 + 14 * testInfo.retry);
  await recordCspViolations(page);
  const ownerErrors = watchConsole(page, CONFLICT_409);

  // 1. Setup through the API with the shared Owner session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Lucía',
    familyNames,
  });
  const acceptedId = await createSubjectViaApi(page, LEVEL, accepted);
  const declinedId = await createSubjectViaApi(page, LEVEL, declined);
  const weeklyId = await createSubjectViaApi(page, LEVEL, weekly);
  const tutorId = await demoTutorId(page);
  const acceptedSession = await offerViaApi(page, {
    studentId,
    subjectId: acceptedId,
    tutorId,
    date,
    start: '16:00',
    end: '17:00',
  });
  const declinedSession = await offerViaApi(page, {
    studentId,
    subjectId: declinedId,
    tutorId,
    date,
    start: '17:00',
    end: '18:00',
  });

  // 2. The tutor signs in: the bell has the offers.
  const tutor = await signInTutor(browser);
  try {
    const [before, after] = common.notifications.bellUnread.split('{{unread}}');
    const bell = tutor.page.getByRole('banner').getByRole('button', {
      name: new RegExp(`^${escape(before!)}[1-9]\\d*${escape(after!)}$`),
    });
    await expect(bell).toBeVisible();
    await bell.click();
    const panel = tutor.page.getByRole('dialog', {
      name: common.notifications.title,
    });
    await expect(panel).toContainText(accepted);
    await expect(panel).toContainText(declined);
    await tutor.page.keyboard.press('Escape');
    await expect(panel).toBeHidden();

    // 3. My offers: accept the first; it moves to Answered.
    await tutor.page
      .getByRole('navigation')
      .getByRole('link', { name: common.nav.offers, exact: true })
      .click();
    const main = tutor.page.getByRole('main');
    await expect(
      main.getByRole('heading', { level: 1, name: common.offers.title }),
    ).toBeVisible();
    const card = (subject: string) =>
      main.getByRole('article').filter({ hasText: subject });
    await expect(card(accepted)).toBeVisible();
    await expect(card(declined)).toBeVisible();
    expect(await axeViolations(tutor.page)).toEqual([]);

    await card(accepted)
      .getByRole('button', { name: common.offers.accept.action })
      .click();
    const confirm = tutor.page.getByRole('dialog', {
      name: common.offers.accept.title,
    });
    await confirm
      .getByRole('button', { name: common.offers.accept.submit, exact: true })
      .click();
    await expect(confirm).toBeHidden();
    await expect(card(accepted)).toBeHidden();

    // 4. Decline the second with a note.
    await card(declined)
      .getByRole('button', { name: common.offers.decline.action })
      .click();
    const decline = tutor.page.getByRole('dialog', {
      name: common.offers.decline.title,
    });
    expect(await axeViolations(tutor.page)).toEqual([]);
    await decline
      .getByLabel(common.offers.decline.note, { exact: true })
      .fill(note);
    await decline
      .getByRole('button', { name: common.offers.decline.submit, exact: true })
      .click();
    await expect(decline).toBeHidden();
    await expect(card(declined)).toBeHidden();

    await main
      .getByRole('tab', { name: common.offers.tabs.answered, exact: true })
      .click();
    await expect(card(accepted)).toContainText(common.offers.status.accepted);
    await expect(card(declined)).toContainText(common.offers.status.declined);
    await expect(card(declined)).toContainText(
      fill(common.offers.card.note, { note }),
    );

    // 5. A weekly series: the Owner offers "this and following sessions"
    // from its first session's page; the tutor accepts the series card.
    await qualifyAndMakeAvailable(page, tutor.page, {
      tutorId,
      subjectId: weeklyId,
      subjectName: weekly,
      weekday: isoWeekday(seriesDate),
    });
    const series = await createSeriesViaApi(page, {
      studentId,
      subjectId: weeklyId,
      date: seriesDate,
    });
    await page.goto(`/schedule/sessions/${series.occurrences[0]!.id}`);
    const ownerPanel = page.getByRole('main');
    await ownerPanel
      .getByRole('radio', { name: common.scheduling.offer.series.series })
      .check();
    await ownerPanel
      .getByLabel(common.scheduling.fields.tutor, { exact: true })
      .selectOption({ label: TUTOR_NAME });
    await ownerPanel
      .getByRole('button', { name: common.scheduling.offer.submit })
      .click();
    await expect(ownerPanel).toContainText(
      fill(common.scheduling.session.seriesOfferedTo, { tutor: TUTOR_NAME }),
    );

    await tutor.page.goto('/offers');
    await expect(card(weekly)).toContainText(
      fill(common.offers.series.scheduled_other, { count: 2 }),
    );
    expect(await axeViolations(tutor.page)).toEqual([]);
    await card(weekly)
      .getByRole('button', { name: common.offers.accept.action })
      .click();
    const confirmSeries = tutor.page.getByRole('dialog', {
      name: common.offers.series.acceptTitle,
    });
    await confirmSeries
      .getByRole('button', { name: common.offers.accept.submit, exact: true })
      .click();
    await expect(confirmSeries).toBeHidden();
    await expect(card(weekly)).toBeHidden();
    const read = await api(page, `/api/v1/class-series/${series.id}`);
    expect(read.status).toBe(200);
    const detail = read.body as {
      assignment: { status: string } | null;
      occurrences: { state: string }[];
    };
    expect(detail.assignment?.status).toBe('accepted');
    expect(detail.occurrences.map((o) => o.state)).toEqual([
      'confirmed',
      'confirmed',
    ]);

    // 6. Substitution and cancellation (#55 I7) of the accepted session.
    const substituteName = `Suplente ${suffix}`;
    await createApprovedTutor(page, playwright.request, baseURL!, {
      username: `e2e.substitute.${suffix}`,
      displayName: substituteName,
      password: 'e2e-substitute-password',
    });
    await page.goto(`/schedule/sessions/${acceptedSession}`);
    const sessionMain = page.getByRole('main');
    const substitute = sessionMain.getByRole('region', {
      name: common.scheduling.substitution.heading,
    });
    await substitute
      .getByLabel(common.scheduling.substitution.tutor, { exact: true })
      .selectOption({ label: substituteName });
    await substitute
      .getByLabel(common.scheduling.substitution.reason, { exact: true })
      .fill('La tutora tiene un compromiso ese día.');
    expect(await axeViolations(page)).toEqual([]);
    await substitute
      .getByRole('button', { name: common.scheduling.substitution.submit })
      .click();
    await substitute
      .getByRole('button', {
        name: common.scheduling.substitution.overrideAction,
      })
      .click();
    const override = page.getByRole('dialog', {
      name: common.scheduling.substitution.overrideAction,
    });
    await override
      .getByLabel(common.scheduling.override.reason, { exact: true })
      .fill('Suplencia urgente acordada con la familia.');
    await override
      .getByRole('button', {
        name: common.scheduling.substitution.overrideAction,
      })
      .click();
    await expect(override).toBeHidden();
    const substitutions = sessionMain.getByRole('region', {
      name: common.scheduling.substitution.historyHeading,
    });
    await expect(substitutions).toContainText(
      `${textBefore(common.scheduling.substitution.pendingText)}${substituteName}`,
    );
    await substitutions
      .getByRole('button', { name: common.scheduling.substitution.withdraw })
      .click();
    const withdraw = page.getByRole('dialog', {
      name: common.scheduling.substitution.withdrawTitle,
    });
    await withdraw
      .getByRole('button', { name: common.scheduling.substitution.withdraw })
      .click();
    await expect(withdraw).toBeHidden();
    await expect(substitutions).toContainText(common.offers.status.withdrawn);
    await expect(substitutions).not.toContainText(
      textBefore(common.scheduling.substitution.pendingText),
    );

    await sessionMain
      .getByRole('button', { name: common.scheduling.cancel.action })
      .click();
    const cancel = page.getByRole('dialog', {
      name: common.scheduling.cancel.title,
    });
    expect(await axeViolations(page)).toEqual([]);
    await cancel
      .getByLabel(common.scheduling.cancel.reason, { exact: true })
      .fill('Cierre por mantenimiento.');
    await cancel
      .getByRole('button', { name: common.scheduling.cancel.submit })
      .click();
    await expect(cancel).toBeHidden();
    await expect(
      sessionMain.getByRole('heading', {
        name: common.scheduling.cancel.banner,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      sessionMain.getByRole('button', {
        name: common.scheduling.cancel.action,
      }),
    ).toBeHidden();

    // The signed-in tutor's bell has the cancellation.
    await tutor.page.goto('/offers');
    await bell.click();
    await expect(panel).toContainText(
      textBefore(common.notifications.types.scheduling_session_cancelled),
    );
    await tutor.page.keyboard.press('Escape');

    // 7. Attendance (#76 H9): the tutor, on a phone, marks the started
    // session from My schedule.
    await tutor.page.setViewportSize({ width: 390, height: 844 });
    const lesson = await attendanceSession(tutor.page, tutorId);
    expect(lesson.students).toHaveLength(2);
    const [first, second] = lesson.students as [string, string];
    await tutor.page.goto(
      `/my-schedule?view=list&date=${localDateOf(lesson.startsAt)}`,
    );
    const calendar = tutor.page.getByRole('main').getByRole('region', {
      name: common.mySchedule.regionLabel,
    });
    await expect(calendar).toHaveAttribute('aria-busy', 'false');
    await calendar
      .getByRole('button', { name: new RegExp(escape(ATTENDANCE_SUBJECT)) })
      .click();
    await tutor.page
      .getByRole('dialog', { name: ATTENDANCE_SUBJECT })
      .getByRole('link', { name: common.attendance.take, exact: true })
      .click();
    await expect(tutor.page).toHaveURL(
      new RegExp(`/my-schedule/sessions/${lesson.id}/attendance$`),
    );
    const sheet = tutor.page.getByRole('main');
    const radio = (student: string, status: 'present' | 'late' | 'absent') =>
      sheet
        .getByRole('group', { name: student, exact: true })
        .getByRole('radio', {
          name: common.attendance.statuses[status],
          exact: true,
        });
    await radio(first, 'present').check();
    await radio(second, 'late').check();
    await sheet
      .getByRole('button', { name: common.attendance.save, exact: true })
      .click();
    await expect(sheet.getByText(common.attendance.saved)).toBeVisible();
    await tutor.page.reload();
    await expect(radio(first, 'present')).toBeChecked();
    await expect(radio(second, 'late')).toBeChecked();
    expect(await axeViolations(tutor.page)).toEqual([]);

    expect(await cspViolations(tutor.page)).toEqual([]);
    expect(tutor.consoleErrors).toEqual([]);
  } finally {
    await tutor.context.close();
  }

  // 6. The Owner: the reassignment item links to the session page.
  await page.goto('/approvals?type=scheduling.offer_reassignment');
  const ownerMain = page.getByRole('main');
  await expect(
    ownerMain.getByRole('heading', { level: 1, name: common.approvals.title }),
  ).toBeVisible();
  const row = ownerMain.getByRole('row').filter({ hasText: declined });
  await expect(row).toContainText(TUTOR_NAME);
  await row
    .getByRole('link', {
      name: common.approvals.reassignment.openSession,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/schedule/sessions/${declinedSession}$`),
  );
  await expect(
    ownerMain.getByRole('heading', {
      level: 1,
      name: common.scheduling.session.title,
    }),
  ).toBeVisible();
  await expect(ownerMain).toContainText(common.scheduling.states.unassigned!);
  const history = ownerMain.getByRole('region', {
    name: common.scheduling.session.history,
  });
  await expect(history).toContainText(common.offers.status.declined);
  await expect(history).toContainText(fill(common.offers.card.note, { note }));
  expect(await axeViolations(page)).toEqual([]);

  // 8. Attendance (#76 H9): the Owner corrects the second student.
  const lesson = await attendanceSession(page, tutorId);
  const second = lesson.students[1]!;
  const reason = 'Recepción confirmó que no llegó.';
  await page.goto(`/schedule/sessions/${lesson.id}`);
  const attendance = page.getByRole('main').getByRole('region', {
    name: common.attendance.section.heading,
  });
  await attendance
    .getByRole('button', {
      name: fill(common.attendance.section.correctFor, { name: second }),
    })
    .click();
  const correction = page.getByRole('dialog', {
    name: common.attendance.correctDialog.title,
  });
  await correction
    .getByLabel(common.attendance.correctDialog.status, { exact: true })
    .selectOption('absent');
  await correction
    .getByLabel(common.attendance.correctDialog.reason, { exact: true })
    .fill(reason);
  await correction
    .getByRole('button', {
      name: common.attendance.correctDialog.submit,
      exact: true,
    })
    .click();
  await expect(correction).toBeHidden();
  const attendanceRow = attendance.getByRole('row').filter({ hasText: second });
  await expect(attendanceRow).toContainText(
    common.attendance.section.correctedBadge,
  );
  await expect(attendanceRow).toContainText(common.attendance.statuses.absent);
  await attendanceRow
    .getByText(fill(common.attendance.history_one, { count: 1 }))
    .click();
  await expect(attendanceRow).toContainText(
    fill(common.attendance.reason, { reason }),
  );
  expect(await axeViolations(page)).toEqual([]);
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);
});
