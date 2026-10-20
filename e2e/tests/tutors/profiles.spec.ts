import { expect, test, type Browser, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, signedInAs } from '../support/catalog';
import { createActiveUser } from '../support/users';

// Tutor profiles (#43 F1). The demo Owner (the shared state) finds a new
// tutor under "Tutores", edits their professional experience and phone.
// Then the tutor signs in: "Tutores" lists only themselves, their form has
// no name field, and changing their own phone succeeds.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 1 login (the tutor) and 1
// redemption call (createActiveUser, which uses the shared Owner session).
// Every `make e2e` starts from an empty database, so the username is unique
// per run.

const TUTOR = 'e2e.tutor.profile';
const TUTOR_NAME = 'E2E Tutor Perfil';
const PASSWORD = 'e2e-tutor-profile-password';

// Chromium logs every 4xx resource as a console error; the signed-out
// /auth/me (401) of the fresh context is expected.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 401/;

// Two browsers and a login: more than the default 30 s under machine load.
const TEST_TIMEOUT_MS = 90_000;

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

async function openTutors(page: Page) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.tutors })
    .click();
  await expect(page).toHaveURL('/tutors');
  await expect(
    page
      .getByRole('main')
      .getByRole('heading', { level: 1, name: common.tutors.title }),
  ).toBeVisible();
}

async function openProfile(page: Page) {
  const main = page.getByRole('main');
  await main.getByRole('link', { name: TUTOR_NAME, exact: true }).click();
  await expect(page).toHaveURL(/\/tutors\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: TUTOR_NAME, exact: true }),
  ).toBeVisible();
}

async function saveForm(page: Page) {
  const main = page.getByRole('main');
  await main.getByRole('button', { name: common.tutors.form.save }).click();
  await expect(
    main.getByRole('status').filter({ hasText: common.tutors.profile.saved }),
  ).toBeVisible();
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

test('an Owner edits a tutor profile; the tutor edits only their own contact', async ({
  page,
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await createActiveUser(playwright.request, baseURL!, {
    username: TUTOR,
    role: 'tutor',
    password: PASSWORD,
    displayName: TUTOR_NAME,
  });

  // 1. The Owner finds the tutor by search and edits experience and phone.
  await recordCspViolations(page);
  const ownerErrors = watchConsole(page);
  await page.goto('/');
  await openTutors(page);
  const main = page.getByRole('main');
  await main.getByLabel(common.tutors.list.search, { exact: true }).fill(TUTOR);
  await expect(page).toHaveURL(/[?&]q=/);
  await openProfile(page);
  await main.getByRole('button', { name: common.tutors.profile.edit }).click();
  await expect(
    main.getByLabel(common.tutors.profile.displayName, { exact: true }),
  ).toHaveValue(TUTOR_NAME);
  await main
    .getByLabel(common.tutors.form.experience, { exact: true })
    .fill('Diez años enseñando matemáticas.');
  await main
    .getByLabel(common.tutors.form.phone, { exact: true })
    .fill('55 1000 2000');
  await saveForm(page);
  await expect(
    main.getByText('Diez años enseñando matemáticas.'),
  ).toBeVisible();
  await expect(main.getByText('55 1000 2000')).toBeVisible();
  expect(await cspViolations(page)).toEqual([]);
  expect(ownerErrors).toEqual([]);

  // 2. The tutor sees only themselves and edits their own phone.
  const tutor = await signInFresh(browser);
  try {
    await openTutors(tutor.page);
    const tutorMain = tutor.page.getByRole('main');
    await expect(tutorMain.locator('tbody tr')).toHaveCount(1);
    await openProfile(tutor.page);
    await expect(
      tutorMain.getByText('Diez años enseñando matemáticas.'),
    ).toBeVisible();
    await tutorMain
      .getByRole('button', { name: common.tutors.profile.edit })
      .click();
    await expect(
      tutorMain.getByLabel(common.tutors.form.phone, { exact: true }),
    ).toHaveValue('55 1000 2000');
    await expect(
      tutorMain.getByLabel(common.tutors.profile.displayName, { exact: true }),
    ).toHaveCount(0);
    await tutorMain
      .getByLabel(common.tutors.form.phone, { exact: true })
      .fill('55 3000 4000');
    await saveForm(tutor.page);
    await expect(tutorMain.getByText('55 3000 4000')).toBeVisible();
    expect(await cspViolations(tutor.page)).toEqual([]);
    expect(tutor.consoleErrors).toEqual([]);
  } finally {
    await tutor.context.close();
  }
});
