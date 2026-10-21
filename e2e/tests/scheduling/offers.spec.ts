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
import { createSubjectViaApi } from '../support/tutors';

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
// Budgets (#27, ADR-0008; see support/auth.ts): 1 login (demo.tutor), no
// one-time-link redemption, no registration. It never changes the zone, the
// opening hours or the scheduling policy. No other spec offers sessions to
// demo.tutor; the date is three weeks ahead (other scheduling specs use the
// first weekday two weeks ahead, 10:00–12:00).

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const TUTOR = 'demo.tutor';
const TUTOR_NAME = 'Demo Tutor';
const TUTOR_PASSWORD = 'classora-demo-tutor';
const LEVEL = 'Primaria';
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

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
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

async function signInTutor(browser: Browser) {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const consoleErrors = watchConsole(page);
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

test('a tutor accepts one offer and declines another; staff see the reassignment', async ({
  page,
  browser,
}, testInfo: TestInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Oferta ${suffix}`;
  const accepted = `Aceptada E2E ${suffix}`;
  const declined = `Rechazada E2E ${suffix}`;
  const note = 'Ese día tengo otra clase.';
  const date = firstWeekdayAhead(21 + 7 * testInfo.retry);
  await recordCspViolations(page);
  const ownerErrors = watchConsole(page);

  // 1. Setup through the API with the shared Owner session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Lucía',
    familyNames,
  });
  const acceptedId = await createSubjectViaApi(page, LEVEL, accepted);
  const declinedId = await createSubjectViaApi(page, LEVEL, declined);
  const tutorId = await demoTutorId(page);
  await offerViaApi(page, {
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
    expect(await cspViolations(tutor.page)).toEqual([]);
    expect(tutor.consoleErrors).toEqual([]);
  } finally {
    await tutor.context.close();
  }

  // 5. The Owner: the reassignment item links to the session page.
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
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);
});
