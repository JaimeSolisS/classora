import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { createSubjectViaApi } from '../support/tutors';

// Course templates (#64 H1): the demo Owner's shared session creates
// "Curso E2E <suffix>" with one subject (made through the API under the
// seeded "Secundaria" level), 10 sessions of 90 minutes, three modules, one
// required assessment on module 2 and 80% attendance; moves module 3 up
// (the order persists after a reload); sets planned sessions adding up to
// 11 (a row error); meets name_taken with the same name in capitals; and
// deactivates the template, which the "Inactive" filter then lists. Names
// carry a per-run suffix.

// Chromium logs every 4xx resource as a console error: the expected 409
// name_taken answer.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

const t = common.courses.templates;
const numbered = (text: string, number: number) => fill(text, { number });

async function fillNewTemplate(
  page: Page,
  { name, subject }: { name: string; subject: string },
) {
  const main = page.getByRole('main');
  await main.getByLabel(t.name, { exact: true }).fill(name);
  await main
    .getByLabel(fill(t.subjectOption, { name: subject, level: 'Secundaria' }), {
      exact: true,
    })
    .check();
  await main.getByLabel(t.sessionCount, { exact: true }).fill('10');
  await main.getByLabel(t.sessionMinutes, { exact: true }).fill('90');
}

test('managing course templates', async ({ page }, testInfo) => {
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const name = `Curso E2E ${suffix}`;
  const subject = `Materia E2E ${suffix}`;
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
  const save = main.getByRole('button', { name: t.save, exact: true });
  const title = (number: number) =>
    main.getByLabel(numbered(t.moduleTitleLabel, number), { exact: true });

  // 1. A subject through the API, then Courses from the navigation.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createSubjectViaApi(page, 'Secundaria', subject);
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.courses })
    .click();
  await expect(page).toHaveURL('/courses');
  await expect(
    main.getByRole('heading', { level: 1, name: t.title }),
  ).toBeVisible();

  // 2. A new template: three modules, an assessment on module 2, 80%.
  await main.getByRole('link', { name: t.new }).click();
  await expect(page).toHaveURL('/courses/templates/new');
  await expect(
    main.getByRole('heading', { level: 1, name: t.newTitle }),
  ).toBeVisible();
  await fillNewTemplate(page, { name, subject });
  await title(1).fill('Introducción');
  await main.getByRole('button', { name: t.addModule }).click();
  await expect(title(2)).toBeFocused();
  await title(2).fill('Desarrollo');
  await main.getByRole('button', { name: t.addModule }).click();
  await title(3).fill('Cierre');
  await main.getByRole('button', { name: t.addAssessment }).click();
  await main
    .getByLabel(numbered(t.assessmentTitleLabel, 1), { exact: true })
    .fill('Examen parcial');
  await main
    .getByLabel(numbered(t.assessmentModuleLabel, 1), { exact: true })
    .selectOption({ label: 'Desarrollo' });
  await main.getByLabel(t.minAttendance, { exact: true }).fill('80');
  await save.click();
  await expect(page).toHaveURL(/\/courses\/templates\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name, exact: true }),
  ).toBeVisible();
  await expect(main.getByRole('status')).toContainText(t.saved);
  const detailUrl = page.url();

  // 3. Module 3 up: the order persists after a reload.
  await main
    .getByRole('button', { name: numbered(t.moveModuleUpLabel, 3) })
    .click();
  await expect(title(2)).toHaveValue('Cierre');
  await save.click();
  await expect(main.getByRole('status')).toContainText(t.saved);
  await page.reload();
  await expect(title(1)).toHaveValue('Introducción');
  await expect(title(2)).toHaveValue('Cierre');
  await expect(title(3)).toHaveValue('Desarrollo');
  await expect(
    main.getByLabel(numbered(t.assessmentModuleLabel, 1), { exact: true }),
  ).toHaveValue(/.+/);

  // 4. Planned sessions adding up to 11 of 10: a row error, nothing saved.
  const planned = (number: number) =>
    main.getByLabel(numbered(t.plannedSessionsLabel, number), {
      exact: true,
    });
  await planned(1).fill('6');
  await planned(2).fill('5');
  await save.click();
  await expect(planned(2)).toHaveAttribute('aria-invalid', 'true');
  await expect(planned(2)).toHaveAccessibleDescription(
    t.validation.plannedExceedTotal,
  );
  await expect(planned(2)).toBeFocused();

  // 5. The same name in capitals: name_taken on the name.
  await page.goto('/courses/templates/new');
  await fillNewTemplate(page, { name: name.toUpperCase(), subject });
  await title(1).fill('Único');
  await save.click();
  const nameField = main.getByLabel(t.name, { exact: true });
  await expect(nameField).toHaveAttribute('aria-invalid', 'true');
  await expect(nameField).toHaveAccessibleDescription(t.validation.nameTaken);

  // 6. Deactivate it: listed with the "Inactive" filter.
  await page.goto(detailUrl);
  await main.getByRole('button', { name: t.deactivate }).click();
  await expect(
    main.getByText(common.catalog.status.inactive, { exact: true }),
  ).toBeVisible();
  await page.goto('/courses');
  await main
    .getByLabel(t.statusFilter, { exact: true })
    .selectOption({ label: t.statusOptions.inactive });
  await expect(page).toHaveURL('/courses?status=inactive');
  const row = main
    .getByRole('row')
    .filter({ has: page.getByRole('link', { name, exact: true }) });
  await expect(row).toContainText(common.catalog.status.inactive);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
