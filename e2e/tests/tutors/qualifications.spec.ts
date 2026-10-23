import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Browser, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
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
// still offers 10:00, now as "Requested". The Owner's Approvals page lists
// the session request. Both family pages get an axe scan.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 3 logins (the demo Owner's
// own reauthenticated API context for the role change, the tutor and the
// guardian), 0 redemptions and 2 self-registrations (createApprovedTutor and
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
const CENTER_ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
// A seeded level (#32) that no spec deactivates.
const LEVEL = 'Secundaria';

// Chromium logs every 4xx resource as a console error: the signed-out
// /auth/me (401) of the fresh context and the request answered 409
// qualification_exists are expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|409)/;

// Three browsers, two logins, two decisions, a session request and a
// revocation: more than the default 30 s under machine load.
const TEST_TIMEOUT_MS = 240_000;

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
 * linked to the student. Returns the student's full name.
 */
async function setUpFamily(
  page: Page,
  request: Parameters<typeof createApprovedAccount>[1],
  baseURL: string,
  tutorId: string,
  weekday: number,
  suffix: string,
): Promise<string> {
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
  return `${givenNames} ${familyNames}`;
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
  await createSubjectViaApi(page, LEVEL, physics);
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
    const studentName = await setUpFamily(
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

      await form
        .getByRole('status')
        .getByRole('link', { name: common.sessionRequests.new.viewRequests })
        .click();
      await expect(parent.page).toHaveURL('/requests');
      const card = parent.page
        .getByRole('main')
        .getByRole('listitem')
        .filter({ hasText: physics });
      await expect(card).toContainText(common.sessionRequests.status.pending);
      await expect(card).toContainText(REQUEST_NOTE);
      expect(await axeViolations(parent.page)).toEqual([]);
      expect(await cspViolations(parent.page)).toEqual([]);
      expect(parent.consoleErrors).toEqual([]);
    } finally {
      await parent.context.close();
    }
    // The Owner's inbox lists the request (approve/reject come with #60).
    await page.goto('/approvals?type=scheduling.session_request');
    await expect(page.getByRole('main')).toContainText(studentName);

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
