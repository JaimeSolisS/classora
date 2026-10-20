import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, errors } from '../support/catalog';

// Classrooms (#39 E1): the demo Owner's shared session adds a classroom
// with capacity 0 (error), then with capacity 12 and two touching Monday
// windows (saved, summary shown), adds an overlapping Monday window (row
// error), meets name_taken with the same name in capitals and deactivates
// the room. Names carry a per-run suffix.

// Chromium logs every 4xx resource as a console error: the expected 409
// name_taken answer.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

const editNamed = (name: string) =>
  common.catalog.actions.editNamed.replace('{{name}}', name);
const numbered = (template: string, number: number) =>
  template.replace('{{number}}', String(number));

function row(page: Page, name: string) {
  return page
    .getByRole('main')
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name, exact: true }) });
}

async function addWindow(
  page: Page,
  title: string,
  number: number,
  values: { weekday: string; start: string; end: string },
) {
  const dialog = page.getByRole('dialog', { name: title });
  await dialog.getByRole('button', { name: common.weeklyWindows.add }).click();
  const day = dialog.getByLabel(
    numbered(common.weeklyWindows.weekdayLabel, number),
    { exact: true },
  );
  await expect(day).toBeFocused();
  await day.selectOption(values.weekday);
  await dialog
    .getByLabel(numbered(common.weeklyWindows.startsAtLabel, number), {
      exact: true,
    })
    .fill(values.start);
  await dialog
    .getByLabel(numbered(common.weeklyWindows.endsAtLabel, number), {
      exact: true,
    })
    .fill(values.end);
}

test('managing classrooms', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Aula E2E ${suffix}`;
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
  const tabs = main.getByRole('navigation', {
    name: common.catalog.tabs.label,
  });
  const save = common.catalog.actions.save;

  // 1. Catalog from the navigation, then the "Classrooms" tab.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.catalog })
    .click();
  await tabs
    .getByRole('link', { name: common.catalog.tabs.classrooms })
    .click();
  await expect(page).toHaveURL('/catalog/classrooms');
  await expect(
    tabs.getByRole('link', { name: common.catalog.tabs.classrooms }),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: common.catalog.classrooms.title,
    }),
  ).toBeVisible();

  // 2. Capacity 0: an error on the field, nothing saved.
  const addRoom = main.getByRole('button', {
    name: common.catalog.classrooms.add,
  });
  await addRoom.click();
  const create = page.getByRole('dialog', {
    name: common.catalog.classrooms.add,
  });
  await create
    .getByLabel(common.catalog.classrooms.name, { exact: true })
    .fill(name);
  const capacity = create.getByLabel(common.catalog.classrooms.capacity, {
    exact: true,
  });
  await capacity.fill('0');
  await create.getByRole('button', { name: save }).click();
  await expect(capacity).toHaveAttribute('aria-invalid', 'true');
  await expect(create).toContainText(
    common.catalog.classrooms.validation.capacityRange,
  );

  // 3. Capacity 12 and two touching Monday windows: saved, summary shown.
  await capacity.fill('12');
  await addWindow(page, common.catalog.classrooms.add, 1, {
    weekday: '1',
    start: '09:00',
    end: '12:00',
  });
  await addWindow(page, common.catalog.classrooms.add, 2, {
    weekday: '1',
    start: '12:00',
    end: '14:00',
  });
  await create.getByRole('button', { name: save }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(main.getByRole('status')).toHaveText(common.catalog.saved);
  await expect(row(page, name)).toContainText('12');
  await expect(row(page, name)).toContainText('Lun 09:00–12:00, 12:00–14:00');

  // 4. An overlapping Monday 11:00-13:00: the error on its row.
  await main.getByRole('button', { name: editNamed(name) }).click();
  const edit = page.getByRole('dialog', {
    name: common.catalog.classrooms.edit,
  });
  await addWindow(page, common.catalog.classrooms.edit, 3, {
    weekday: '1',
    start: '11:00',
    end: '13:00',
  });
  await edit.getByRole('button', { name: save }).click();
  const thirdStart = edit.getByLabel(
    numbered(common.weeklyWindows.startsAtLabel, 3),
    { exact: true },
  );
  await expect(thirdStart).toHaveAttribute('aria-invalid', 'true');
  await expect(thirdStart).toHaveAccessibleDescription(
    common.weeklyWindows.validation.overlap,
  );
  await edit
    .getByRole('button', { name: common.catalog.actions.cancel })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 5. The same name in capitals: name_taken on the name.
  await addRoom.click();
  await create
    .getByLabel(common.catalog.classrooms.name, { exact: true })
    .fill(name.toUpperCase());
  await capacity.fill('5');
  await create.getByRole('button', { name: save }).click();
  const nameField = create.getByLabel(common.catalog.classrooms.name, {
    exact: true,
  });
  await expect(nameField).toHaveAttribute('aria-invalid', 'true');
  await expect(create).toContainText(errors.name_taken);
  await create
    .getByRole('button', { name: common.catalog.actions.cancel })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // 6. Deactivate it: "Inactive".
  await main.getByRole('button', { name: editNamed(name) }).click();
  await edit
    .getByRole('button', { name: common.catalog.actions.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, name)).toContainText(common.catalog.status.inactive);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
