import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, fill, signedInAs } from '../support/catalog';
import { createApprovedTutor, createSubjectViaApi } from '../support/tutors';

// Tutor qualification requests (#44 G1). The demo Owner (the shared state)
// creates two subjects under a seeded level and an approved tutor account.
// The tutor signs in once in a second browser context, requests the first
// subject (Pending), requests it again (qualification_exists in the dialog)
// and requests the second. The Owner approves the first and rejects the
// second with a reason on /approvals. The tutor reloads: the bell has the
// approval message, the rows say Approved and Rejected with its reason, and
// "Teaching subjects" lists the approved one.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 2 logins (the demo Owner's
// own reauthenticated API context for the role change, and the tutor), 0
// redemptions and 1 self-registration (createApprovedTutor). Every
// `make e2e` starts from an empty database, so names are unique per run.

const TUTOR = 'e2e.tutor.qualifications';
const TUTOR_NAME = 'E2E Tutor Calificaciones';
const PASSWORD = 'e2e-tutor-qualifications-password';
const REASON = 'Falta el certificado de química.';
// A seeded level (#32) that no spec deactivates.
const LEVEL = 'Secundaria';

// Chromium logs every 4xx resource as a console error: the signed-out
// /auth/me (401) of the fresh context and the request answered 409
// qualification_exists are expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of (401|409)/;

// Two browsers, a login and two decisions: more than the default 30 s under
// machine load.
const TEST_TIMEOUT_MS = 120_000;

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

async function signInFresh(browser: Browser) {
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
    .fill(PASSWORD);
  await page.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, TUTOR_NAME)),
  ).toBeVisible();
  // A new user lands on the privacy notice first (#174).
  await acceptPrivacyNoticeIfShown(page);
  return { context, page, consoleErrors };
}

const qualifications = (page: Page) =>
  page.getByRole('main').locator('#qualifications');

const row = (page: Page, subject: string) =>
  qualifications(page).getByRole('row').filter({ hasText: subject });

async function openOwnProfile(page: Page) {
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

test('a tutor requests qualifications; the Owner approves one and rejects one', async ({
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
  await createApprovedTutor(page, playwright.request, baseURL!, {
    username: TUTOR,
    displayName: TUTOR_NAME,
    password: PASSWORD,
  });

  const tutor = await signInFresh(browser);
  try {
    // 2. The tutor requests Física: Pending. Again: qualification_exists.
    await openOwnProfile(tutor.page);
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
    expect(await cspViolations(tutor.page)).toEqual([]);
    expect(tutor.consoleErrors).toEqual([]);
  } finally {
    await tutor.context.close();
  }
});
