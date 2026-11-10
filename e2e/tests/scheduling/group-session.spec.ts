import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { switchColorScheme } from '../support/a11y';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { pickOption } from '../support/combobox';
import {
  addDays,
  createCohortViaApi,
  createGroupViaApi,
  firstWeekdayAhead,
} from '../support/scheduling';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// One-off group and course sessions from "Schedule a session" (HF-11b,
// #593). The demo Owner (the shared state; schedule.manage, schedule.view,
// course.view) creates a student and a subject, a group with the student
// (weekly on D, the first weekday at least 14 days ahead, 10:00-11:00) and a
// course template plus cohort with the student (weekly on D's weekday from
// D, 12:00-13:30, 4 sessions: its dates are D to D+21), all through the
// API. Then:
// 1. "Schedule" → "Schedule a session" → Grupo, the group picked by typing,
//    D 16:00-17:00 (free of the group's series), no tutor: "Unassigned"
//    with 1 participant.
// 2. "Schedule another", the same group at 16:30-17:30: the conflict names
//    the student. axe in light and dark; no sideways scroll at 320 px.
// 3. Curso, the cohort, D+7 16:00-17:00 (inside its dates): scheduled.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (API creates with the shared Owner session). It never
// changes the zone, the scheduling policy or the business settings.

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const scheduling = common.scheduling;
const owner = scheduling.create.owner;
const type = scheduling.create.type;

// Chromium logs every 4xx resource as a console error: the expected 409.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

async function fillWhen(page: Page, date: string, start: string, end: string) {
  const main = page.getByRole('main');
  await main.getByLabel(scheduling.fields.date, { exact: true }).fill(date);
  await main.getByLabel(scheduling.fields.start, { exact: true }).fill(start);
  await main.getByLabel(scheduling.fields.end, { exact: true }).fill(end);
}

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(
    results.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
    ),
  ).toEqual([]);
}

test('an Owner schedules one-off group and course sessions', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Grupal ${suffix}`;
  const studentName = `Rita ${familyNames}`;
  const subjectName = `Materia grupal ${suffix}`;
  const groupName = `Grupo extra ${suffix}`;
  const cohortName = `Cohorte extra ${suffix}`;
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
  const date = firstWeekdayAhead();

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Rita',
    familyNames,
  });
  const subjectId = await createSubjectViaApi(page, 'Primaria', subjectName);
  await createGroupViaApi(page, {
    name: groupName,
    subjectId,
    startsOn: date,
    startTime: '10:00',
    studentIds: [studentId],
  });
  await createCohortViaApi(page, {
    name: cohortName,
    subjectId,
    startsOn: date,
    startTime: '12:00',
    studentIds: [studentId],
  });

  // 1. Schedule → "Schedule a session" → Grupo.
  await page
    .getByRole('link', { name: common.nav.schedule, exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/schedule$/);
  await main
    .getByRole('link', {
      name: scheduling.calendar.scheduleSession,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/schedule\/new$/);
  await expect(
    main.getByRole('radio', { name: type.individual, exact: true }),
  ).toBeChecked();
  await main.getByRole('radio', { name: type.group, exact: true }).check();
  await expect(
    main.getByRole('combobox', { name: scheduling.fields.student }),
  ).toHaveCount(0);
  await pickOption(
    main,
    owner.group,
    groupName,
    `${groupName} · ${subjectName}`,
  );
  await expect(
    main.getByText(fill(owner.enrolled_one, { count: 1 }), { exact: true }),
  ).toBeVisible();
  await expect(main.getByText(studentName, { exact: true })).toBeVisible();
  await fillWhen(page, date, '16:00', '17:00');
  await main
    .getByRole('button', { name: scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: owner.groupScheduled }),
  ).toBeVisible();
  await expect(
    main.getByText(scheduling.success.unassigned, { exact: true }),
  ).toBeVisible();
  await expect(
    main.getByText(fill(owner.participantCount_one, { count: 1 }), {
      exact: true,
    }),
  ).toBeVisible();

  // 2. The same group at an overlapping time: the student is named.
  await main
    .getByRole('button', { name: scheduling.success.another, exact: true })
    .click();
  await expect(
    main.getByRole('radio', { name: type.group, exact: true }),
  ).toBeChecked();
  await pickOption(
    main,
    owner.group,
    groupName,
    `${groupName} · ${subjectName}`,
  );
  await expect(main.getByText(studentName, { exact: true })).toBeVisible();
  await fillWhen(page, date, '16:30', '17:30');
  await main
    .getByRole('button', { name: scheduling.submit, exact: true })
    .click();
  const alert = main.getByRole('alert');
  await expect(alert).toContainText(
    fill(owner.studentOverlap, { name: studentName }),
  );
  await expect(alert).toContainText(scheduling.severity.hard);
  await expect(alert.getByRole('heading')).toBeFocused();

  for (const colorScheme of ['light', 'dark'] as const) {
    await switchColorScheme(page, colorScheme);
    await expectNoAxeViolations(page);
  }
  await page.emulateMedia({ colorScheme: 'light' });

  for (const viewport of [
    { width: 320, height: 640 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document.documentElement.scrollWidth <=
            document.documentElement.clientWidth,
        ),
      )
      .toBe(true);
  }
  await page.setViewportSize({ width: 1280, height: 720 });

  // 3. Curso: the cohort, a week later, inside its dates.
  await main.getByRole('radio', { name: type.course, exact: true }).check();
  await pickOption(
    main,
    owner.course,
    cohortName,
    `${cohortName} · ${subjectName}`,
  );
  await expect(main.getByText(studentName, { exact: true })).toBeVisible();
  await fillWhen(page, addDays(date, 7), '16:00', '17:00');
  await main
    .getByRole('button', { name: scheduling.submit, exact: true })
    .click();
  await expect(
    main.getByRole('heading', { name: owner.courseScheduled }),
  ).toBeVisible();
  await expect(
    main.getByText(fill(owner.participantCount_one, { count: 1 }), {
      exact: true,
    }),
  ).toBeVisible();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
