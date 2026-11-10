import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { switchColorScheme } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import {
  cancelClassSessionViaApi,
  createClassSessionViaApi,
  fillTime,
  firstWeekdayAhead,
} from '../support/scheduling';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Scheduling a makeup from a cancelled session (#436 D3). The demo Owner (the
// shared state) creates a student, a subject and an unassigned session on the
// first weekday D at least 14 days ahead through the API, and cancels it
// there, a center cancellation with a makeup allowed. The session's page
// offers "Schedule makeup"; the form shows the student and subject fixed; a
// session 7 days after D (inside the seeded 30-day window) is scheduled
// without a tutor. The success panel links to the target, the new session
// says what it makes up, and the cancelled one says "Makeup scheduled". Axe
// runs on the form in both schemes.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration. It never changes the zone or a scheduling policy.

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
}

function sevenDaysAfter(date: string): string {
  const day = new Date(`${date}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 7);
  return day.toISOString().slice(0, 10);
}

test('an Owner schedules a makeup from a cancelled session', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Reposición ${suffix}`;
  const studentName = `Marta ${familyNames}`;
  const subjectName = `Materia E2E ${suffix}`;
  await recordCspViolations(page);
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const main = page.getByRole('main');

  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Marta',
    familyNames,
  });
  const subjectId = await createSubjectViaApi(page, 'Primaria', subjectName);
  const date = firstWeekdayAhead();
  const targetId = await createClassSessionViaApi(page, {
    studentId,
    subjectId,
    date,
    start: '10:00',
    end: '11:00',
  });
  await cancelClassSessionViaApi(page, targetId, 'Cierre por mantenimiento.');

  // 1. The cancelled session offers the makeup.
  await page.goto(`/schedule/sessions/${targetId}`);
  await main
    .getByRole('link', { name: common.scheduling.makeup.action, exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/schedule/new\\?makeupOf=${targetId}$`),
  );

  // 2. The student and subject are fixed; no picker, no subject select.
  await expect(main.getByText(studentName, { exact: true })).toBeVisible();
  await expect(main.getByText(subjectName).first()).toBeVisible();
  await expect(
    main.getByLabel(common.scheduling.fields.subject, { exact: true }),
  ).toHaveCount(0);
  await expectNoAxeViolations(page);
  await switchColorScheme(page, 'dark');
  await expectNoAxeViolations(page);
  await switchColorScheme(page, 'light');

  // 3. Seven days later, without a tutor.
  await main
    .getByLabel(common.scheduling.fields.date, { exact: true })
    .fill(sevenDaysAfter(date));
  await fillTime(
    main.getByLabel(common.scheduling.fields.start, { exact: true }),
    '10:00',
  );
  await fillTime(
    main.getByLabel(common.scheduling.fields.end, { exact: true }),
    '11:00',
  );
  await main
    .getByRole('button', { name: common.scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: common.scheduling.success.heading }),
  ).toBeVisible();

  // 4. Each session links to the other.
  await expect(
    main.getByRole('link', {
      name: common.scheduling.makeup.openCancelled,
      exact: true,
    }),
  ).toHaveAttribute('href', `/schedule/sessions/${targetId}`);
  await main
    .getByRole('link', {
      name: common.scheduling.success.openSession,
      exact: true,
    })
    .click();
  await expect(
    main.getByText(common.scheduling.makeup.of, { exact: true }),
  ).toBeVisible();
  await main
    .getByRole('link', {
      name: common.scheduling.makeup.openCancelled,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(new RegExp(`/schedule/sessions/${targetId}$`));
  await expect(
    main.getByText(common.scheduling.makeup.scheduled, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByRole('link', {
      name: common.scheduling.makeup.openMakeup,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    main.getByRole('link', { name: common.scheduling.makeup.action }),
  ).toHaveCount(0);

  expect(pageErrors).toEqual([]);
  expect(await cspViolations(page)).toEqual([]);
});
