import { randomUUID } from 'node:crypto';
import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, errors, fill, signedInAs } from '../support/catalog';
import { createApprovedAccount, createStudentLogin } from '../support/consent';
import { createGuardianViaApi, linkGuardianViaApi } from '../support/guardians';
import { createStudentViaApi } from '../support/students';

// Guardian consent for a minor's login and an adult's academic access
// (#35 K2, K3). The demo Owner's shared session (no login) sets everything
// up through the API; the parent and the student sign in in fresh contexts.
//
// Names carry a random per-test suffix with its own prefix, so the fuzzy
// name search never matches another spec's rows; assertions are on this
// test's own rows.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 4 logins (2 per test: the
// parent and the student), 3 redemption calls (the minor's invitation page:
// 1 lookup and 2 redemptions; every other account is a self-registration
// approved by the Owner, so it spends none) and 3 registrations (two parents
// and the adult student). Every `make e2e` starts from an empty database, so
// the `e2e.consent.*` usernames are unique per run.

const PASSWORD = 'e2e-consent-password';
const STUDENT_PASSWORD = 'e2e-consent-student-pw';
const TEST_TIMEOUT_MS = 120_000;
// The birthdates as the detail shows them (es, long): createStudentViaApi's
// default minor and the adult's.
const MINOR_BIRTHDATE = '14 de febrero de 2013';
const ADULT_BIRTHDATE = '1 de enero de 2000';

const c = common.consent;

// Chromium logs every 4xx resource; the app reports every failed query
// (src/app/errors.ts). The fresh contexts expect the signed-out /auth/me
// (401), the hidden student (404), the refused redemption (409) and, after
// the suspension, the ended session (401).
const EXPECTED_ERRORS = [
  /Failed to load resource: the server responded with a status of (401|404|409)/,
  /^\[classora\] .*code: (not_found|guardian_consent_required|authentication_required), status: (401|404|409)/,
];

function recordConsoleErrors(
  page: Page,
  expected: readonly RegExp[] = [],
): string[] {
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (
      message.type() === 'error' &&
      !expected.some((pattern) => pattern.test(message.text()))
    ) {
      consoleErrors.push(message.text());
    }
  });
  return consoleErrors;
}

interface Fresh {
  page: Page;
  errors: string[];
  close: () => Promise<void>;
}

async function freshPage(browser: Browser): Promise<Fresh> {
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  const page = await context.newPage();
  await recordCspViolations(page);
  const errors = recordConsoleErrors(page, EXPECTED_ERRORS);
  return { page, errors, close: () => context.close() };
}

/** Signs in through the form, then accepts the privacy notice (#174). */
async function signIn(
  page: Page,
  username: string,
  password: string,
  displayName: string,
): Promise<void> {
  if (!page.url().endsWith('/login')) {
    await page.goto('/login');
  }
  const main = page.getByRole('main');
  await main
    .getByLabel(common.auth.login.username, { exact: true })
    .fill(username);
  await main
    .getByLabel(common.auth.login.password, { exact: true })
    .fill(password);
  await main.getByRole('button', { name: common.auth.login.submit }).click();
  await expect(
    page.getByRole('banner').getByText(signedInAs(common, displayName)),
  ).toBeVisible();
  await acceptPrivacyNoticeIfShown(page);
}

async function openStudent(page: Page, id: string, name: string) {
  await page.goto(`/students/${id}`);
  await expect(
    page.getByRole('main').getByRole('heading', { level: 1, name }),
  ).toBeVisible();
}

test('minor: a guardian allows the login, and withdrawing suspends it', async ({
  page,
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const sfx = `cns${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const parentName = `Padre ${sfx}`;
  const studentName = `Sofía Ñúñez ${sfx}`;
  await recordCspViolations(page);
  const ownerErrors = recordConsoleErrors(page);

  // Setup through the API with the shared Owner session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const sofiaId = await createStudentViaApi(page, {
    givenNames: 'Sofía',
    familyNames: `Ñúñez ${sfx}`,
  });
  const otherId = await createStudentViaApi(page, {
    givenNames: 'Beto',
    familyNames: `Otro ${sfx}`,
  });
  const parentId = await createApprovedAccount(
    page,
    playwright.request,
    baseURL!,
    {
      username: 'e2e.consent.parent',
      displayName: parentName,
      password: PASSWORD,
      role: 'guardian',
    },
  );
  const guardianId = await createGuardianViaApi(page, {
    givenNames: 'Padre',
    familyNames: sfx,
    userId: parentId,
  });
  await linkGuardianViaApi(page, sofiaId, guardianId);
  const link = await createStudentLogin(page, {
    username: 'e2e.consent.student',
    displayName: studentName,
    studentId: sofiaId,
  });

  const student = await freshPage(browser);
  const parent = await freshPage(browser);
  try {
    // 1. B opens the link and sets a password (lookup 1, redemption 1): the
    // gate refuses, and the form stays.
    const studentMain = student.page.getByRole('main');
    await student.page.goto(link);
    await expect(
      studentMain.getByRole('heading', { level: 1, name: common.invite.title }),
    ).toBeVisible();
    await studentMain
      .getByLabel(common.invite.newPassword, { exact: true })
      .fill(STUDENT_PASSWORD);
    await studentMain
      .getByLabel(common.invite.confirmPassword, { exact: true })
      .fill(STUDENT_PASSWORD);
    await studentMain
      .getByRole('button', { name: common.invite.submit })
      .click();
    await expect(
      studentMain.getByText(errors.guardian_consent_required),
    ).toBeVisible();
    await expect(
      studentMain.getByRole('button', { name: common.invite.submit }),
    ).toBeVisible();

    // 2. C, the parent (login 1): Students → Sofía → Consents → allow.
    await signIn(parent.page, 'e2e.consent.parent', PASSWORD, parentName);
    const parentMain = parent.page.getByRole('main');
    await parent.page
      .getByRole('banner')
      .getByRole('link', { name: common.nav.students })
      .click();
    await parentMain
      .getByRole('link', { name: `Ñúñez ${sfx}, Sofía`, exact: true })
      .click();
    await expect(
      parentMain.getByRole('heading', { level: 1, name: studentName }),
    ).toBeVisible();
    const consents = parentMain.getByRole('region', { name: c.title });
    await consents
      .getByRole('button', { name: fill(c.allowLogin, { name: 'Sofía' }) })
      .click();
    const allow = parent.page.getByRole('dialog');
    await allow.getByRole('button', { name: c.confirm, exact: true }).click();
    await expect(parent.page.getByRole('dialog')).toHaveCount(0);
    await expect(consents.getByRole('status')).toHaveText(c.recorded);
    await expect(
      consents.getByText(c.loginGiven.replace(/\{\{date\}\}.*/, '')),
    ).toBeVisible();

    // 3. B submits again (redemption 2) → /login → signs in (login 2): only
    // Sofía, her birthdate, no Guardians section; the other minor is not
    // found.
    await studentMain
      .getByRole('button', { name: common.invite.submit })
      .click();
    await expect(student.page).toHaveURL('/login');
    await signIn(
      student.page,
      'e2e.consent.student',
      STUDENT_PASSWORD,
      studentName,
    );
    await student.page
      .getByRole('banner')
      .getByRole('link', { name: common.nav.students })
      .click();
    const rows = studentMain.locator('tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByRole('link')).toHaveText(
      `Ñúñez ${sfx}, Sofía`,
    );
    await rows.first().getByRole('link').click();
    await expect(
      studentMain.getByRole('heading', { level: 1, name: studentName }),
    ).toBeVisible();
    await expect(studentMain.getByText(MINOR_BIRTHDATE)).toBeVisible();
    await expect(
      studentMain.getByRole('heading', {
        level: 2,
        name: common.students.guardians.title,
      }),
    ).toHaveCount(0);
    await student.page.goto(`/students/${otherId}`);
    await expect(
      studentMain.getByRole('heading', {
        level: 1,
        name: common.students.notFound,
      }),
    ).toBeVisible();

    // 4. C withdraws: the warning names Sofía. B's next load is the login
    // page (the session ended with the suspension).
    await consents.getByRole('button', { name: c.withdraw }).click();
    const withdraw = parent.page.getByRole('dialog', { name: c.revokeTitle });
    await expect(
      withdraw.getByText(fill(c.revokeWarning, { name: 'Sofía' })),
    ).toBeVisible();
    await withdraw.getByRole('button', { name: c.withdraw }).click();
    await expect(parent.page.getByRole('dialog')).toHaveCount(0);
    await expect(consents.getByRole('status')).toContainText(c.revokedStatus);

    await student.page.reload();
    await expect(student.page).toHaveURL(/\/login/);

    expect(await cspViolations(student.page)).toEqual([]);
    expect(await cspViolations(parent.page)).toEqual([]);
    expect(student.errors).toEqual([]);
    expect(parent.errors).toEqual([]);
  } finally {
    await student.close();
    await parent.close();
  }
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);
});

test('adult: the student shares academic records with a guardian', async ({
  page,
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const sfx = `cna${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const parentName = `Madre ${sfx}`;
  const studentName = `Andrés ${sfx}`;
  await recordCspViolations(page);
  const ownerErrors = recordConsoleErrors(page);

  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const andresId = await createStudentViaApi(page, {
    givenNames: 'Andrés',
    familyNames: sfx,
    birthdate: '2000-01-01',
  });
  const parentId = await createApprovedAccount(
    page,
    playwright.request,
    baseURL!,
    {
      username: 'e2e.consent.adultparent',
      displayName: parentName,
      password: PASSWORD,
      role: 'guardian',
    },
  );
  const guardianId = await createGuardianViaApi(page, {
    givenNames: 'Madre',
    familyNames: sfx,
    userId: parentId,
  });
  await linkGuardianViaApi(page, andresId, guardianId);
  // An adult needs no consent: the approval passes the gate.
  await createApprovedAccount(page, playwright.request, baseURL!, {
    username: 'e2e.consent.adult',
    displayName: studentName,
    password: STUDENT_PASSWORD,
    role: 'student',
    studentId: andresId,
  });

  const parent = await freshPage(browser);
  const student = await freshPage(browser);
  try {
    // The parent (login 1) sees Andrés without his birthdate.
    await signIn(parent.page, 'e2e.consent.adultparent', PASSWORD, parentName);
    const parentMain = parent.page.getByRole('main');
    await openStudent(parent.page, andresId, studentName);
    await expect(parentMain.getByText(ADULT_BIRTHDATE)).toHaveCount(0);

    // The student (login 2) opens their own record and shares with her.
    await signIn(
      student.page,
      'e2e.consent.adult',
      STUDENT_PASSWORD,
      studentName,
    );
    await openStudent(student.page, andresId, studentName);
    const consents = student.page
      .getByRole('main')
      .getByRole('region', { name: c.title });
    await consents
      .getByRole('button', { name: `${c.share}: ${parentName}` })
      .click();
    await student.page
      .getByRole('dialog')
      .getByRole('button', { name: c.confirm, exact: true })
      .click();
    await expect(student.page.getByRole('dialog')).toHaveCount(0);
    await expect(consents.getByRole('status')).toHaveText(c.recorded);

    // The parent's same session now sees the birthdate.
    await openStudent(parent.page, andresId, studentName);
    await expect(parentMain.getByText(ADULT_BIRTHDATE)).toBeVisible();

    // Stop sharing: hidden again on the parent's next load.
    await consents
      .getByRole('button', { name: `${c.stopSharing}: ${parentName}` })
      .click();
    await student.page
      .getByRole('dialog')
      .getByRole('button', { name: c.stopSharing, exact: true })
      .click();
    await expect(student.page.getByRole('dialog')).toHaveCount(0);
    await expect(consents.getByRole('status')).toHaveText(c.revokedStatus);
    await openStudent(parent.page, andresId, studentName);
    await expect(parentMain.getByText(ADULT_BIRTHDATE)).toHaveCount(0);

    expect(await cspViolations(student.page)).toEqual([]);
    expect(await cspViolations(parent.page)).toEqual([]);
    expect(student.errors).toEqual([]);
    expect(parent.errors).toEqual([]);
  } finally {
    await student.close();
    await parent.close();
  }
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);
});
