import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Overriding a soft conflict (#49 G9). The demo Owner (the shared state;
// schedule.manage and schedule.override) creates a student and a subject
// through the API and schedules that student, without tutor or classroom,
// on the first Sunday at least 14 days ahead, 15:00–16:00. The center is
// closed on Sundays, so the panel lists outside_operating_hours labelled
// "Can be overridden"; "Override and schedule" opens the dialog (no axe
// violations with it open), a reason is typed, and the success panel says
// "Scheduled with an override".
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (two API creates with the shared Owner session). It never
// changes the zone, the opening hours or the scheduling policy. The time is
// at or after 09:00 on purpose: settings.spec.ts briefly opens Sunday
// 07:00–08:00 on another worker, and no spec schedules Sunday before 09:00
// (_docs/testing-guidelines.md, E2E data rules).

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;

// Chromium logs every 4xx resource as a console error: the expected 409.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

/** The first Sunday at least 14 days after today, in the center zone. */
function firstSundayAhead(): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 14, 12));
  while (date.getUTCDay() !== 0) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

test('an Owner overrides a soft conflict with a reason', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Excepción ${suffix}`;
  const studentName = `Mateo ${familyNames}`;
  const subjectName = `Materia E2E ${suffix}`;
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

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await createStudentViaApi(page, { givenNames: 'Mateo', familyNames });
  await createSubjectViaApi(page, 'Primaria', subjectName);

  // 1. A Sunday session: outside the opening hours, which can be overridden.
  await page.goto('/schedule/new');
  await expect(
    main.getByRole('heading', { level: 1, name: common.scheduling.title }),
  ).toBeVisible();
  await main
    .getByLabel(common.scheduling.fields.student, { exact: true })
    .fill(familyNames);
  const option = main.getByRole('radio', { name: studentName });
  await expect(option).toBeVisible();
  await option.check();
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await main
    .getByLabel(common.scheduling.fields.date, { exact: true })
    .fill(firstSundayAhead());
  await main
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('15:00');
  await main
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('16:00');
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  const alert = main.getByRole('alert');
  await expect(alert).toContainText(common.scheduling.severity.soft);
  await expect(alert).not.toContainText(common.scheduling.severity.hard);
  await expect(alert.getByRole('heading')).toBeFocused();

  // 2. The dialog: every soft conflict, a required reason; no axe violations.
  const overrideButton = main.getByRole('button', {
    name: common.scheduling.override.action,
    exact: true,
  });
  await overrideButton.click();
  const dialog = page.getByRole('dialog', {
    name: common.scheduling.override.title,
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('listitem')).toHaveCount(1);
  expect(await axeViolations(page)).toEqual([]);

  // 3. A reason, then "Override and schedule": scheduled with an override.
  await dialog
    .getByLabel(common.scheduling.override.reason, { exact: true })
    .fill('Clase de recuperación acordada con la familia.');
  await dialog
    .getByRole('button', {
      name: common.scheduling.override.submit,
      exact: true,
    })
    .click();
  await expect(dialog).toBeHidden();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();
  await expect(
    main.getByText(common.scheduling.override.scheduledWith, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByText(common.scheduling.success.unassigned, { exact: true }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
