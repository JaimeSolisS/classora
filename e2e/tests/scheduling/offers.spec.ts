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
// Closeout (#77 H8), in the same test and logins: `run.sh` also inserts a
// started confirmed individual session of demo.tutor with one student (the
// subject "E2E Closeout", before the attendance session). The tutor, at
// 390×844, opens it from My schedule, "Cerrar clase", marks the student
// Presente, sets 55 minutes, fills Topics, Performance and Next steps
// (axe on the summary step), submits and sees "Enviado a revisión", also
// after a reload (axe). Resubmission (#305 G3), still in the tutor's
// block: the Owner (shared session) requests changes with a reason on the
// session page; the tutor's bell has the message, the My schedule dialog
// shows "Cambios solicitados" and "Corregir cierre", the closeout page the
// reason (axe); "Editar y reenviar" (attendance editable), 58 minutes,
// Homework, "Reenviar" → "Cierre reenviado a revisión." and 58 of 60 min
// (axe). The Owner finds the item in the approval inbox ("Reenviado"),
// opens it with "Revisar" and sees the Closeout section with 58 of 60
// minutes (axe). Closeout review (#78 I9): the Owner corrects the minutes
// to 50 with a reason (the history shows 58 → 50), approves ("Aprobado ·
// 50 min", axe) and the inbox no longer lists it as pending.
//
// Homework (#313 G3), in the same test after the resubmission: the tutor
// opens the demo class U1 (the demo seed's confirmed class with
// demo.student) in My schedule, "Asignar tarea" (the class and the student
// prefilled), max 10, due tomorrow, axe on the form and the roster; a new
// demo.student context (desktop for the bell, then 390 px) follows the
// set-homework notification to the item, hands in a text and an https link
// (axe); the tutor's bell opens the roster (the link with rel noopener and
// nofollow), grades 8 with feedback (axe with the dialog open); the student
// reloads and sees "8.00 de 10.00 (80.00 %)" and no form, and its API gets
// 404 for the sibling's homework (U2) and 403 on the working list.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 3 logins (demo.tutor; the
// reauthenticated Owner context of createApprovedTutor, #55; and
// demo.student, #313), no one-time-link redemption, 1 registration (the
// substitute, #55); the attendance, closeout, review and resubmission steps
// add none (#76, #77, #78, #305). It never
// changes the zone, the
// opening hours or the scheduling policy. No other spec offers sessions to
// demo.tutor; the dates are three and six weeks ahead (other scheduling
// specs use the first weekday two weeks ahead, 10:00–12:00). No other spec
// logs in as demo.student, moves or cancels the demo class U1, or counts
// demo.tutor's or demo.student's notifications (#313 parallel safety).

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
// #313 G3 adds about 30 s (the homework steps): 180 → 210 s.
const TEST_TIMEOUT_MS = 210_000;
const TUTOR = 'demo.tutor';
const TUTOR_NAME = 'Demo Tutor';
const TUTOR_PASSWORD = 'classora-demo-tutor';
/** The #195 demo student (#313 G3: the reserved login). */
const DEMO_STUDENT = 'demo.student';
const DEMO_STUDENT_NAME = 'Demo Student';
const DEMO_STUDENT_PASSWORD = 'classora-demo-student';
const LEVEL = 'Primaria';
/** The subject of the session `run.sh` inserts for attendance (#76). */
const ATTENDANCE_SUBJECT = 'E2E Attendance';
/** The subject of the session `run.sh` inserts for the closeout (#77). */
const CLOSEOUT_SUBJECT = 'E2E Closeout';
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

/** The started session `run.sh` inserted for demo.tutor (#76 H9; the
 * closeout one with `subject`, #77 H8). */
async function attendanceSession(
  page: Page,
  tutorId: string,
  subject: string = ATTENDANCE_SUBJECT,
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
  ).items.filter((item) => item.subject.name === subject);
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

// #313 G3: the student's expected 4xx: the signed-out /auth/me (401), and
// the sibling's homework (404) and the working list (403) from its API.
const STUDENT_EXPECTED =
  /Failed to load resource: the server responded with a status of (401|403|404)/;

/** demo.student's own login (the one login #313 adds), desktop size. */
async function signInDemoStudent(browser: Browser) {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const consoleErrors = watchConsole(page, STUDENT_EXPECTED);
  await page.goto('/login');
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(DEMO_STUDENT);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(DEMO_STUDENT_PASSWORD);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, DEMO_STUDENT_NAME)),
  ).toBeVisible();
  await acceptPrivacyNoticeIfShown(page);
  return { context, page, consoleErrors };
}

interface DemoClass {
  id: string;
  startsAt: string;
  subject: string;
  studentId: string;
  studentName: string;
}

/**
 * The demo seed's (#299) confirmed classes of demo.tutor at 16:00: U1
 * (Matemáticas, Secundaria, with demo.student) and U2 (Matemáticas,
 * Primaria, with the sibling), read from the tutor's schedule.
 */
async function demoClasses(
  page: Page,
  tutorId: string,
): Promise<{ u1: DemoClass; u2: DemoClass }> {
  const from = new Date().toISOString();
  const to = new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString();
  const read = await api(
    page,
    `/api/v1/tutors/${tutorId}/schedule?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
  );
  expect(read.status, JSON.stringify(read.body)).toBe(200);
  const items = (
    read.body as {
      items: {
        id: string;
        kind: string;
        state: string;
        starts_at: string;
        subject: { name: string; education_level_name: string };
        participants: { student_id: string; display_name: string }[];
      }[];
    }
  ).items;
  const at16 = (level: string) => {
    const found = items
      .filter(
        (item) =>
          item.kind === 'individual' &&
          item.state === 'confirmed' &&
          item.subject.name === 'Matemáticas' &&
          item.subject.education_level_name === level &&
          new Intl.DateTimeFormat('en-GB', {
            timeZone: ZONE,
            hour: '2-digit',
            minute: '2-digit',
          }).format(new Date(item.starts_at)) === '16:00' &&
          item.participants.length === 1,
      )
      .sort((a, b) => a.starts_at.localeCompare(b.starts_at))[0];
    expect(found, `the demo class (${level}) at 16:00`).toBeDefined();
    return {
      id: found!.id,
      startsAt: found!.starts_at,
      subject: found!.subject.name,
      studentId: found!.participants[0]!.student_id,
      studentName: found!.participants[0]!.display_name,
    };
  };
  return { u1: at16('Secundaria'), u2: at16('Primaria') };
}

/** The day `days` days after today, center zone (YYYY-MM-DD). */
function firstDayAhead(days: number): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  return new Date(Date.UTC(year, month - 1, day + days, 12))
    .toISOString()
    .slice(0, 10);
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
    // The default (desktop) size, for the bell in step 9b: at 390 px the
    // header shows only the menu button.
    const desktop = tutor.page.viewportSize();
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

    // 9. Closeout (#77 H8): the tutor closes out the other started session
    // from My schedule, still at 390×844.
    const closing = await attendanceSession(
      tutor.page,
      tutorId,
      CLOSEOUT_SUBJECT,
    );
    expect(closing.students).toHaveLength(1);
    const [only] = closing.students as [string];
    await tutor.page.goto(
      `/my-schedule?view=list&date=${localDateOf(closing.startsAt)}`,
    );
    const agenda = tutor.page.getByRole('main').getByRole('region', {
      name: common.mySchedule.regionLabel,
    });
    await expect(agenda).toHaveAttribute('aria-busy', 'false');
    await agenda
      .getByRole('button', { name: new RegExp(escape(CLOSEOUT_SUBJECT)) })
      .click();
    await tutor.page
      .getByRole('dialog', { name: CLOSEOUT_SUBJECT })
      .getByRole('link', { name: common.closeout.closeOut, exact: true })
      .click();
    await expect(tutor.page).toHaveURL(
      new RegExp(`/my-schedule/sessions/${closing.id}/closeout$`),
    );
    const flow = tutor.page.getByRole('main');
    const next = flow.getByRole('button', {
      name: common.closeout.next,
      exact: true,
    });
    await flow
      .getByRole('group', { name: only, exact: true })
      .getByRole('radio', {
        name: common.attendance.statuses.present,
        exact: true,
      })
      .check();
    await flow
      .getByRole('button', { name: common.attendance.save, exact: true })
      .click();
    await expect(flow.getByText(common.attendance.saved)).toBeVisible();
    await expect(next).toBeEnabled();
    await next.click();
    await flow
      .getByLabel(common.closeout.actualMinutes, { exact: true })
      .fill('55');
    await next.click();
    await flow
      .getByLabel(common.closeout.fields.topics, { exact: false })
      .fill('Fracciones equivalentes');
    await flow
      .getByLabel(common.closeout.fields.performance, { exact: false })
      .fill('Participó y resolvió los ejercicios.');
    await flow
      .getByLabel(common.closeout.fields.next_steps, { exact: false })
      .fill('Suma de fracciones.');
    expect(await axeViolations(tutor.page)).toEqual([]);
    await next.click();
    await flow
      .getByRole('button', { name: common.closeout.submit, exact: true })
      .click();
    await expect(flow.getByText(common.closeout.submittedNotice)).toBeVisible();
    await expect(
      flow.getByText(common.closeout.submitted, { exact: true }),
    ).toBeVisible();
    await tutor.page.reload();
    await expect(
      flow.getByText(common.closeout.submitted, { exact: true }),
    ).toBeVisible();
    await expect(
      flow.getByText(
        fill(common.closeout.minutesOfScheduled, { actual: 55, scheduled: 60 }),
      ),
    ).toBeVisible();
    expect(await axeViolations(tutor.page)).toEqual([]);

    // 9b. Closeout resubmission (#305 G3), still in the tutor block: the
    // Owner (shared page) requests changes on the session page; the tutor
    // sees the bell message, the My schedule badge and "Corregir cierre",
    // the reviewer's reason, then edits (attendance editable, 58 min,
    // homework) and resubmits.
    const changesReason = 'Añade la tarea asignada.';
    await page.goto(`/schedule/sessions/${closing.id}`);
    const review = page.getByRole('main').getByRole('region', {
      name: common.closeout.section.heading,
    });
    await review
      .getByRole('button', {
        name: common.closeout.review.requestChanges,
        exact: true,
      })
      .click();
    const requestDialog = page.getByRole('dialog');
    await requestDialog
      .getByLabel(common.closeout.review.requestDialog.reason, { exact: false })
      .fill(changesReason);
    await requestDialog
      .getByRole('button', {
        name: common.closeout.review.requestDialog.submit,
        exact: true,
      })
      .click();
    await expect(requestDialog).toBeHidden();
    await expect(review).toContainText(common.closeout.review.changesRequested);

    // The bell is in the header at desktop width only; back to the phone
    // size for My schedule and the closeout.
    if (desktop) {
      await tutor.page.setViewportSize(desktop);
    }
    await tutor.page.reload();
    await bell.click();
    await expect(panel).toContainText(
      `${textBefore(common.notifications.types.academics_closeout_changes_requested)}${CLOSEOUT_SUBJECT}`,
    );
    await tutor.page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await tutor.page.setViewportSize({ width: 390, height: 844 });
    await tutor.page.goto(
      `/my-schedule?view=list&date=${localDateOf(closing.startsAt)}`,
    );
    await expect(agenda).toHaveAttribute('aria-busy', 'false');
    await agenda
      .getByRole('button', { name: new RegExp(escape(CLOSEOUT_SUBJECT)) })
      .click();
    const entry = tutor.page.getByRole('dialog', { name: CLOSEOUT_SUBJECT });
    await expect(entry).toContainText(common.closeout.tutorView.badge);
    await entry
      .getByRole('link', { name: common.closeout.tutorView.fix, exact: true })
      .click();
    await expect(tutor.page).toHaveURL(
      new RegExp(`/my-schedule/sessions/${closing.id}/closeout$`),
    );
    await expect(
      flow.getByRole('heading', {
        name: common.closeout.tutorView.changesRequested,
      }),
    ).toBeVisible();
    await expect(flow.getByText(changesReason)).toBeVisible();
    expect(await axeViolations(tutor.page)).toEqual([]);
    await flow
      .getByRole('button', {
        name: common.closeout.tutorView.resubmit,
        exact: true,
      })
      .click();
    await expect(
      flow.getByRole('button', { name: common.attendance.save, exact: true }),
    ).toBeVisible();
    await next.click();
    await flow
      .getByLabel(common.closeout.actualMinutes, { exact: true })
      .fill('58');
    await next.click();
    await flow
      .getByLabel(common.closeout.fields.homework, { exact: false })
      .fill('Ejercicios 1 a 5');
    await next.click();
    await flow
      .getByRole('button', {
        name: common.closeout.tutorView.resubmitSubmit,
        exact: true,
      })
      .click();
    await expect(
      flow.getByText(common.closeout.tutorView.resubmittedNotice),
    ).toBeVisible();
    await expect(
      flow.getByRole('heading', {
        name: common.closeout.tutorView.underReview,
      }),
    ).toBeVisible();
    await expect(
      flow.getByText(
        fill(common.closeout.minutesOfScheduled, { actual: 58, scheduled: 60 }),
      ),
    ).toBeVisible();
    expect(await axeViolations(tutor.page)).toEqual([]);

    // 9c. Homework (#313 G3), still in the tutor's block: the tutor sets
    // homework from the demo class U1 (demo.student's) in My schedule; a
    // new demo.student context (the one login #313 adds) follows the bell
    // to the item and hands in a text and a link; the tutor's bell opens
    // the roster, where the link is safe, and grades 8 of 10; the student
    // reloads and sees the grade; the student's API gets 404 for the
    // sibling's homework (U2) and 403 on the working list.
    const demo = await demoClasses(tutor.page, tutorId);
    const homeworkTitle = `E2E homework ${suffix}`;
    const answer = `E2E answer ${suffix}`;
    const answerUrl = `https://example.com/e2e-${suffix}`;
    const feedback = `E2E feedback ${suffix}`;
    await tutor.page.setViewportSize({ width: 390, height: 844 });
    await tutor.page.goto(
      `/my-schedule?view=list&date=${localDateOf(demo.u1.startsAt)}`,
    );
    const week = tutor.page.getByRole('main').getByRole('region', {
      name: common.mySchedule.regionLabel,
    });
    await expect(week).toHaveAttribute('aria-busy', 'false');
    await week.locator(`[data-session-id="${demo.u1.id}"]`).click();
    await tutor.page
      .getByRole('dialog', { name: demo.u1.subject })
      .getByRole('link', {
        name: common.assignments.entry.setHomework,
        exact: true,
      })
      .click();
    await expect(tutor.page).toHaveURL(
      new RegExp(`/assignments/new\\?sessionId=${demo.u1.id}$`),
    );
    const homeworkForm = tutor.page.getByRole('main');
    await expect(
      homeworkForm.getByRole('checkbox', { name: demo.u1.studentName }),
    ).toBeChecked();
    await homeworkForm
      .getByLabel(common.assignments.form.title, { exact: true })
      .fill(homeworkTitle);
    await homeworkForm
      .getByLabel(common.assignments.form.instructions, { exact: true })
      .fill('Resuelve los ejercicios 1 a 5.');
    await homeworkForm
      .getByLabel(common.assignments.form.dueOn, { exact: true })
      .fill(firstDayAhead(1));
    await homeworkForm
      .getByLabel(common.assignments.form.maxScore, { exact: true })
      .fill('10');
    expect(await axeViolations(tutor.page)).toEqual([]);
    await homeworkForm
      .getByRole('button', { name: common.assignments.form.save, exact: true })
      .click();
    await expect(tutor.page).toHaveURL(/\/assignments\/[0-9a-f-]{36}$/);
    const assignmentId = tutor.page.url().split('/').pop()!;
    await expect(
      homeworkForm.getByText(
        fill(common.assignments.notices.created_one, { count: 1 }),
      ),
    ).toBeVisible();
    const rosterRow = homeworkForm
      .getByRole('listitem')
      .filter({ hasText: demo.u1.studentName });
    await expect(rosterRow).toContainText(common.assignments.states.pending);
    expect(await axeViolations(tutor.page)).toEqual([]);

    // The student (+1 login, #313): the bell at desktop width (the header
    // shows only the menu button at 390 px), then the phone.
    const student = await signInDemoStudent(browser);
    try {
      const [unreadBefore, unreadAfter] =
        common.notifications.bellUnread.split('{{unread}}');
      const studentBell = student.page.getByRole('banner').getByRole('button', {
        name: new RegExp(
          `^${escape(unreadBefore!)}[1-9]\\d*${escape(unreadAfter!)}$`,
        ),
      });
      await studentBell.click();
      await student.page
        .getByRole('dialog', { name: common.notifications.title })
        .getByRole('button', {
          name: new RegExp(
            `^${escape(textBefore(common.notifications.types.academics_assignment_set))}`,
          ),
        })
        .first()
        .click();
      await expect(student.page).toHaveURL(
        `/my-homework/${assignmentId}?student=${demo.u1.studentId}`,
      );
      await student.page.setViewportSize({ width: 390, height: 844 });
      const studentMain = student.page.getByRole('main');
      await expect(
        studentMain.getByRole('heading', { level: 1, name: homeworkTitle }),
      ).toBeVisible();
      await expect(studentMain).toContainText(
        common.assignments.states.pending,
      );
      await studentMain
        .getByLabel(common.assignments.submit.text, { exact: true })
        .fill(answer);
      await studentMain
        .getByLabel(common.assignments.submit.link, { exact: true })
        .fill(answerUrl);
      await studentMain
        .getByRole('button', {
          name: common.assignments.submit.handIn,
          exact: true,
        })
        .click();
      await expect(
        studentMain.getByText(common.assignments.notices.handedIn),
      ).toBeVisible();
      await expect(studentMain).toContainText(
        common.assignments.states.submitted,
      );
      expect(await axeViolations(student.page)).toEqual([]);

      // The tutor: the bell (desktop width) opens the roster.
      if (desktop) {
        await tutor.page.setViewportSize(desktop);
      }
      await tutor.page.reload();
      await bell.click();
      await panel
        .getByRole('button', {
          name: new RegExp(
            `^${escape(common.notifications.types.academics_assignment_submitted)}`,
          ),
        })
        .first()
        .click();
      await expect(tutor.page).toHaveURL(`/assignments/${assignmentId}`);
      await tutor.page.setViewportSize({ width: 390, height: 844 });
      await expect(rosterRow).toContainText(
        common.assignments.states.submitted,
      );
      await expect(rosterRow).toContainText(answer);
      const handedLink = rosterRow.getByRole('link', {
        name: new RegExp(escape(answerUrl)),
      });
      await expect(handedLink).toHaveAttribute('href', answerUrl);
      const rel = (await handedLink.getAttribute('rel')) ?? '';
      expect(rel).toContain('noopener');
      expect(rel).toContain('nofollow');
      await rosterRow
        .getByRole('button', {
          name: fill(common.assignments.actions.gradeFor, {
            name: demo.u1.studentName,
          }),
        })
        .click();
      const gradeDialog = tutor.page.getByRole('dialog', {
        name: fill(common.assignments.grade.title, {
          name: demo.u1.studentName,
        }),
      });
      await gradeDialog
        .getByLabel(common.assignments.grade.score, { exact: true })
        .fill('8');
      await gradeDialog
        .getByLabel(common.assignments.grade.feedbackOptional, { exact: true })
        .fill(feedback);
      expect(await axeViolations(tutor.page)).toEqual([]);
      await gradeDialog
        .getByRole('button', { name: common.assignments.grade.save })
        .click();
      await expect(gradeDialog).toBeHidden();
      // The merged contract sends decimals as strings with two places.
      const eightOfTen = fill(common.assignments.scoreWithPercent, {
        score: '8.00',
        max: '10.00',
        percent: '80.00',
      });
      await expect(rosterRow).toContainText(common.assignments.states.graded);
      await expect(rosterRow).toContainText(eightOfTen);

      // The student reloads: graded, the feedback, no form.
      await student.page.reload();
      await expect(studentMain).toContainText(common.assignments.states.graded);
      await expect(studentMain).toContainText(eightOfTen);
      await expect(studentMain).toContainText(feedback);
      await expect(studentMain.getByRole('textbox')).toHaveCount(0);
      const sibling = await api(
        student.page,
        `/api/v1/students/${demo.u2.studentId}/assignments`,
      );
      expect(sibling.status).toBe(404);
      const working = await api(student.page, '/api/v1/assignments');
      expect(working.status).toBe(403);
      expect(await cspViolations(student.page)).toEqual([]);
      expect(student.consoleErrors).toEqual([]);
    } finally {
      await student.context.close();
    }

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

  // 10. Closeout (#77 H8, #78 G2): the Owner finds the item in the approval
  // inbox (the #305 resubmission: "Reenviado", 58 min, no decision buttons)
  // and opens the session's Closeout section with "Revisar".
  const closed = await attendanceSession(page, tutorId, CLOSEOUT_SUBJECT);
  await page.goto('/approvals?type=academics.session_closeout');
  const item = page
    .getByRole('main')
    .getByRole('row')
    .filter({ hasText: CLOSEOUT_SUBJECT });
  await expect(item).toContainText(
    fill(common.closeout.minutesOfScheduled, { actual: 58, scheduled: 60 }),
  );
  await expect(item).toContainText(
    common.approvals.closeout.kinds.resubmission,
  );
  await expect(item.getByRole('button')).toHaveCount(0);
  await item
    .getByRole('link', { name: common.approvals.closeout.review })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/schedule/sessions/${closed.id}#closeout$`),
  );
  const closeoutSection = page.getByRole('main').getByRole('region', {
    name: common.closeout.section.heading,
  });
  await expect(closeoutSection).toContainText(common.closeout.submitted);
  await expect(closeoutSection).toContainText(
    fill(common.closeout.minutesOfScheduled, { actual: 58, scheduled: 60 }),
  );
  expect(await axeViolations(page)).toEqual([]);

  // 11. Closeout review (#78 I9), on the shared Owner session: correct the
  // minutes 58 → 50 with a reason (the history shows it), approve
  // ("Aprobado · 50 min"), and the inbox no longer lists it as pending.
  await closeoutSection
    .getByRole('button', { name: common.closeout.review.correct, exact: true })
    .click();
  const correctDialog = page.getByRole('dialog');
  await correctDialog
    .getByLabel(common.closeout.actualMinutes, { exact: true })
    .fill('50');
  await correctDialog
    .getByLabel(common.closeout.review.correctDialog.reason, { exact: false })
    .fill('Terminó antes');
  await correctDialog
    .getByRole('button', {
      name: common.closeout.review.correctDialog.submit,
      exact: true,
    })
    .click();
  await expect(correctDialog).toBeHidden();
  await expect(closeoutSection).toContainText(
    fill(common.closeout.review.history.minutes, { previous: 58, next: 50 }),
  );
  await closeoutSection
    .getByRole('button', { name: common.closeout.review.approve, exact: true })
    .click();
  const approveDialog = page.getByRole('dialog');
  await expect(approveDialog).toContainText(
    fill(common.closeout.review.approveDialog.minutes, {
      actual: 50,
      scheduled: 60,
    }),
  );
  await approveDialog
    .getByRole('button', {
      name: common.closeout.review.approveDialog.submit,
      exact: true,
    })
    .click();
  await expect(approveDialog).toBeHidden();
  await expect(closeoutSection).toContainText(
    fill(common.closeout.review.approvedShort, { minutes: 50 }),
  );
  await expect(
    closeoutSection.getByRole('button', {
      name: common.closeout.review.approve,
      exact: true,
    }),
  ).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);
  await page.goto('/approvals?type=academics.session_closeout&status=approved');
  await expect(
    page
      .getByRole('main')
      .getByRole('row')
      .filter({ hasText: CLOSEOUT_SUBJECT }),
  ).toBeVisible();
  await page.goto('/approvals?type=academics.session_closeout');
  await expect(
    page
      .getByRole('main')
      .getByRole('row')
      .filter({ hasText: CLOSEOUT_SUBJECT }),
  ).toHaveCount(0);
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);
});
