import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, errors } from '../support/catalog';

// The academic catalog (#32 K1-K4): the demo Owner's shared session (no
// login, so no login budget) adds a level and a subject, meets name_taken
// and education_level_has_active_subjects, then deactivates the subject and
// the level. Names carry a unique suffix per run, so runs and parallel
// workers never collide; the spec never renames, reorders or deactivates the
// seeded levels. Read access for the other roles is proven by the API's
// runtime permission matrix (K2).

const SEEDED_LEVELS = ['Primaria', 'Secundaria', 'Preparatoria', 'Universidad'];

// Chromium logs every 4xx resource as a console error: the two expected
// 409 answers (name_taken, education_level_has_active_subjects).
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

const editNamed = (name: string) =>
  common.catalog.actions.editNamed.replace('{{name}}', name);

/** The first cell of every row of the page's table. */
function firstCells(page: Page) {
  return page.getByRole('main').locator('tbody tr td:first-child');
}

function row(page: Page, name: string) {
  return page
    .getByRole('main')
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name, exact: true }) });
}

async function openLevels(page: Page) {
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.catalog })
    .click();
  await expect(page).toHaveURL('/catalog/education-levels');
  await expect(
    page
      .getByRole('main')
      .getByRole('heading', { level: 1, name: common.catalog.title }),
  ).toBeVisible();
}

test('managing education levels and subjects', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const levelName = `E2E Nivel ${suffix}`;
  const subjectName = `Álgebra ${suffix}`;
  await recordCspViolations(page);
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
  const main = page.getByRole('main');

  // 1. Catalog from the navigation: the seeded levels first, in order.
  await page.goto('/');
  await openLevels(page);
  await expect(firstCells(page).first()).toHaveText(SEEDED_LEVELS[0]!);
  expect((await firstCells(page).allTextContents()).slice(0, 4)).toEqual(
    SEEDED_LEVELS,
  );

  // 2. Add a level: its row shows 0 active subjects; focus is back on the
  // button.
  const addLevel = main.getByRole('button', {
    name: common.catalog.levels.add,
  });
  await addLevel.click();
  const levelDialog = page.getByRole('dialog', {
    name: common.catalog.levels.add,
  });
  await levelDialog
    .getByLabel(common.catalog.levels.name, { exact: true })
    .fill(levelName);
  await levelDialog
    .getByRole('button', { name: common.catalog.actions.save })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, levelName).getByRole('cell').nth(2)).toHaveText('0');
  await expect(addLevel).toBeFocused();
  await expect(main.getByRole('status')).toHaveText(common.catalog.saved);

  // 3. Open it and add a subject: Active.
  await main.getByRole('link', { name: levelName, exact: true }).click();
  await expect(
    main.getByRole('heading', { level: 1, name: levelName }),
  ).toBeVisible();
  const addSubject = main.getByRole('button', {
    name: common.catalog.subjects.add,
  });
  await addSubject.click();
  let subjectDialog = page.getByRole('dialog', {
    name: common.catalog.subjects.add,
  });
  await subjectDialog
    .getByLabel(common.catalog.levels.name, { exact: true })
    .fill(subjectName);
  await subjectDialog
    .getByRole('button', { name: common.catalog.actions.save })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, subjectName)).toContainText(
    common.catalog.status.active,
  );

  // 4. The same name in capitals: name_taken on the name field.
  await addSubject.click();
  subjectDialog = page.getByRole('dialog', {
    name: common.catalog.subjects.add,
  });
  const nameField = subjectDialog.getByLabel(common.catalog.levels.name, {
    exact: true,
  });
  await nameField.fill(subjectName.toUpperCase());
  await subjectDialog
    .getByRole('button', { name: common.catalog.actions.save })
    .click();
  await expect(nameField).toHaveAttribute('aria-invalid', 'true');
  await expect(nameField).toHaveAccessibleDescription(errors.name_taken);
  await subjectDialog
    .getByRole('button', { name: common.catalog.actions.cancel })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 5. Back on the levels page, deactivating the level is refused.
  await main.getByRole('link', { name: common.catalog.levels.back }).click();
  await main.getByRole('button', { name: editNamed(levelName) }).click();
  let editLevel = page.getByRole('dialog', {
    name: common.catalog.levels.edit,
  });
  await editLevel
    .getByRole('button', { name: common.catalog.actions.deactivate })
    .click();
  await expect(editLevel.getByRole('alert')).toContainText(
    errors.education_level_has_active_subjects,
  );
  await editLevel
    .getByRole('button', { name: common.catalog.actions.cancel })
    .click();

  // 6. Deactivate the subject, then the level; the level page shows the
  // note and "Add subject" is disabled.
  await main.getByRole('link', { name: levelName, exact: true }).click();
  await main.getByRole('button', { name: editNamed(subjectName) }).click();
  await page
    .getByRole('dialog', { name: common.catalog.subjects.edit })
    .getByRole('button', { name: common.catalog.actions.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, subjectName)).toContainText(
    common.catalog.status.inactive,
  );
  await main.getByRole('link', { name: common.catalog.levels.back }).click();
  await main.getByRole('button', { name: editNamed(levelName) }).click();
  editLevel = page.getByRole('dialog', { name: common.catalog.levels.edit });
  await editLevel
    .getByRole('button', { name: common.catalog.actions.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, levelName)).toContainText(
    common.catalog.status.inactive,
  );
  await main.getByRole('link', { name: levelName, exact: true }).click();
  await expect(
    main.getByText(common.catalog.levels.inactiveNote),
  ).toBeVisible();
  await expect(addSubject).toBeDisabled();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
