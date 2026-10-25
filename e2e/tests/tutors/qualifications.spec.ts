import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Browser, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { switchColorScheme } from '../support/a11y';
import { common, errors, fill, signedInAs } from '../support/catalog';
import { createApprovedAccount } from '../support/consent';
import { createGuardianViaApi, linkGuardianViaApi } from '../support/guardians';
import { createStudentViaApi } from '../support/students';
import { createApprovedTutor, createSubjectViaApi } from '../support/tutors';

// Tutor qualification requests (#44 G1). The demo Owner (the shared state)
// creates two subjects under a seeded level and an approved tutor account.
// The tutor signs in once in a second browser context, requests the first
// subject (Pending), requests it again (qualification_exists in the dialog)
// and requests the second. The Owner approves the first and rejects the
// second with a reason on /approvals. The tutor reloads: the bell has the
// approval message, the rows say Approved and Rejected with its reason, and
// "Teaching subjects" lists the approved one. Then (#198 F1) the Owner opens
// the tutor's profile and revokes Física with a reason; the tutor reloads:
// the row says Revoked with the reason, "Teaching subjects" no longer lists
// it, the bell has the revoked message, and requesting Física again gives
// Pending.
//
// Between the approval and the revocation (#59 H5), a family requests a
// session: the Owner sets the tutor's weekly availability (one weekday at
// least 14 days ahead, 10:00-12:00) through the API, creates a student and
// a guardian account (an approved self-registration) with a profile linked
// to the student. The guardian signs in, opens "Session requests" →
// "Request a session", chooses Física and 60 min, goes to that week and
// sees 10:00 with no tutor name on the page, requests it with a note (the
// success panel), finds it Pending in "My session requests", and the week
// still offers 10:00, now as "Requested". Both family pages get an axe
// scan.
//
// #60 I5: the guardian also requests 11:00 the same day. The Owner opens
// /approvals, follows "Review" on the 10:00 request, sees the tutor in
// "Suggested tutors", chooses them and confirms: the success link opens the
// session page, which shows Offered to that tutor (axe on the review page).
// Then the Owner rejects 11:00 with a reason. The guardian reloads My
// requests: 10:00 Confirmed, 11:00 Rejected with the reason, both
// notifications in the bell, and no tutor name on the family pages.
//
// #67 G8: the tutor accepts that offer (the #52 API in the tutor's context);
// the Owner links a second child to the guardian and creates a student no
// one links. On a phone (390×844) the tutor opens "My schedule" from the
// menu and finds the session in the List view (the default below 768 px);
// the guardian chooses the child and sees it Confirmed with the tutor's
// display name (the rejected 11:00 has no session), and the guardian's API
// call for the unlinked student is 404. Axe on both pages. The confirmed
// session doesn't block the revocation that follows (#198 E1).
//
// #62b E1: the guardian opens that session's details, chooses "Request a
// change" → "Cancel this class", sees the policy preview and sends it: the
// details say "Change requested — pending". The Owner follows "Review" from
// the approval inbox and approves it as an exception with a reason. The
// guardian's bell has the approval and its link opens My change requests:
// Approved, the exception outcome with its makeup date and the reason, and
// no tutor name on the page. Axe on the dialog and on My change requests
// (light and dark). The cancelled session doesn't block the revocation
// either.
//
// #79 I8 (before #62b): the tutor, on a phone, opens that session's notes
// from My schedule, writes an internal note and a second one that they
// share (the dialog shows its text). The guardian's bell has the shared-note
// notification and its link opens My schedule on the class's date; the
// class's details show "Notes from the tutor" with the shared text only, and
// the guardian's GET …/learning-notes never contains the internal text. Axe
// on the notes page, the share dialog and the guardian's dialog.
//
// #308 G3 (after #79, before #62b): the Owner creates an evaluation
// template through the API and finds it on /academics/evaluation-templates;
// the tutor (phone) sees "Academic history of {child}" and no "Evaluate"
// in the class's details (it hasn't started), saves a draft and finalizes a
// second evaluation from "New evaluation" (no class link); the guardian's
// bell opens /my-history?student=…, which shows the final one in today's
// school year (read from GET /academic-periods) and the chronological view,
// never the draft; the family's API reads agree. Axe on the template list,
// the form and the history.
//
// #310 G3 (after #308, before #62b, whose cancellation ends the tutor's
// assignment): the tutor (phone) follows "Learning plans of {child}" from
// the class's details to "New plan" (only Física offered), saves a draft
// with one objective and two milestones (a third added and removed), and
// the guardian's API reads never carry it; the tutor activates it (the
// dialog names the child) and marks a milestone done (1 of 2 · 50 %); the
// guardian's bell opens the family page of the plan (Active, the progress,
// no control), and the "Learning plans" nav item lists it for the child.
// Axe on the editor and the family view.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 3 logins (the demo Owner's
// own reauthenticated API context for the role change, the tutor and the
// guardian; #79, #308 and #310 reuse them), 0 redemptions and 2 self-registrations (createApprovedTutor and
// the guardian's createApprovedAccount). Every `make e2e` starts from an
// empty database, so names are unique per run.

const TUTOR = 'e2e.tutor.qualifications';
const TUTOR_NAME = 'E2E Tutor Calificaciones';
const PASSWORD = 'e2e-tutor-qualifications-password';
const REASON = 'Falta el certificado de química.';
const REVOKE_REASON = 'El certificado de física venció.';
// #59 H5: the guardian who requests a session (created by this test).
const PARENT = 'e2e.requests.parent';
const PARENT_NAME = 'E2E Madre Solicitudes';
const PARENT_PASSWORD = 'e2e-requests-parent-password';
const REQUEST_NOTE = 'Prefiere la sesión antes del mediodía.';
// #60 I5: the reason the family sees for the 11:00 request.
const REJECT_REASON = 'Ese día solo hay una tutora disponible.';
const CENTER_ZONE = 'America/Mexico_City';
// #67 G8: the phone viewport of the My schedule checks.
const PHONE = { width: 390, height: 844 };
// #62b E1: the reason the Owner gives for the exception.
const EXCEPTION_REASON = 'La familia avisó de una emergencia médica.';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
// A seeded level (#32) that no spec deactivates.
const LEVEL = 'Secundaria';

// Chromium logs every 4xx resource as a console error: the signed-out
// /auth/me (401) of the fresh context, the request answered 409
// qualification_exists and (#67 G8) the guardian's deliberate 404 for an
// unlinked student's schedule are expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|404|409)/;

// Three browsers, two logins, two decisions, two session requests and their
// review, a change request and its review, and a revocation: more than the
// default 30 s under machine load.
const TEST_TIMEOUT_MS = 300_000;

function watchConsole(page: Page): string[] {
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
  return consoleErrors;
}

async function signInFresh(
  browser: Browser,
  account = { username: TUTOR, password: PASSWORD, displayName: TUTOR_NAME },
) {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const consoleErrors = watchConsole(page);
  await page.goto('/login');
  await page
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(account.username);
  await page
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(account.password);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, account.displayName)),
  ).toBeVisible();
  // A new user lands on the privacy notice first (#174).
  await acceptPrivacyNoticeIfShown(page);
  return { context, page, consoleErrors };
}

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** Today in the center zone, as YYYY-MM-DD (E2E never changes the zone). */
function centerToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: CENTER_ZONE }).format(
    new Date(),
  );
}

/** A local date `days` after `date`, and its ISO weekday (1 = Monday). */
function plusDays(
  date: string,
  days: number,
): { date: string; weekday: number } {
  const [year, month, day] = date.split('-').map(Number) as [
    number,
    number,
    number,
  ];
  const next = new Date(Date.UTC(year, month - 1, day + days, 12));
  const weekday = next.getUTCDay() === 0 ? 7 : next.getUTCDay();
  return { date: next.toISOString().slice(0, 10), weekday };
}

/**
 * #59 H5 setup with the shared Owner session: the tutor's weekly window
 * (`weekday`, 10:00-12:00), a minor student, and a guardian account (an
 * approved self-registration: 1 registration, no login) whose profile is
 * linked to the student. Returns the student's full name and the guardian
 * profile's id.
 */
async function setUpFamily(
  page: Page,
  request: Parameters<typeof createApprovedAccount>[1],
  baseURL: string,
  tutorId: string,
  weekday: number,
  suffix: string,
): Promise<{ studentName: string; guardianId: string }> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const weekly = await api(
    page,
    `/api/v1/tutors/${tutorId}/availability/weekly`,
    {
      method: 'PUT',
      json: {
        version: 0,
        windows: [{ weekday, starts_at: '10:00', ends_at: '12:00' }],
      },
      headers: { 'X-CSRF-Token': csrfTokenOf(me) },
    },
  );
  expect(weekly.status).toBe(200);
  const givenNames = 'Lucía';
  const familyNames = `Solicitudes ${suffix}`;
  const studentId = await createStudentViaApi(page, {
    givenNames,
    familyNames,
  });
  const userId = await createApprovedAccount(page, request, baseURL, {
    username: PARENT,
    displayName: PARENT_NAME,
    password: PARENT_PASSWORD,
    role: 'guardian',
  });
  const guardianId = await createGuardianViaApi(page, {
    givenNames: 'Madre',
    familyNames: `Solicitudes ${suffix}`,
    userId,
  });
  await linkGuardianViaApi(page, studentId, guardianId);
  return { studentName: `${givenNames} ${familyNames}`, guardianId };
}

const qualifications = (page: Page) =>
  page.getByRole('main').locator('#qualifications');

const row = (page: Page, subject: string) =>
  qualifications(page).getByRole('row').filter({ hasText: subject });

async function openTutorProfile(page: Page) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.tutors, exact: true })
    .click();
  await expect(page).toHaveURL('/tutors');
  const main = page.getByRole('main');
  await main.getByRole('link', { name: TUTOR_NAME, exact: true }).click();
  await expect(page).toHaveURL(/\/tutors\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: TUTOR_NAME, exact: true }),
  ).toBeVisible();
}

async function requestSubject(page: Page, level: string, subject: string) {
  await qualifications(page)
    .getByRole('button', { name: common.tutors.qualifications.request })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  const levelSelect = dialog.getByLabel(
    common.tutors.qualifications.levelLabel,
    { exact: true },
  );
  await expect(levelSelect.getByRole('option', { name: level })).toBeAttached();
  await levelSelect.selectOption({ label: level });
  const subjectSelect = dialog.getByLabel(
    common.tutors.qualifications.subjectLabel,
    { exact: true },
  );
  await expect(
    subjectSelect.getByRole('option', { name: subject }),
  ).toBeAttached();
  await subjectSelect.selectOption({ label: subject });
  await dialog
    .getByRole('button', { name: common.tutors.qualifications.send })
    .click();
  return dialog;
}

async function decide(
  owner: Page,
  action: 'approve' | 'reject',
  summary: string,
) {
  const main = owner.getByRole('main');
  const named =
    action === 'approve'
      ? common.approvals.actions.approveFor
      : common.approvals.actions.rejectFor;
  const button = main.getByRole('button', {
    name: fill(named, { name: summary }),
  });
  await button.click();
  const dialog = owner.getByRole('dialog');
  await expect(dialog).toBeVisible();
  if (action === 'reject') {
    await dialog
      .getByLabel(common.approvals.dialog.reasonRequiredLabel, { exact: true })
      .fill(REASON);
  }
  await dialog
    .getByRole('button', {
      name:
        action === 'approve'
          ? common.approvals.actions.approve
          : common.approvals.actions.reject,
      exact: true,
    })
    .click();
  await expect(dialog).toBeHidden();
  await expect(button).toHaveCount(0);
}

test('a tutor requests qualifications; the Owner approves, rejects and revokes', async ({
  page,
  browser,
  playwright,
  baseURL,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const physics = `Física E2E ${suffix}`;
  const chemistry = `Química E2E ${suffix}`;

  // 1. Setup through the API with the shared Owner session.
  await recordCspViolations(page);
  const ownerErrors = watchConsole(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  // #308 G3: the template's subject.
  const physicsId = await createSubjectViaApi(page, LEVEL, physics);
  await createSubjectViaApi(page, LEVEL, chemistry);
  const tutorId = await createApprovedTutor(
    page,
    playwright.request,
    baseURL!,
    {
      username: TUTOR,
      displayName: TUTOR_NAME,
      password: PASSWORD,
    },
  );

  const tutor = await signInFresh(browser);
  try {
    // 2. The tutor requests Física: Pending. Again: qualification_exists.
    await openTutorProfile(tutor.page);
    let dialog = await requestSubject(tutor.page, LEVEL, physics);
    await expect(dialog).toBeHidden();
    await expect(
      qualifications(tutor.page)
        .getByRole('status')
        .filter({ hasText: common.tutors.qualifications.requested }),
    ).toBeVisible();
    await expect(row(tutor.page, physics)).toContainText(
      common.tutors.qualifications.statuses.pending,
    );
    dialog = await requestSubject(tutor.page, LEVEL, physics);
    await expect(dialog.getByRole('alert')).toContainText(
      errors.qualification_exists,
    );
    await dialog
      .getByRole('button', { name: common.tutors.qualifications.close })
      .click();
    await expect(dialog).toBeHidden();
    dialog = await requestSubject(tutor.page, LEVEL, chemistry);
    await expect(dialog).toBeHidden();
    await expect(row(tutor.page, chemistry)).toContainText(
      common.tutors.qualifications.statuses.pending,
    );

    // 3. The Owner approves Física and rejects Química with a reason, on
    // the Approvals page filtered to tutor qualifications (other specs may
    // leave open requests of other types).
    await page.goto('/approvals?type=staff.qualification');
    await expect(
      page
        .getByRole('main')
        .getByRole('heading', { level: 1, name: common.approvals.title }),
    ).toBeVisible();
    const summary = (subject: string) =>
      fill(common.approvals.summary.qualification, {
        tutor: TUTOR_NAME,
        subject,
        level: LEVEL,
      });
    await decide(page, 'approve', summary(physics));
    await decide(page, 'reject', summary(chemistry));
    expect(await cspViolations(page)).toEqual([]);
    expect(ownerErrors).toEqual([]);

    // 4. The tutor reloads: the bell has the approval, the rows changed and
    // Física is a teaching subject.
    await tutor.page.reload();
    const [before, after] = common.notifications.bellUnread.split('{{unread}}');
    const escape = (text: string) =>
      text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const bell = tutor.page.getByRole('banner').getByRole('button', {
      name: new RegExp(`^${escape(before!)}[1-9]${escape(after!)}$`),
    });
    await expect(bell).toBeVisible();
    await bell.click();
    const panel = tutor.page.getByRole('dialog', {
      name: common.notifications.title,
    });
    await expect(panel).toContainText(
      fill(common.notifications.types.staff_qualification_approved, {
        subject_name: physics,
        education_level_name: LEVEL,
      }),
    );
    await expect(panel).toContainText(
      fill(common.notifications.types.staff_qualification_rejected, {
        subject_name: chemistry,
        education_level_name: LEVEL,
      }),
    );
    await tutor.page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(row(tutor.page, physics)).toContainText(
      common.tutors.qualifications.statuses.approved,
    );
    await expect(row(tutor.page, chemistry)).toContainText(
      common.tutors.qualifications.statuses.rejected,
    );
    await expect(row(tutor.page, chemistry)).toContainText(REASON);
    await expect(
      qualifications(tutor.page)
        .getByRole('listitem')
        .filter({ hasText: physics }),
    ).toHaveText(`${physics} (${LEVEL})`);
    await expect(
      qualifications(tutor.page)
        .getByRole('listitem')
        .filter({ hasText: chemistry }),
    ).toHaveCount(0);

    // 4b. (#59 H5) A guardian requests a session with the approved tutor.
    const target = plusDays(centerToday(), 14);
    // Sunday is closed (default operating hours): use the Monday after.
    const day = target.weekday === 7 ? plusDays(target.date, 1) : target;
    const { studentName, guardianId } = await setUpFamily(
      page,
      playwright.request,
      baseURL!,
      tutorId,
      day.weekday,
      suffix,
    );
    const parent = await signInFresh(browser, {
      username: PARENT,
      password: PARENT_PASSWORD,
      displayName: PARENT_NAME,
    });
    try {
      await parent.page
        .getByRole('banner')
        .getByRole('link', { name: common.nav.sessionRequests, exact: true })
        .click();
      await expect(parent.page).toHaveURL('/requests');
      await parent.page
        .getByRole('main')
        .getByRole('link', { name: common.sessionRequests.list.requestOne })
        .first()
        .click();
      await expect(parent.page).toHaveURL('/requests/new');
      const form = parent.page.getByRole('main');
      await expect(
        form.getByRole('heading', {
          level: 1,
          name: common.sessionRequests.new.title,
        }),
      ).toBeVisible();
      await expect(form).toContainText(studentName);
      const subjectSelect = form.getByLabel(
        common.sessionRequests.new.subject,
        { exact: true },
      );
      await expect(
        subjectSelect.getByRole('option', { name: physics }),
      ).toBeAttached();
      await subjectSelect.selectOption({ label: physics });
      await form
        .getByRole('radio', {
          name: fill(common.sessionRequests.minutes, { minutes: 60 }),
        })
        .check();
      // The week starts today: two "Next week" reach the target's week.
      const next = form.getByRole('button', {
        name: common.sessionRequests.new.nextWeek,
      });
      await next.click();
      await next.click();
      const slotName = fill(common.sessionRequests.new.slot, {
        start: '10:00',
        end: '11:00',
      });
      const slot = form.getByRole('button', { name: slotName, exact: true });
      await expect(slot).toBeVisible();
      // Slots are tutor-agnostic: the tutor is never named to families.
      await expect(parent.page.getByText(TUTOR_NAME)).toHaveCount(0);
      expect(await axeViolations(parent.page)).toEqual([]);

      await slot.click();
      const confirm = parent.page.getByRole('dialog');
      await expect(confirm).toContainText(
        common.sessionRequests.confirm.notBooking,
      );
      await confirm
        .getByLabel(common.sessionRequests.confirm.note, { exact: true })
        .fill(REQUEST_NOTE);
      await confirm
        .getByRole('button', { name: common.sessionRequests.confirm.send })
        .click();
      await expect(confirm).toBeHidden();
      await expect(
        form
          .getByRole('status')
          .filter({ hasText: common.sessionRequests.new.sent }),
      ).toBeVisible();
      // The request holds no time: 10:00 is still offered, as "Requested".
      await expect(
        form.getByRole('button', {
          name: fill(common.sessionRequests.new.requestedSlot, {
            slot: slotName,
          }),
          exact: true,
        }),
      ).toBeDisabled();

      // #60 I5: a second request, 11:00 the same day (no note).
      const lateSlotName = fill(common.sessionRequests.new.slot, {
        start: '11:00',
        end: '12:00',
      });
      await form
        .getByRole('button', { name: lateSlotName, exact: true })
        .click();
      const second = parent.page.getByRole('dialog');
      await second
        .getByRole('button', { name: common.sessionRequests.confirm.send })
        .click();
      await expect(second).toBeHidden();

      await form
        .getByRole('status')
        .getByRole('link', { name: common.sessionRequests.new.viewRequests })
        .click();
      await expect(parent.page).toHaveURL('/requests');
      const cards = parent.page
        .getByRole('main')
        .getByRole('listitem')
        .filter({ hasText: physics });
      await expect(cards).toHaveCount(2);
      const early = cards.filter({ hasText: REQUEST_NOTE });
      const late = cards.filter({ hasNotText: REQUEST_NOTE });
      await expect(early).toContainText(common.sessionRequests.status.pending);
      await expect(late).toContainText(common.sessionRequests.status.pending);
      expect(await axeViolations(parent.page)).toEqual([]);

      // The ids of both requests (newest first), through the guardian's API.
      const mine = await api(parent.page, '/api/v1/session-requests');
      expect(mine.status).toBe(200);
      const items = (
        mine.body as { items: { id: string; starts_at: string }[] }
      ).items;
      expect(items).toHaveLength(2);
      const [lateId, earlyId] = [items[0]!.id, items[1]!.id];
      expect(Date.parse(items[0]!.starts_at)).toBeGreaterThan(
        Date.parse(items[1]!.starts_at),
      );

      // #60 I5: the Owner reviews 10:00 from the inbox and confirms it with
      // the suggested tutor.
      await page.goto('/approvals?type=scheduling.session_request');
      const main = page.getByRole('main');
      await expect(main).toContainText(studentName);
      await main.locator(`a[href="/schedule/requests/${earlyId}"]`).click();
      await expect(page).toHaveURL(`/schedule/requests/${earlyId}`);
      await expect(
        main.getByRole('heading', {
          level: 1,
          name: common.scheduling.requestReview.title,
        }),
      ).toBeVisible();
      await expect(main).toContainText(REQUEST_NOTE);
      await main
        .getByRole('button', {
          name: common.scheduling.tutorSuggestions.button,
        })
        .click();
      const chooseTutor = main.getByRole('button', {
        name: fill(common.scheduling.tutorSuggestions.chooseTutor, {
          name: TUTOR_NAME,
        }),
      });
      await expect(chooseTutor).toBeVisible();
      expect(await axeViolations(page)).toEqual([]);
      await chooseTutor.click();
      await main
        .getByRole('button', {
          name: common.scheduling.requestReview.confirm.submit,
        })
        .click();
      await expect(
        main.getByRole('status').filter({
          hasText: fill(common.scheduling.requestReview.confirmedTo, {
            tutor: TUTOR_NAME,
          }),
        }),
      ).toBeVisible();
      await main
        .getByRole('status')
        .getByRole('link', {
          name: common.scheduling.requestReview.openSession,
        })
        .click();
      await expect(page).toHaveURL(/\/schedule\/sessions\/[0-9a-f-]{36}$/);
      await expect(main).toContainText(common.scheduling.states.offered);
      await expect(main).toContainText(TUTOR_NAME);

      // The Owner rejects 11:00 with a reason the family will see.
      await page.goto(`/schedule/requests/${lateId}`);
      await main
        .getByRole('button', {
          name: common.scheduling.requestReview.reject.open,
        })
        .click();
      const rejectDialog = page.getByRole('dialog');
      await expect(rejectDialog).toContainText(
        common.scheduling.requestReview.reject.description,
      );
      await rejectDialog
        .getByLabel(common.scheduling.requestReview.reject.reason, {
          exact: true,
        })
        .fill(REJECT_REASON);
      await rejectDialog
        .getByRole('button', {
          name: common.scheduling.requestReview.reject.submit,
        })
        .click();
      await expect(rejectDialog).toBeHidden();
      await expect(main).toContainText(REJECT_REASON);
      expect(await cspViolations(page)).toEqual([]);
      expect(ownerErrors).toEqual([]);

      // The guardian reloads: Confirmed and Rejected with the reason, both
      // notifications, and never the tutor's name.
      await parent.page.reload();
      await expect(early).toContainText(
        common.sessionRequests.status.confirmed,
      );
      await expect(early).toContainText(
        common.sessionRequests.list.confirmedHelp,
      );
      await expect(late).toContainText(common.sessionRequests.status.rejected);
      await expect(late).toContainText(REJECT_REASON);
      const [unreadBefore, unreadAfter] =
        common.notifications.bellUnread.split('{{unread}}');
      const escapeText = (text: string) =>
        text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const parentBell = parent.page.getByRole('banner').getByRole('button', {
        name: new RegExp(
          `^${escapeText(unreadBefore!)}[1-9]${escapeText(unreadAfter!)}$`,
        ),
      });
      await parentBell.click();
      const parentPanel = parent.page.getByRole('dialog', {
        name: common.notifications.title,
      });
      const decided = (template: string) =>
        fill(template.split('{{starts_at}}')[0]!, {
          student_name: studentName,
          subject_name: physics,
        });
      await expect(parentPanel).toContainText(
        decided(
          common.notifications.types.scheduling_session_request_confirmed,
        ),
      );
      await expect(parentPanel).toContainText(
        decided(common.notifications.types.scheduling_session_request_rejected),
      );
      await expect(parent.page.getByText(TUTOR_NAME)).toHaveCount(0);
      await parent.page.keyboard.press('Escape');
      await expect(parentPanel).toBeHidden();
      await expect(parent.page.getByText(TUTOR_NAME)).toHaveCount(0);
      expect(await axeViolations(parent.page)).toEqual([]);

      // 4c. (#67 G8) The tutor accepts the offer the Owner's confirm made,
      // through the #52 API in the tutor's context.
      const tutorMe = await api(tutor.page, '/api/v1/auth/me');
      const offers = await api(tutor.page, '/api/v1/assignment-offers');
      expect(offers.status).toBe(200);
      const [offer] = (offers.body as { items: { id: string }[] }).items;
      expect(offer).toBeDefined();
      const accepted = await api(
        tutor.page,
        `/api/v1/assignment-offers/${offer!.id}/accept`,
        { method: 'POST', headers: { 'X-CSRF-Token': csrfTokenOf(tutorMe) } },
      );
      expect(accepted.status).toBe(200);
      // A second child (created and linked by the Owner, no login), so the
      // guardian chooses one; and a student nobody linked to the guardian.
      const siblingId = await createStudentViaApi(page, {
        givenNames: 'Andrés',
        familyNames: `Solicitudes ${suffix}`,
      });
      await linkGuardianViaApi(page, siblingId, guardianId);
      const strangerId = await createStudentViaApi(page, {
        givenNames: 'Extraña',
        familyNames: `Horario ${suffix}`,
      });
      const sessionName = (state: string, who: string) =>
        new RegExp(`${escape(physics)}.*${escape(state)}.*${escape(who)}`);
      const openMySchedule = async (who: Page) => {
        await who.setViewportSize(PHONE);
        await who
          .getByRole('banner')
          .getByRole('button', { name: common.nav.toggle })
          .click();
        await who
          .getByRole('banner')
          .getByRole('link', { name: common.nav.mySchedule, exact: true })
          .click();
        await expect(who).toHaveURL(/\/my-schedule/);
        await expect(
          who.getByRole('heading', { level: 1, name: common.mySchedule.title }),
        ).toBeVisible();
      };
      const calendarOf = (who: Page) =>
        who.getByRole('main').getByRole('region', {
          name: common.mySchedule.regionLabel,
        });

      // The tutor, on a phone: My schedule, List by default, the session.
      const tutorProfile = tutor.page.url();
      const tutorViewport = tutor.page.viewportSize();
      await openMySchedule(tutor.page);
      await tutor.page.goto(`/my-schedule?date=${day.date}`);
      await expect(calendarOf(tutor.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await expect(
        tutor.page.getByRole('main').getByRole('button', {
          name: common.scheduling.calendar.views.list,
          exact: true,
        }),
      ).toHaveAttribute('aria-pressed', 'true');
      await expect(
        calendarOf(tutor.page).getByRole('button', {
          name: sessionName(common.mySchedule.states.confirmed, studentName),
        }),
      ).toHaveCount(1);
      expect(await axeViolations(tutor.page)).toEqual([]);
      // Back to the tutor's profile for step 6.
      if (tutorViewport) {
        await tutor.page.setViewportSize(tutorViewport);
      }
      await tutor.page.goto(tutorProfile);

      // The guardian, on a phone: chooses the child; Confirmed with the
      // tutor's display name. The rejected 11:00 request has no session.
      const parentViewport = parent.page.viewportSize();
      await openMySchedule(parent.page);
      await parent.page.goto(`/my-schedule?date=${day.date}`);
      const whose = parent.page
        .getByRole('main')
        .getByLabel(common.mySchedule.whose.label, { exact: true });
      await whose.selectOption({ label: studentName });
      await expect(parent.page).toHaveURL(/for=[0-9a-f-]{36}/);
      await expect(calendarOf(parent.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      const classes = calendarOf(parent.page).getByRole('button', {
        name: new RegExp(escape(physics)),
      });
      await expect(classes).toHaveCount(1);
      await expect(classes).toHaveAccessibleName(
        sessionName(
          common.mySchedule.states.confirmed,
          fill(common.mySchedule.tutor, { name: TUTOR_NAME }),
        ),
      );
      expect(await axeViolations(parent.page)).toEqual([]);

      // #79 I8: the tutor (phone) opens the class's notes from My schedule,
      // writes an internal note, then writes and shares a second one; the
      // guardian's bell links to My schedule, the class's details show only
      // the shared note, and the family route never returns the internal
      // text.
      const childId = new URL(parent.page.url()).searchParams.get('for')!;
      const internalText = `E2E internal ${suffix}`;
      const sharedText = `E2E shared ${suffix}`;
      const tutorPage = tutor.page.url();
      const tutorSize = tutor.page.viewportSize();
      await tutor.page.setViewportSize(PHONE);
      await tutor.page.goto(`/my-schedule?date=${day.date}`);
      await expect(calendarOf(tutor.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await calendarOf(tutor.page)
        .getByRole('button', {
          name: sessionName(common.mySchedule.states.confirmed, studentName),
        })
        .click();
      await tutor.page
        .getByRole('dialog', { name: physics })
        .getByRole('link', { name: common.notes.open, exact: true })
        .click();
      await expect(tutor.page).toHaveURL(
        /\/my-schedule\/sessions\/[0-9a-f-]{36}\/notes$/,
      );
      const notesMain = tutor.page.getByRole('main');
      await expect(
        notesMain.getByRole('heading', { level: 1, name: common.notes.title }),
      ).toBeVisible();
      const composerBox = notesMain.getByLabel(
        fill(common.notes.composer.label, { student: studentName }),
        { exact: true },
      );
      const saveNote = notesMain.getByRole('button', {
        name: common.notes.composer.save,
      });
      await composerBox.fill(internalText);
      await saveNote.click();
      const internalItem = notesMain
        .getByRole('listitem')
        .filter({ hasText: internalText });
      await expect(internalItem).toContainText(common.notes.badges.internal);
      await composerBox.fill(sharedText);
      await saveNote.click();
      const sharedItem = notesMain
        .getByRole('listitem')
        .filter({ hasText: sharedText });
      await expect(sharedItem).toContainText(common.notes.badges.internal);
      expect(await axeViolations(tutor.page)).toEqual([]);
      await sharedItem
        .getByRole('button', { name: common.notes.actions.share })
        .click();
      const shareDialog = tutor.page.getByRole('dialog', {
        name: common.notes.share.title,
      });
      await expect(shareDialog).toContainText(sharedText);
      expect(await axeViolations(tutor.page)).toEqual([]);
      await shareDialog
        .getByRole('button', { name: common.notes.share.confirm })
        .click();
      await expect(shareDialog).toBeHidden();
      await expect(sharedItem).toContainText(
        common.notes.badges.shared.split('{{date}}')[0]!,
      );
      await expect(internalItem).toContainText(common.notes.badges.internal);
      if (tutorSize) {
        await tutor.page.setViewportSize(tutorSize);
      }
      await tutor.page.goto(tutorPage);

      // The guardian (desktop width, the bell is in the header).
      if (parentViewport) {
        await parent.page.setViewportSize(parentViewport);
      }
      await parent.page.reload();
      await parentBell.click();
      await parentPanel
        .getByRole('button', {
          name: new RegExp(
            `^${escapeText(
              common.notifications.types.academics_learning_note_published.split(
                '{{starts_at}}',
              )[0]!,
            )}`,
          ),
        })
        .click();
      await expect(parent.page).toHaveURL(
        `/my-schedule?view=list&date=${day.date}`,
      );
      await whose.selectOption({ label: studentName });
      await expect(calendarOf(parent.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await classes.click();
      const noteDetails = parent.page.getByRole('dialog', { name: physics });
      await expect(
        noteDetails.getByRole('heading', { name: common.notes.family.title }),
      ).toBeVisible();
      await expect(noteDetails).toContainText(sharedText);
      await expect(noteDetails).not.toContainText(internalText);
      expect(await axeViolations(parent.page)).toEqual([]);
      await parent.page.keyboard.press('Escape');
      await expect(noteDetails).toBeHidden();
      const learning = await api(
        parent.page,
        `/api/v1/students/${childId}/learning-notes`,
      );
      expect(learning.status).toBe(200);
      expect(JSON.stringify(learning.body)).toContain(sharedText);
      expect(JSON.stringify(learning.body)).not.toContain(internalText);
      await parent.page.setViewportSize(PHONE);

      // #308 G3: evaluations. The Owner creates a template (API) and finds
      // it in the list; the tutor (phone) opens the class's details: no
      // "Evaluate" (the class hasn't started, E4), "Academic history" →
      // "New evaluation", saves a draft, then a second evaluation that they
      // finalize. The guardian's bell (desktop width: the header shows only
      // ☰ at 390 px) opens the child's history, which shows the final one
      // in today's school year and never the draft.
      const templateName = `Evaluación E2E ${suffix}`;
      const draftText = `E2E draft ${suffix}`;
      const finalText = `E2E final ${suffix}`;
      const ownerMe = await api(page, '/api/v1/auth/me');
      const template = await api(page, '/api/v1/evaluation-templates', {
        method: 'POST',
        json: {
          name: templateName,
          kind: 'diagnostic',
          subject_id: physicsId,
          criteria: [
            { title: 'Comprensión', max_score: '10' },
            { title: 'Resolución', max_score: '10' },
          ],
        },
        headers: { 'X-CSRF-Token': csrfTokenOf(ownerMe) },
      });
      expect(template.status).toBe(201);
      await page.goto('/academics/evaluation-templates');
      await expect(
        main.getByRole('heading', {
          level: 1,
          name: common.academics.templates.title,
        }),
      ).toBeVisible();
      await expect(
        main.getByRole('link', { name: templateName }),
      ).toBeVisible();
      expect(await axeViolations(page)).toEqual([]);

      const evaluationsPage = tutor.page.url();
      const evaluationsSize = tutor.page.viewportSize();
      await tutor.page.setViewportSize(PHONE);
      await tutor.page.goto(`/my-schedule?date=${day.date}`);
      await expect(calendarOf(tutor.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await calendarOf(tutor.page)
        .getByRole('button', {
          name: sessionName(common.mySchedule.states.confirmed, studentName),
        })
        .click();
      const classDetails = tutor.page.getByRole('dialog', { name: physics });
      const historyLink = classDetails.getByRole('link', {
        name: fill(common.academics.entry.historyOf, { name: studentName }),
      });
      await expect(historyLink).toBeVisible();
      await expect(
        classDetails.getByRole('link', {
          name: fill(common.academics.entry.evaluateName, {
            name: studentName,
          }),
        }),
      ).toHaveCount(0);
      await historyLink.click();
      await expect(tutor.page).toHaveURL(`/students/${childId}/history`);
      const tutorMain = tutor.page.getByRole('main');
      await tutorMain
        .getByRole('link', { name: common.academics.history.newEvaluation })
        .click();
      await expect(tutor.page).toHaveURL(
        `/students/${childId}/evaluations/new`,
      );
      const scores = tutorMain.getByLabel(
        common.academics.evaluations.fields.score,
        { exact: true },
      );
      const startEvaluation = async () => {
        await tutorMain
          .getByLabel(common.academics.evaluations.fields.template, {
            exact: true,
          })
          .selectOption({
            label: fill(common.academics.evaluations.templateOption, {
              name: templateName,
              kind: common.academics.kinds.diagnostic,
              subject: physics,
            }),
          });
        await expect(scores).toHaveCount(2);
      };
      const saveDraft = async (feedback: string) => {
        await tutorMain
          .getByLabel(common.academics.evaluations.fields.feedback, {
            exact: true,
          })
          .fill(feedback);
        await tutorMain
          .getByRole('button', {
            name: common.academics.evaluations.actions.saveDraft,
          })
          .click();
        await expect(tutor.page).toHaveURL(/\/evaluations\/[0-9a-f-]{36}$/);
        await expect(tutorMain.getByRole('status')).toContainText(
          common.academics.evaluations.notices.draftSaved,
        );
      };
      await startEvaluation();
      await scores.nth(0).fill('8');
      await saveDraft(draftText);
      await expect(tutorMain).toContainText(
        common.academics.evaluations.status.draft,
      );
      expect(await axeViolations(tutor.page)).toEqual([]);
      await tutor.page.goto(`/students/${childId}/evaluations/new`);
      await startEvaluation();
      await scores.nth(0).fill('8');
      await scores.nth(1).fill('9.5');
      expect(await axeViolations(tutor.page)).toEqual([]);
      await saveDraft(finalText);
      await tutorMain
        .getByRole('button', {
          name: common.academics.evaluations.actions.finalize,
          exact: true,
        })
        .click();
      const finalizeDialog = tutor.page.getByRole('dialog', {
        name: common.academics.evaluations.finalize.title,
      });
      await expect(finalizeDialog).toContainText(
        fill(common.academics.evaluations.finalize.description, {
          student: studentName,
        }),
      );
      await finalizeDialog
        .getByRole('button', {
          name: common.academics.evaluations.finalize.confirm,
        })
        .click();
      await expect(finalizeDialog).toBeHidden();
      await expect(tutorMain.getByRole('status')).toContainText(
        common.academics.evaluations.notices.finalized,
      );
      await expect(tutorMain).toContainText(
        common.academics.evaluations.status.final,
      );
      // The merged contract sends decimals as strings with two places.
      await expect(tutorMain).toContainText(
        fill(common.academics.evaluations.read.total, {
          total: '17.50',
          max: '20.00',
          percent: '87.50',
        }),
      );
      if (evaluationsSize) {
        await tutor.page.setViewportSize(evaluationsSize);
      }
      await tutor.page.goto(evaluationsPage);

      // The guardian (desktop width, the bell is in the header).
      if (parentViewport) {
        await parent.page.setViewportSize(parentViewport);
      }
      await parent.page.reload();
      await parentBell.click();
      await parentPanel
        .getByRole('button', {
          name: new RegExp(
            `^${escapeText(
              common.notifications.types.academics_evaluation_finalized.split(
                '{{evaluated_on}}',
              )[0]!,
            )}`,
          ),
        })
        .click();
      await expect(parent.page).toHaveURL(`/my-history?student=${childId}`);
      await parent.page.setViewportSize(PHONE);
      const historyMain = parent.page.getByRole('main');
      await expect(
        historyMain.getByRole('heading', {
          level: 1,
          name: common.academics.history.title,
        }),
      ).toBeVisible();
      const periods = await api(
        page,
        `/api/v1/academic-periods?period_type=school_year&on=${centerToday()}`,
      );
      expect(periods.status).toBe(200);
      const [schoolYear] = (periods.body as { items: { name: string }[] })
        .items;
      expect(schoolYear).toBeDefined();
      const yearSection = historyMain.getByRole('region', {
        name: new RegExp(`^${escape(schoolYear!.name)} \\(`),
      });
      await expect(
        yearSection.getByRole('heading', { level: 3, name: physics }),
      ).toBeVisible();
      await expect(
        yearSection.getByRole('article').filter({ hasText: finalText }),
      ).toHaveCount(1);
      await expect(historyMain).not.toContainText(draftText);
      expect(await axeViolations(parent.page)).toEqual([]);
      await historyMain
        .getByRole('button', { name: common.academics.history.views.list })
        .click();
      await expect(
        historyMain.getByRole('article').filter({ hasText: finalText }),
      ).toHaveCount(1);
      await expect(historyMain).not.toContainText(draftText);
      const familyHistory = await api(
        parent.page,
        `/api/v1/students/${childId}/academic-history`,
      );
      expect(familyHistory.status).toBe(200);
      expect(JSON.stringify(familyHistory.body)).toContain(finalText);
      expect(JSON.stringify(familyHistory.body)).not.toContain(draftText);
      const familyWorking = await api(
        parent.page,
        `/api/v1/students/${childId}/evaluations`,
      );
      expect(familyWorking.status).toBe(404);

      // #310 G3: learning plans. The tutor (phone) opens the class's
      // details: "Learning plans of {child}" → the list → "New plan", where
      // only the class's subject is offered (axe on the editor); one
      // objective with two milestones (one with a due date), a third added
      // and removed (3 → 2), saved as a draft. The guardian's API reads
      // never carry the draft. The tutor activates (the dialog names the
      // child) and marks the first milestone Done: 1 of 2 · 50 %. The
      // guardian's bell (desktop width: the header shows only ☰ at 390 px)
      // opens the family page of the plan; at 390 px: Active, the progress,
      // no control (axe); the "Learning plans" nav item opens the list,
      // where the picker chooses the child.
      const planTitle = `E2E plan ${suffix}`;
      const plansPage = tutor.page.url();
      const plansSize = tutor.page.viewportSize();
      await tutor.page.setViewportSize(PHONE);
      await tutor.page.goto(`/my-schedule?date=${day.date}`);
      await expect(calendarOf(tutor.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await calendarOf(tutor.page)
        .getByRole('button', {
          name: sessionName(common.mySchedule.states.confirmed, studentName),
        })
        .click();
      await tutor.page
        .getByRole('dialog', { name: physics })
        .getByRole('link', {
          name: fill(common.learningPlans.entry.linkOf, { name: studentName }),
        })
        .click();
      await expect(tutor.page).toHaveURL(`/students/${childId}/learning-plans`);
      const plansMain = tutor.page.getByRole('main');
      await plansMain
        .getByRole('link', { name: common.learningPlans.new, exact: true })
        .click();
      await expect(tutor.page).toHaveURL(
        `/students/${childId}/learning-plans/new`,
      );
      const planSubject = plansMain.getByLabel(
        common.learningPlans.editor.subject,
        { exact: true },
      );
      // The placeholder and the one subject the tutor teaches the child.
      await expect(planSubject.locator('option')).toHaveCount(2);
      // Labelled with its level (GET /subjects).
      await planSubject.selectOption({
        label: fill(common.academics.subjectOption, {
          subject: physics,
          level: LEVEL,
        }),
      });
      expect(await axeViolations(tutor.page)).toEqual([]);
      const editor = common.learningPlans.editor;
      await plansMain.getByLabel(editor.title, { exact: true }).fill(planTitle);
      await plansMain
        .getByLabel(fill(editor.objectiveTitle, { number: 1 }), {
          exact: true,
        })
        .fill('Comprensión');
      await plansMain
        .getByLabel(fill(editor.milestoneTitle, { position: '1.1' }), {
          exact: true,
        })
        .fill('Lectura diaria');
      const addMilestone = plansMain.getByRole('button', {
        name: fill(editor.addMilestoneTo, {
          name: fill(editor.objectiveNamed, {
            number: 1,
            title: 'Comprensión',
          }),
        }),
      });
      await addMilestone.click();
      await plansMain
        .getByLabel(fill(editor.milestoneTitle, { position: '1.2' }), {
          exact: true,
        })
        .fill('Resumen');
      await plansMain
        .getByLabel(fill(editor.dueOn, { position: '1.2' }), { exact: true })
        .fill(plusDays(centerToday(), 30).date);
      await addMilestone.click();
      const planCounter = (n: number) =>
        fill(editor.planCounter, { n, max: 50 });
      await expect(plansMain).toContainText(planCounter(3));
      await plansMain
        .getByRole('button', {
          name: fill(editor.remove, {
            name: fill(editor.milestoneUnnamed, { position: '1.3' }),
          }),
        })
        .click();
      await expect(plansMain).toContainText(planCounter(2));
      await plansMain.getByRole('button', { name: editor.save }).click();
      await expect(tutor.page).toHaveURL(/\/learning-plans\/[0-9a-f-]{36}$/);
      const planId = new URL(tutor.page.url()).pathname.split('/').pop()!;
      await expect(
        plansMain
          .getByRole('status')
          .filter({ hasText: common.learningPlans.notices.draftSaved }),
      ).toBeVisible();
      await expect(plansMain).toContainText(common.learningPlans.status.draft);

      // The guardian's API reads: the draft is in neither.
      const familyPlans = await api(
        parent.page,
        `/api/v1/students/${childId}/learning-plans`,
      );
      expect(familyPlans.status).toBe(200);
      expect(JSON.stringify(familyPlans.body)).not.toContain(planTitle);
      const familyDraft = await api(
        parent.page,
        `/api/v1/learning-plans/${planId}`,
      );
      expect(familyDraft.status).toBe(404);

      // The tutor activates it and marks the first milestone done.
      await plansMain
        .getByRole('button', {
          name: common.learningPlans.actions.activate,
          exact: true,
        })
        .click();
      const activateDialog = tutor.page.getByRole('dialog', {
        name: common.learningPlans.activate.title,
      });
      await expect(activateDialog).toContainText(
        fill(common.learningPlans.activate.description, {
          student: studentName,
        }),
      );
      await activateDialog
        .getByRole('button', { name: common.learningPlans.activate.confirm })
        .click();
      await expect(activateDialog).toBeHidden();
      await expect(
        plansMain
          .getByRole('status')
          .filter({ hasText: common.learningPlans.notices.activated }),
      ).toBeVisible();
      await expect(plansMain).toContainText(common.learningPlans.status.active);
      await plansMain
        .getByLabel(
          fill(common.learningPlans.statusOf, { title: 'Lectura diaria' }),
          { exact: true },
        )
        .selectOption('done');
      // The merged contract sends the percent as a whole-number string.
      const halfDone = fill(common.learningPlans.progress_other, {
        done: 1,
        total: 2,
        percent: '50',
      });
      await expect(plansMain).toContainText(halfDone);
      if (plansSize) {
        await tutor.page.setViewportSize(plansSize);
      }
      await tutor.page.goto(plansPage);

      // The guardian (desktop width, the bell is in the header).
      if (parentViewport) {
        await parent.page.setViewportSize(parentViewport);
      }
      await parent.page.reload();
      await parentBell.click();
      await parentPanel
        .getByRole('button', {
          name: new RegExp(
            `^${escapeText(
              common.notifications.types.academics_learning_plan_activated,
            )}`,
          ),
        })
        .click();
      await expect(parent.page).toHaveURL(`/my-learning-plans/${planId}`);
      await parent.page.setViewportSize(PHONE);
      const familyPlanMain = parent.page.getByRole('main');
      await expect(
        familyPlanMain.getByRole('heading', { level: 1, name: planTitle }),
      ).toBeVisible();
      await expect(familyPlanMain).toContainText(
        common.learningPlans.status.active,
      );
      await expect(familyPlanMain).toContainText(halfDone);
      await expect(familyPlanMain.getByRole('button')).toHaveCount(0);
      await expect(familyPlanMain.getByRole('combobox')).toHaveCount(0);
      await expect(
        familyPlanMain.getByRole('link', {
          name: common.learningPlans.actions.edit,
        }),
      ).toHaveCount(0);
      expect(await axeViolations(parent.page)).toEqual([]);
      await parent.page
        .getByRole('banner')
        .getByRole('button', { name: common.nav.toggle })
        .click();
      await parent.page
        .getByRole('banner')
        .getByRole('link', { name: common.nav.learningPlans, exact: true })
        .click();
      await expect(parent.page).toHaveURL(/\/my-learning-plans/);
      const whosePlans = familyPlanMain.getByLabel(
        common.learningPlans.family.whose,
        { exact: true },
      );
      await whosePlans.selectOption({ label: studentName });
      await expect(whosePlans).toHaveValue(childId);
      await expect(
        familyPlanMain.getByRole('link', { name: planTitle }),
      ).toBeVisible();
      await parent.page.goto(`/my-schedule?date=${day.date}`);
      await whose.selectOption({ label: studentName });
      await expect(calendarOf(parent.page)).toHaveAttribute(
        'aria-busy',
        'false',
      );

      // #62b E1: the guardian asks to cancel that session (on the phone).
      await classes.click();
      const details = parent.page.getByRole('dialog', { name: physics });
      await details
        .getByRole('button', { name: common.changeRequests.action.request })
        .click();
      const requestDialog = parent.page.getByRole('dialog', {
        name: common.changeRequests.dialog.title,
      });
      await requestDialog
        .getByRole('radio', { name: common.changeRequests.dialog.cancel })
        .check();
      await requestDialog
        .getByRole('button', { name: common.changeRequests.dialog.next })
        .click();
      await expect(
        requestDialog.getByText(common.changeRequests.preview.heading),
      ).toBeVisible();
      await expect(requestDialog).toContainText(
        common.changeRequests.preview.estimate,
      );
      expect(await axeViolations(parent.page)).toEqual([]);
      await requestDialog
        .getByRole('button', { name: common.changeRequests.dialog.send })
        .click();
      await expect(requestDialog).toBeHidden();
      await expect(details).toContainText(common.changeRequests.action.sent);
      await expect(details).toContainText(common.changeRequests.action.pending);
      await parent.page.keyboard.press('Escape');
      await expect(details).toBeHidden();
      const changeRequests = await api(parent.page, '/api/v1/change-requests');
      expect(changeRequests.status).toBe(200);
      const [changeRequest] = (
        changeRequests.body as { items: { id: string; status: string }[] }
      ).items;
      expect(changeRequest?.status).toBe('pending');

      // The Owner follows "Review" from the inbox and approves it as an
      // exception with a reason.
      await page.goto('/approvals?type=scheduling.change_request');
      await main
        .locator(`a[href="/schedule/change-requests/${changeRequest!.id}"]`)
        .click();
      await expect(page).toHaveURL(
        `/schedule/change-requests/${changeRequest!.id}`,
      );
      await expect(
        main.getByRole('heading', {
          level: 1,
          name: common.scheduling.changeRequest.title,
        }),
      ).toBeVisible();
      await main
        .getByRole('button', {
          name: common.scheduling.changeRequest.decide.exception,
          exact: true,
        })
        .click();
      const exceptionDialog = page.getByRole('dialog');
      await exceptionDialog
        .getByLabel(common.scheduling.changeRequest.reason, { exact: true })
        .fill(EXCEPTION_REASON);
      await exceptionDialog
        .getByRole('button', {
          name: common.scheduling.changeRequest.exception.submit,
          exact: true,
        })
        .click();
      await expect(exceptionDialog).toBeHidden();
      await expect(main).toContainText(
        common.scheduling.changeRequest.approvedExceptionText,
      );
      expect(ownerErrors).toEqual([]);

      // The guardian (desktop width, the bell is in the header): the
      // approval notification opens My change requests.
      if (parentViewport) {
        await parent.page.setViewportSize(parentViewport);
      }
      await parent.page.reload();
      const approvedMessage =
        common.notifications.types
          .scheduling_change_request_approved_cancellation_makeup;
      await parentBell.click();
      await parentPanel
        .getByRole('button', {
          name: new RegExp(
            `^${escapeText(approvedMessage.split('{{starts_at}}')[0]!)}`,
          ),
        })
        .click();
      await expect(parent.page).toHaveURL('/change-requests?status=approved');
      const decidedCard = parent.page
        .getByRole('main')
        .getByRole('listitem')
        .filter({ hasText: physics });
      await expect(decidedCard).toHaveCount(1);
      await expect(decidedCard).toContainText(
        fill(common.changeRequests.list.statusText, {
          status: common.scheduling.changeRequest.statuses.approved,
        }),
      );
      await expect(decidedCard).toContainText(
        common.changeRequests.list.approvedException.split('{{makeup}}')[0]!,
      );
      await expect(decidedCard).toContainText(
        common.scheduling.changeRequest.makeupUntil.split('{{date}}')[0]!,
      );
      await expect(decidedCard).toContainText(EXCEPTION_REASON);
      await expect(parent.page.getByText(TUTOR_NAME)).toHaveCount(0);
      expect(await axeViolations(parent.page)).toEqual([]);
      await switchColorScheme(parent.page, 'dark');
      expect(await axeViolations(parent.page)).toEqual([]);
      await switchColorScheme(parent.page, 'light');

      // Another student's schedule: the same 404 as an unknown id.
      const from = `${day.date}T00:00:00Z`;
      const to = `${plusDays(day.date, 7).date}T00:00:00Z`;
      const foreign = await api(
        parent.page,
        `/api/v1/students/${strangerId}/schedule?from=${from}&to=${to}`,
      );
      expect(foreign.status).toBe(404);
      expect((foreign.body as { code: string }).code).toBe('not_found');
      expect(await cspViolations(parent.page)).toEqual([]);
      expect(parent.consoleErrors).toEqual([]);
    } finally {
      await parent.context.close();
    }

    // 5. (#198) The Owner opens the tutor's profile and revokes Física.
    await openTutorProfile(page);
    await qualifications(page)
      .getByRole('button', {
        name: fill(common.tutors.qualifications.revokeFor, {
          subject: physics,
        }),
      })
      .click();
    const revokeDialog = page.getByRole('dialog');
    await expect(revokeDialog).toBeVisible();
    await revokeDialog
      .getByLabel(common.tutors.qualifications.revokeReason, { exact: true })
      .fill(REVOKE_REASON);
    await revokeDialog
      .getByRole('button', {
        name: common.tutors.qualifications.revokeConfirm,
        exact: true,
      })
      .click();
    await expect(revokeDialog).toBeHidden();
    await expect(
      qualifications(page)
        .getByRole('status')
        .filter({ hasText: common.tutors.qualifications.revoked }),
    ).toBeVisible();
    await expect(row(page, physics)).toContainText(
      common.tutors.qualifications.statuses.revoked,
    );
    expect(await cspViolations(page)).toEqual([]);
    expect(ownerErrors).toEqual([]);

    // 6. The tutor reloads: Revoked with the reason, no longer a teaching
    // subject, the revoked message in the bell; requesting it again: Pending.
    await tutor.page.reload();
    await expect(row(tutor.page, physics)).toContainText(
      common.tutors.qualifications.statuses.revoked,
    );
    await expect(row(tutor.page, physics)).toContainText(REVOKE_REASON);
    await expect(
      qualifications(tutor.page)
        .getByRole('listitem')
        .filter({ hasText: physics }),
    ).toHaveCount(0);
    await expect(bell).toBeVisible();
    await bell.click();
    await expect(panel).toContainText(
      fill(common.notifications.types.staff_qualification_revoked, {
        subject_name: physics,
        education_level_name: LEVEL,
      }),
    );
    await tutor.page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    dialog = await requestSubject(tutor.page, LEVEL, physics);
    await expect(dialog).toBeHidden();
    await expect(
      row(tutor.page, physics).filter({
        hasText: common.tutors.qualifications.statuses.pending,
      }),
    ).toHaveCount(1);
    await expect(
      row(tutor.page, physics).filter({
        hasText: common.tutors.qualifications.statuses.revoked,
      }),
    ).toHaveCount(1);
    expect(await cspViolations(tutor.page)).toEqual([]);
    expect(tutor.consoleErrors).toEqual([]);
  } finally {
    await tutor.context.close();
  }
});
