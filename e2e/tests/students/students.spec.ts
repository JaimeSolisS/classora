import { expect, test, type Page } from '@playwright/test';
import {
  acceptPrivacyNoticeIfShown,
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, fill, signedInAs } from '../support/catalog';
import { createActiveUser } from '../support/users';

// Student profiles (#33 L1, L2). The demo Owner's shared session (no login)
// adds a student with an emergency contact, finds them with an accent-free
// search in either word order, edits the contacts (add, move up, remove),
// and deactivates the student. A Tutor without assignments then signs in in
// a fresh context and can't see the student ("This student doesn't exist.")
// or any student in the list.
//
// Names carry a unique per-test suffix, and every search includes it, so
// parallel tests and runs never see each other's students.
//
// Budgets (#27, ADR-0008; see support/auth.ts): 2 logins (1 by
// createActiveUser, 1 for the tutor) and 1 redemption call
// (createActiveUser). Every `make e2e` starts from an empty database, so the
// username `e2e.students.tutor` is unique per run.

const TUTOR = 'e2e.students.tutor';
const TUTOR_NAME = 'E2E Students Tutor';
const PASSWORD = 'e2e-students-password';

// The tutor's page expects these console errors (Chromium logs every 4xx
// resource; the app reports every failed query, src/app/errors.ts): the
// signed-out /auth/me of the fresh context (401) and the hidden student
// (404). The Owner's page expects none.
const TUTOR_EXPECTED_ERRORS = [
  /Failed to load resource: the server responded with a status of (401|404)/,
  /^\[classora\] .*code: not_found, status: 404/,
];

const TEST_TIMEOUT_MS = 90_000;

const s = common.students;
const contactLabel = (
  key: 'nameLabel' | 'relationshipLabel' | 'phoneLabel',
  n: number,
) => fill(s.contacts[key], { n });

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

async function openStudents(page: Page) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.students })
    .click();
  await expect(page).toHaveURL('/students');
  await expect(
    page.getByRole('main').getByRole('heading', { level: 1, name: s.title }),
  ).toBeVisible();
}

async function search(page: Page, text: string) {
  const main = page.getByRole('main');
  await main.getByLabel(s.list.search, { exact: true }).fill(text);
  await expect(page).toHaveURL(
    new RegExp(`[?&]q=${encodeURIComponent(text).replaceAll('%20', '\\+')}`),
  );
}

test('managing a student profile, and a tutor who may not see it', async ({
  page,
  browser,
  playwright,
  baseURL,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const sfx = `e2e${testInfo.workerIndex}${Date.now().toString(36)}`;
  const family = `Ñúñez Pérez ${sfx}`;
  const fullName = `José ${family}`;
  const listName = `${family}, José`;
  await recordCspViolations(page);
  const consoleErrors = recordConsoleErrors(page);
  const main = page.getByRole('main');

  // 1. Students → Add student → Save: the detail shows the name, the Minor
  // badge, the level and the contact.
  await page.goto('/');
  await openStudents(page);
  await main.getByRole('link', { name: s.add }).click();
  await expect(page).toHaveURL('/students/new');
  await main.getByLabel(s.fields.givenNames, { exact: true }).fill('José');
  await main.getByLabel(s.fields.familyNames, { exact: true }).fill(family);
  await main.getByLabel(s.fields.birthdate, { exact: true }).fill('2012-05-04');
  await main
    .getByLabel(s.fields.level, { exact: true })
    .selectOption({ label: 'Secundaria' });
  await main.getByLabel(contactLabel('nameLabel', 1)).fill('María Pérez');
  await main.getByLabel(contactLabel('relationshipLabel', 1)).fill('Madre');
  await main.getByLabel(contactLabel('phoneLabel', 1)).fill('+52 55 1234 5678');
  await main.getByRole('button', { name: s.save, exact: true }).click();

  await expect(page).toHaveURL(/\/students\/[0-9a-f-]{36}$/);
  const studentPath = new URL(page.url()).pathname;
  await expect(
    main.getByRole('heading', { level: 1, name: fullName }),
  ).toBeVisible();
  // The page's own status (the Guardians section, #34, has its own).
  await expect(main.getByRole('status').first()).toHaveText(s.saved);
  await expect(main.getByText(s.badge.minor, { exact: true })).toBeVisible();
  await expect(main.getByText('Secundaria', { exact: true })).toBeVisible();
  await expect(main.getByText('María Pérez')).toBeVisible();

  // 2. Search without accents, in either order: this student's row (other
  // specs' students may also match the fuzzy search, so only this row is
  // asserted, #172 QA D1).
  await openStudents(page);
  await search(page, `jose nunez ${sfx}`);
  const rows = main.locator('tbody tr');
  const ownRow = rows.filter({
    has: page.getByRole('link', { name: listName, exact: true }),
  });
  await expect(ownRow).toHaveCount(1);
  await search(page, `NUNEZ JOSE ${sfx}`);
  await expect(ownRow).toHaveCount(1);

  // 3. Edit: add Luis, move him up, remove María → only Luis remains (his
  // Remove is disabled) → Save.
  await ownRow.getByRole('link').click();
  await main.getByRole('link', { name: s.edit, exact: true }).click();
  await expect(page).toHaveURL(`${studentPath}/edit`);
  await main.getByRole('button', { name: s.contacts.add }).click();
  await expect(main.getByLabel(contactLabel('nameLabel', 2))).toBeFocused();
  await main.getByLabel(contactLabel('nameLabel', 2)).fill('Luis Ñúñez');
  await main.getByLabel(contactLabel('relationshipLabel', 2)).fill('Padre');
  await main.getByLabel(contactLabel('phoneLabel', 2)).fill('55 8765 4321');
  await main
    .getByRole('button', { name: fill(s.contacts.moveUpNamed, { n: 2 }) })
    .click();
  await expect(main.getByLabel(contactLabel('nameLabel', 1))).toHaveValue(
    'Luis Ñúñez',
  );
  await main
    .getByRole('button', { name: fill(s.contacts.removeNamed, { n: 2 }) })
    .click();
  await expect(main.getByLabel(contactLabel('nameLabel', 2))).toHaveCount(0);
  await expect(
    main.getByRole('button', { name: fill(s.contacts.removeNamed, { n: 1 }) }),
  ).toBeDisabled();
  await main.getByRole('button', { name: s.save, exact: true }).click();
  await expect(page).toHaveURL(studentPath);
  // The page's own status (the Guardians section, #34, has its own).
  await expect(main.getByRole('status').first()).toHaveText(s.saved);
  const contacts = main.locator('ol li');
  await expect(contacts).toHaveCount(1);
  await expect(contacts.first()).toContainText('Luis Ñúñez');
  await expect(main.getByText('María Pérez')).toHaveCount(0);

  // 4. Deactivate → confirm → Inactive. The default list hides the student;
  // status "All" shows them as Inactive.
  await main
    .getByRole('button', { name: s.deactivate.action, exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: s.deactivate.title });
  await dialog
    .getByRole('button', { name: s.deactivate.action, exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    main.getByText(s.status.inactive, { exact: true }),
  ).toBeVisible();
  await openStudents(page);
  await search(page, sfx);
  await expect(
    main.getByRole('heading', { level: 1, name: s.title }),
  ).toBeVisible();
  // Waits for the search result: the stale unfiltered list never holds this
  // inactive student, so the row check alone would pass too early.
  await expect(main.getByText(s.list.noResults)).toBeVisible();
  await expect(ownRow).toHaveCount(0);
  await main
    .getByLabel(s.list.status, { exact: true })
    .selectOption({ label: s.list.statusAll });
  await expect(ownRow).toHaveCount(1);
  await expect(ownRow).toContainText(s.status.inactive);
  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);

  // 5. A Tutor without assignments (login 1 by createActiveUser, login 2
  // here) sees neither this student nor any other.
  await createActiveUser(playwright.request, baseURL!, {
    username: TUTOR,
    role: 'tutor',
    password: PASSWORD,
    displayName: TUTOR_NAME,
  });
  const context = await browser.newContext({ storageState: SIGNED_OUT_STATE });
  try {
    const tutor = await context.newPage();
    await recordCspViolations(tutor);
    const tutorErrors = recordConsoleErrors(tutor, TUTOR_EXPECTED_ERRORS);
    await tutor.goto('/login');
    await tutor
      .getByLabel(common.auth.login.username, { exact: true })
      .fill(TUTOR);
    await tutor
      .getByLabel(common.auth.login.password, { exact: true })
      .fill(PASSWORD);
    await tutor.getByRole('button', { name: common.auth.login.submit }).click();
    await expect(
      tutor.getByRole('banner').getByText(signedInAs(common, TUTOR_NAME)),
    ).toBeVisible();
    // A new user lands on the privacy notice first (#174).
    await acceptPrivacyNoticeIfShown(tutor);

    await tutor.goto(studentPath);
    await expect(
      tutor
        .getByRole('main')
        .getByRole('heading', { level: 1, name: s.notFound }),
    ).toBeVisible();
    await openStudents(tutor);
    await expect(tutor.getByRole('main').getByText(s.list.empty)).toBeVisible();
    await expect(tutor.getByRole('main').locator('tbody tr')).toHaveCount(0);
    expect(await cspViolations(tutor)).toEqual([]);
    expect(tutorErrors).toEqual([]);
  } finally {
    await context.close();
  }
});
