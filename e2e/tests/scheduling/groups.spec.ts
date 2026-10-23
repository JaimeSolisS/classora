import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Chromium logs every 4xx resource as a console error: the expected 409.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

// Ongoing group tutoring (#234 G3). The demo Owner (the shared state;
// schedule.manage and schedule.override) creates three students and a
// subject through the API, then:
// 1. "Groups" → "New group": maximum 2, no tutor and no room, weekly on the
//    first Tuesday at least 14 days ahead, 10:00-11:00, ending after 4
//    sessions, with students 1 and 2. The group page says "2 of 2 seats
//    taken" and lists the 4 next sessions.
// 2. "Enroll student" 3: the panel lists group_capacity_exceeded as "Can
//    be overridden" (axe with the dialog open); the Owner overrides it with
//    a reason and the roster shows student 3 "Overridden".
// 3. "Withdraw" student 1 with a reason: the row moves to Withdrawn with it.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (API creates with the shared Owner session). A Tuesday at
// 10:00 is inside the seeded opening hours and far beyond the notice
// window; no tutor and no room, so no tutor or room check applies. It
// never changes the zone, the scheduling policy or the closures.

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const groups = common.groups;
const series = common.scheduling.series;

/** The first Tuesday at least 14 days after today, in the center zone. */
function firstTuesdayAhead(): string {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day + 14, 12));
  while (date.getUTCDay() !== 2) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

function fill(text: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.split(`{{${key}}}`).join(String(value)),
    text,
  );
}

async function pick(
  scope: Locator,
  label: string,
  query: string,
  name: string,
) {
  await scope.getByLabel(label, { exact: true }).fill(query);
  const option = scope.getByRole('radio', { name, exact: true });
  await expect(option).toBeVisible();
  await option.check();
}

test('an Owner creates a group, enrolls over the maximum and withdraws', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Grupo ${suffix}`;
  const [first, second, third] = ['Uno', 'Dos', 'Tres'].map(
    (given) => `${given} ${familyNames}`,
  ) as [string, string, string];
  const subjectName = `Materia grupo ${suffix}`;
  const groupName = `Grupo E2E ${suffix}`;
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
  for (const given of ['Uno', 'Dos', 'Tres']) {
    await createStudentViaApi(page, { givenNames: given, familyNames });
  }
  await createSubjectViaApi(page, 'Primaria', subjectName);

  // 1. Groups → New group.
  await page
    .getByRole('link', { name: common.nav.groups, exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/groups$/);
  await expect(
    main.getByRole('heading', { level: 1, name: groups.title }),
  ).toBeVisible();
  await main.getByRole('link', { name: groups.new, exact: true }).click();
  await expect(
    main.getByRole('heading', { level: 1, name: groups.create.title }),
  ).toBeVisible();
  await main.getByLabel(groups.fields.name, { exact: true }).fill(groupName);
  await main
    .getByLabel(common.scheduling.fields.subject, { exact: true })
    .selectOption({ label: subjectName });
  await main.getByLabel(groups.fields.max, { exact: true }).fill('2');
  await main
    .getByLabel(series.startsOn, { exact: true })
    .fill(firstTuesdayAhead());
  await main
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('10:00');
  await main
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('11:00');
  await expect(
    main.getByRole('checkbox', { name: common.weeklyWindows.weekdays['2'] }),
  ).toBeChecked();
  await main.getByRole('radio', { name: series.endsOptions.count }).check();
  await main.getByLabel(series.count, { exact: true }).fill('4');
  // The initial students: each choice adds a removable chip (the radios
  // never stay checked).
  await main
    .getByLabel(groups.create.addStudent, { exact: true })
    .fill(familyNames);
  for (const name of [first, second]) {
    await main.getByRole('radio', { name, exact: true }).click();
    await expect(
      main.getByRole('button', {
        name: fill(groups.create.remove, { name }),
        exact: true,
      }),
    ).toBeVisible();
  }
  await main
    .getByRole('button', { name: groups.create.submit, exact: true })
    .click();

  await expect(page).toHaveURL(/\/groups\/[0-9a-f-]+$/);
  await expect(
    main.getByRole('heading', { level: 1, name: groupName }),
  ).toBeVisible();
  await expect(main.getByRole('status')).toContainText(
    fill(groups.create.done_other, { count: 4 }),
  );
  await expect(
    main.getByText(fill(groups.capacity.taken, { enrolled: 2, max: 2 })),
  ).toBeVisible();
  await expect(main.locator('a[href^="/schedule/sessions/"]')).toHaveCount(4);

  // 2. Enroll the third student: over the maximum, then an override.
  await main
    .getByRole('button', { name: groups.page.enroll, exact: true })
    .click();
  const enrollDialog = page.getByRole('dialog', { name: groups.enroll.title });
  await pick(enrollDialog, groups.enroll.student, familyNames, third);
  await enrollDialog
    .getByRole('button', { name: groups.enroll.submit, exact: true })
    .click();
  const panel = enrollDialog.getByRole('alert');
  await expect(panel).toContainText(
    common.scheduling.conflicts.group_capacity_exceeded,
  );
  await expect(
    panel.getByText(common.scheduling.severity.soft, { exact: true }),
  ).toHaveCount(1);
  await expect(
    panel.getByRole('heading', { name: groups.conflicts.wholeGroup }),
  ).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(
    results.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
    ),
  ).toEqual([]);
  await enrollDialog
    .getByRole('button', { name: groups.enroll.overrideAction, exact: true })
    .click();
  const overrideDialog = page.getByRole('dialog', {
    name: groups.enroll.overrideAction,
  });
  await overrideDialog
    .getByLabel(common.scheduling.override.reason, { exact: true })
    .fill('Hermano de un alumno del grupo; el aula tiene lugar.');
  await overrideDialog
    .getByRole('button', { name: groups.enroll.overrideAction, exact: true })
    .click();
  await expect(overrideDialog).toBeHidden();
  await expect(enrollDialog).toBeHidden();
  await expect(main.getByRole('status')).toContainText(
    fill(groups.enroll.doneOverride, { name: third }),
  );
  // QA D1: the focus is back on the trigger.
  await expect(
    main.getByRole('button', { name: groups.page.enroll, exact: true }),
  ).toBeFocused();
  await expect(main.getByRole('row').filter({ hasText: third })).toContainText(
    groups.roster.overridden,
  );

  // 3. Withdraw the first student with a reason.
  await main
    .getByRole('button', {
      name: fill(groups.roster.withdrawOf, { name: first }),
      exact: true,
    })
    .click();
  const withdrawDialog = page.getByRole('dialog', {
    name: groups.withdraw.title,
  });
  await withdrawDialog
    .getByLabel(groups.withdraw.reason, { exact: true })
    .fill('Cambió de horario escolar.');
  await withdrawDialog
    .getByRole('button', { name: groups.withdraw.submit, exact: true })
    .click();
  await expect(withdrawDialog).toBeHidden();
  await expect(main.getByRole('status')).toContainText(
    fill(groups.withdraw.done, { name: first }),
  );
  const withdrawn = main.getByRole('table', {
    name: fill(groups.roster.withdrawn_one, { count: 1 }),
  });
  await expect(
    withdrawn.getByRole('row').filter({ hasText: first }),
  ).toContainText('Cambió de horario escolar.');
  // QA D1: the row's button is gone; the notice keeps the focus.
  await expect(main.getByRole('status')).toBeFocused();

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
