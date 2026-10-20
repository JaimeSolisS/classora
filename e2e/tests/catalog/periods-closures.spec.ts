import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, errors } from '../support/catalog';

// Academic periods and closures (#38 F1): the demo Owner's shared session
// adds a school year, meets period_overlap with a second one, adds a
// semester inside it, then adds a holiday on September 16 of that year,
// finds it in that year's filter and deactivates it. The dates are in a
// year unique to the run (2100-2599), so nothing collides with other runs
// or specs; names carry a per-run suffix.

// Chromium logs every 4xx resource as a console error: the expected 409
// period_overlap answer.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

const editNamed = (name: string) =>
  common.catalog.actions.editNamed.replace('{{name}}', name);

function row(page: Page, name: string) {
  return page
    .getByRole('main')
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name, exact: true }) });
}

async function fillDialog(
  page: Page,
  title: string,
  values: { name: string; type?: string; start: string; end: string },
) {
  const dialog = page.getByRole('dialog', { name: title });
  await dialog
    .getByLabel(common.catalog.range.name, { exact: true })
    .fill(values.name);
  if (values.type) {
    await dialog
      .getByLabel(common.catalog.range.type, { exact: true })
      .selectOption(values.type);
  }
  await dialog
    .getByLabel(common.catalog.range.startsOn, { exact: true })
    .fill(values.start);
  await dialog
    .getByLabel(common.catalog.range.endsOn, { exact: true })
    .fill(values.end);
  await dialog
    .getByRole('button', { name: common.catalog.actions.save })
    .click();
  return dialog;
}

test('managing academic periods and closures', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const year = 2100 + (Date.now() % 500);
  const schoolYear = `E2E ${suffix}`;
  const holiday = `E2E Independencia ${suffix}`;
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

  // 1. Catalog from the navigation, then the "Academic periods" tab.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.catalog })
    .click();
  await tabs.getByRole('link', { name: common.catalog.tabs.periods }).click();
  await expect(page).toHaveURL('/catalog/periods');
  await expect(
    tabs.getByRole('link', { name: common.catalog.tabs.periods }),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    main.getByRole('heading', { level: 1, name: common.catalog.periods.title }),
  ).toBeVisible();

  // 2. A school year, August 1 to July 15 of the next year.
  const addPeriod = main.getByRole('button', {
    name: common.catalog.periods.add,
  });
  await addPeriod.click();
  await fillDialog(page, common.catalog.periods.add, {
    name: schoolYear,
    start: `${year}-08-01`,
    end: `${year + 1}-07-15`,
  });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, schoolYear)).toContainText(
    common.catalog.periodTypes.school_year,
  );
  await expect(main.getByRole('status')).toHaveText(common.catalog.saved);

  // 3. A second, overlapping school year: the overlap message.
  await addPeriod.click();
  const overlapping = await fillDialog(page, common.catalog.periods.add, {
    name: `${schoolYear} B`,
    start: `${year + 1}-01-01`,
    end: `${year + 1}-12-31`,
  });
  await expect(overlapping.getByRole('alert')).toContainText(
    errors.period_overlap,
  );
  await overlapping
    .getByRole('button', { name: common.catalog.actions.cancel })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, `${schoolYear} B`)).toHaveCount(0);

  // 4. A semester inside it: accepted.
  await addPeriod.click();
  await fillDialog(page, common.catalog.periods.add, {
    name: `${schoolYear} S1`,
    type: 'semester',
    start: `${year}-08-15`,
    end: `${year}-12-15`,
  });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, `${schoolYear} S1`)).toContainText(
    common.catalog.periodTypes.semester,
  );

  // 5. Closures: a holiday on September 16 of that year, in that year's
  // filter (opened by link: the select offers the current year ± 2).
  await page.goto(`/catalog/closures?year=${year}`);
  await expect(
    main.getByRole('heading', {
      level: 1,
      name: common.catalog.closures.title,
    }),
  ).toBeVisible();
  await expect(
    main.getByLabel(common.catalog.closures.year, { exact: true }),
  ).toHaveValue(String(year));
  await main.getByRole('button', { name: common.catalog.closures.add }).click();
  await fillDialog(page, common.catalog.closures.add, {
    name: holiday,
    start: `${year}-09-16`,
    end: `${year}-09-16`,
  });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, holiday)).toContainText(
    common.catalog.closureTypes.holiday,
  );
  await expect(row(page, holiday)).toContainText(common.catalog.status.active);

  // 6. Deactivate it: "Inactive".
  await main.getByRole('button', { name: editNamed(holiday) }).click();
  await page
    .getByRole('dialog', { name: common.catalog.closures.edit })
    .getByRole('button', { name: common.catalog.actions.deactivate })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row(page, holiday)).toContainText(
    common.catalog.status.inactive,
  );

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
