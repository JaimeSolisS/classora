import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, fill, signedInAs } from '../support/catalog';
import {
  createFamilyViaApi,
  createGuardianAccountViaPage,
} from '../support/families';
import { createGuardianViaApi, linkGuardianViaApi } from '../support/guardians';
import { createStudentViaApi } from '../support/students';

// Family groupings (#172 J1). Setup through the API with the demo Owner's
// shared session: a guardian account (invited and redeemed without a
// login), its guardian profile, a minor student linked to that profile and
// a second family "Otra <sfx>". Then:
// 1. the Owner adds "Familia Ñúñez <sfx>" and adds the student and the
//    guardian through the detail's dialogs;
// 2. adding the same student to "Otra <sfx>" shows the conflict message;
// 3. the guardian signs in in a fresh context: Families lists only their
//    family, whose detail shows the student and only this guardian, and
//    "Otra <sfx>" doesn't exist;
// 4. the Owner removes the guardian from the family, and the guardian's
//    next request (no new login) no longer finds the family.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 1 login (the guardian) and
// 1 redemption call (the guardian account). Every `make e2e` starts from an
// empty database, so the username `e2e.families.parent` is unique per run.

const PARENT = 'e2e.families.parent';
const PARENT_NAME = 'E2E Families Parent';
const PASSWORD = 'e2e-families-password';

// The guardian's page expects these console errors (Chromium logs every 4xx
// resource; the app reports every failed query, src/app/errors.ts): the
// signed-out /auth/me of the fresh context (401) and the hidden family
// (404). The Owner's page expects the refused add (409).
const PARENT_EXPECTED_ERRORS = [
  /Failed to load resource: the server responded with a status of (401|404)/,
  /^\[classora\] .*code: not_found, status: 404/,
];
const OWNER_EXPECTED_ERRORS = [
  /Failed to load resource: the server responded with a status of 409/,
  /^\[classora\] .*code: family_member_conflict, status: 409/,
];

const TEST_TIMEOUT_MS = 90_000;

const f = common.families;

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

/** Opens the add dialog of `section`, picks `option` and submits. */
async function addMember(
  page: Page,
  section: string,
  add: { button: string; title: string; label: string },
  search: string,
  option: string,
) {
  await page
    .getByRole('main')
    .getByRole('region', { name: section })
    .getByRole('button', { name: add.button, exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: add.title });
  await dialog.getByRole('searchbox', { name: add.label }).fill(search);
  await dialog.getByRole('radio', { name: option, exact: true }).check();
  await dialog.getByRole('button', { name: f.addAction, exact: true }).click();
  return dialog;
}

test('grouping a family, the one-family rule and what the guardian sees', async ({
  page,
  browser,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  // A random suffix with its own prefix, and a member surname no other spec
  // uses: the fuzzy name search (pg_trgm) must never match another spec's
  // students or guardians (#172 QA D1). Only the family is "Ñúñez".
  const sfx = `fam${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const familyName = `Familia Ñúñez ${sfx}`;
  const otherName = `Otra ${sfx}`;
  const surname = `Quiroga ${sfx}`;
  const studentName = `Ana ${surname}`;
  const guardianName = `Laura ${surname}`;
  await recordCspViolations(page);
  const consoleErrors = recordConsoleErrors(page, OWNER_EXPECTED_ERRORS);
  const main = page.getByRole('main');

  // Setup through the API (redemption 1, no login).
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const userId = await createGuardianAccountViaPage(
    page,
    playwright.request,
    baseURL!,
    { username: PARENT, password: PASSWORD, displayName: PARENT_NAME },
  );
  const guardianId = await createGuardianViaApi(page, {
    givenNames: 'Laura',
    familyNames: surname,
    userId,
  });
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Ana',
    familyNames: surname,
  });
  await linkGuardianViaApi(page, studentId, guardianId);
  const otherId = await createFamilyViaApi(page, otherName);

  // 1. Families → Add family → the detail → Add student, Add guardian.
  await openNav(page, common.nav.families, '/families', f.title);
  await main.getByRole('link', { name: f.add }).click();
  await expect(page).toHaveURL('/families/new');
  await main.getByLabel(f.fields.name, { exact: true }).fill(familyName);
  await main.getByRole('button', { name: f.save, exact: true }).click();
  await expect(page).toHaveURL(/\/families\/[0-9a-f-]{36}$/);
  const familyPath = new URL(page.url()).pathname;
  await expect(
    main.getByRole('heading', { level: 1, name: familyName }),
  ).toBeVisible();
  await expect(main.getByRole('status')).toHaveText(f.saved);

  const studentAdd = {
    button: f.students.add,
    title: f.students.addTitle,
    label: f.students.student,
  };
  await addMember(
    page,
    f.students.title,
    studentAdd,
    `quiroga ${sfx}`,
    `${surname}, Ana`,
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toHaveText(f.students.added);
  await addMember(
    page,
    f.guardians.title,
    {
      button: f.guardians.add,
      title: f.guardians.addTitle,
      label: f.guardians.guardian,
    },
    `quiroga ${sfx}`,
    `${surname}, Laura`,
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toHaveText(f.guardians.added);
  await expect(
    main
      .getByRole('region', { name: f.students.title })
      .getByRole('link', { name: studentName, exact: true }),
  ).toBeVisible();
  await expect(
    main
      .getByRole('region', { name: f.guardians.title })
      .getByRole('link', { name: guardianName, exact: true }),
  ).toBeVisible();

  // 2. The same student in "Otra <sfx>" → the conflict message, in the
  // dialog; nothing is added.
  await page.goto(`/families/${otherId}`);
  await expect(
    main.getByRole('heading', { level: 1, name: otherName }),
  ).toBeVisible();
  const conflict = await addMember(
    page,
    f.students.title,
    studentAdd,
    `quiroga ${sfx}`,
    `${surname}, Ana`,
  );
  await expect(conflict.getByRole('alert')).toContainText(f.students.conflict);
  await conflict.getByRole('button', { name: f.cancel, exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByText(f.students.empty)).toBeVisible();
  expect(await cspViolations(page)).toEqual([]);

  // 3. The guardian signs in in a fresh context (the one login).
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

    await openNav(parent, common.nav.families, '/families', f.title);
    const rows = parentMain.locator('tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByRole('link')).toHaveText(familyName);
    await rows.first().getByRole('link').click();
    await expect(
      parentMain.getByRole('heading', { level: 1, name: familyName }),
    ).toBeVisible();
    await expect(
      parentMain
        .getByRole('region', { name: f.students.title })
        .getByRole('link', { name: studentName, exact: true }),
    ).toBeVisible();
    const guardians = parentMain
      .getByRole('region', { name: f.guardians.title })
      .getByRole('listitem');
    await expect(guardians).toHaveCount(1);
    await expect(guardians.first()).toContainText(guardianName);
    await expect(parentMain.getByRole('button')).toHaveCount(0);

    await parent.goto(`/families/${otherId}`);
    await expect(
      parentMain.getByRole('heading', { level: 1, name: f.notFound }),
    ).toBeVisible();

    // 4. The Owner removes the guardian; the guardian's next request (no
    // new login) no longer finds the family.
    await page.goto(familyPath);
    await main
      .getByRole('region', { name: f.guardians.title })
      .getByRole('button', {
        name: fill(f.removeNamed, { name: guardianName }),
      })
      .click();
    const confirm = page.getByRole('dialog', { name: f.removeTitle });
    await confirm.getByRole('button', { name: f.remove, exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(main.getByRole('status')).toHaveText(f.guardians.removed);

    await parent.goto(familyPath);
    await expect(
      parentMain.getByRole('heading', { level: 1, name: f.notFound }),
    ).toBeVisible();
    expect(await cspViolations(parent)).toEqual([]);
    expect(parentErrors).toEqual([]);
  } finally {
    await context.close();
  }
  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
