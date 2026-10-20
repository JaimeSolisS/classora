import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, fill, signedInAs } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createApprovedAccount } from '../support/consent';

// Guardian profiles and student links (#34 K1). The demo Owner's shared
// session (no login) adds a guardian profile linked to a new guardian
// account, and links it to one of two new minor students. The guardian then
// signs in in a fresh context and sees exactly that student, with the
// birthdate and no Guardians section, while the other student is "This
// student doesn't exist." After the Owner unlinks, the guardian's next
// request (same session, no new login) no longer shows the student.
//
// Names carry a unique per-test suffix, and every search includes it.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 1 login (the guardian) and 1
// self-registration (createApprovedAccount: the Owner's shared session
// approves it; no token redemption, #36 rebalance). Every `make e2e` starts
// from an empty database, so the username `e2e.guardians.parent` is unique
// per run.

const PARENT = 'e2e.guardians.parent';
const PARENT_NAME = 'E2E Guardians Parent';
const PASSWORD = 'e2e-guardians-password';

// The guardian's page expects these console errors (Chromium logs every 4xx
// resource; the app reports every failed query, src/app/errors.ts): the
// signed-out /auth/me of the fresh context (401) and the hidden student
// (404). The Owner's page expects none.
const PARENT_EXPECTED_ERRORS = [
  /Failed to load resource: the server responded with a status of (401|404)/,
  /^\[classora\] .*code: not_found, status: 404/,
];

const TEST_TIMEOUT_MS = 90_000;

const g = common.guardians;
const sg = common.students.guardians;

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

async function openNav(page: Page, name: string, path: string, title: string) {
  await page.getByRole('banner').getByRole('link', { name }).click();
  await expect(page).toHaveURL(path);
  await expect(
    page.getByRole('main').getByRole('heading', { level: 1, name: title }),
  ).toBeVisible();
}

test('linking a guardian to a student, and unlinking revokes access', async ({
  page,
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // A random suffix with its own prefix: the fuzzy name search (pg_trgm)
  // must never match another spec's students or guardians (#172 QA D1).
  const sfx = `gdn${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const family = `Ñúñez ${sfx}`;
  const guardianName = `Laura ${family}`;
  const ownName = `Ana Ñúñez ${sfx}`;
  await recordCspViolations(page);
  const consoleErrors = recordConsoleErrors(page);
  const main = page.getByRole('main');

  // Setup through the API, from the app origin with the Owner's shared
  // session: the guardian account (a self-registration the Owner approves:
  // no login, no redemption) and two minor students.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createApprovedAccount(page, playwright.request, baseURL!, {
    username: PARENT,
    displayName: PARENT_NAME,
    password: PASSWORD,
    role: 'guardian',
  });
  const ownId = await createStudentViaApi(page, {
    givenNames: 'Ana',
    familyNames: `Ñúñez ${sfx}`,
  });
  const otherId = await createStudentViaApi(page, {
    givenNames: 'Beto',
    familyNames: `Otro ${sfx}`,
  });

  // 1. Guardians → Add guardian: names, phone, Account = the new account.
  await openNav(page, common.nav.guardians, '/guardians', g.title);
  await main.getByRole('link', { name: g.add }).click();
  await expect(page).toHaveURL('/guardians/new');
  await main.getByLabel(g.fields.givenNames, { exact: true }).fill('Laura');
  await main.getByLabel(g.fields.familyNames, { exact: true }).fill(family);
  await main.getByLabel(g.fields.phone, { exact: true }).fill('55 1234 0000');
  await main.getByRole('searchbox', { name: g.account.search }).fill(PARENT);
  await main.getByRole('radio', { name: new RegExp(`^${PARENT} ·`) }).check();
  await main.getByRole('button', { name: g.save, exact: true }).click();
  await expect(page).toHaveURL(/\/guardians\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: guardianName }),
  ).toBeVisible();
  await expect(main.getByRole('status')).toHaveText(g.saved);
  await expect(main.getByText(PARENT, { exact: true })).toBeVisible();

  // 2. The student's detail → Link guardian: search without accents, choose
  // Laura and "Madre" → Link. The Guardians section lists her.
  await page.goto(`/students/${ownId}`);
  await expect(
    main.getByRole('heading', { level: 1, name: ownName }),
  ).toBeVisible();
  await main.getByRole('button', { name: sg.link, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: sg.linkTitle });
  await dialog
    .getByRole('searchbox', { name: sg.guardian })
    .fill(`nunez ${sfx}`);
  await dialog
    .getByRole('radio', { name: `${family}, Laura`, exact: true })
    .check();
  await dialog
    .getByLabel(sg.relationship, { exact: true })
    .selectOption({ label: common.relationships.mother! });
  await dialog
    .getByRole('button', { name: sg.linkAction, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const section = main.getByRole('region', { name: sg.title });
  await expect(section.getByRole('status')).toHaveText(sg.linked);
  await expect(
    section.getByRole('link', { name: guardianName, exact: true }),
  ).toBeVisible();
  await expect(section).toContainText(common.relationships.mother!);
  expect(await cspViolations(page)).toEqual([]);

  // 3. The guardian signs in in a fresh context (login 2): Students lists
  // exactly their child, with the birthdate and no Guardians section; the
  // other student doesn't exist; Guardians lists only their own profile.
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  try {
    const parent = await context.newPage();
    await recordCspViolations(parent);
    const parentErrors = recordConsoleErrors(parent, PARENT_EXPECTED_ERRORS);
    const parentMain = parent.getByRole('main');
    await parent.goto('/login');
    await parent
      .getByLabel(common.auth.login.username, { exact: true })
      .fill(PARENT);
    await parent
      .getByLabel(common.auth.login.password, { exact: true })
      .fill(PASSWORD);
    await parent
      .getByRole('button', { name: common.auth.login.submit })
      .click();
    await expect(
      parent.getByRole('banner').getByText(signedInAs(common, PARENT_NAME)),
    ).toBeVisible();
    // A new user lands on the privacy notice first (#174).
    await acceptPrivacyNoticeIfShown(parent);

    await openNav(
      parent,
      common.nav.students,
      '/students',
      common.students.title,
    );
    const rows = parentMain.locator('tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByRole('link')).toHaveText(
      `Ñúñez ${sfx}, Ana`,
    );
    await rows.first().getByRole('link').click();
    await expect(
      parentMain.getByRole('heading', { level: 1, name: ownName }),
    ).toBeVisible();
    await expect(parentMain.getByText('2013')).toBeVisible();
    await expect(
      parentMain.getByRole('heading', { level: 2, name: sg.title }),
    ).toHaveCount(0);

    await parent.goto(`/students/${otherId}`);
    await expect(
      parentMain.getByRole('heading', {
        level: 1,
        name: common.students.notFound,
      }),
    ).toBeVisible();

    await openNav(parent, common.nav.guardians, '/guardians', g.title);
    const guardianRows = parentMain.locator('tbody tr');
    await expect(guardianRows).toHaveCount(1);
    await expect(guardianRows.first().getByRole('link')).toHaveText(
      `${family}, Laura`,
    );

    // 4. The Owner unlinks; the guardian's next request (no new login) no
    // longer shows the student.
    await page.goto(`/students/${ownId}`);
    await section
      .getByRole('button', {
        name: fill(sg.unlinkNamed, { name: guardianName }),
      })
      .click();
    const confirm = page.getByRole('dialog', { name: sg.unlinkTitle });
    await confirm.getByRole('button', { name: sg.unlink, exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(section.getByRole('status')).toHaveText(sg.unlinked);

    await parent.goto(`/students/${ownId}`);
    await expect(
      parentMain.getByRole('heading', {
        level: 1,
        name: common.students.notFound,
      }),
    ).toBeVisible();
    await openNav(
      parent,
      common.nav.students,
      '/students',
      common.students.title,
    );
    await expect(
      parentMain.getByText(common.students.list.empty),
    ).toBeVisible();
    await expect(parentMain.locator('tbody tr')).toHaveCount(0);
    expect(await cspViolations(parent)).toEqual([]);
    expect(parentErrors).toEqual([]);
  } finally {
    await context.close();
  }
  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
