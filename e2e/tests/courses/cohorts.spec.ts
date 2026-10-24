import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Course cohorts (#243 F1). The demo Owner's shared session creates a
// subject (under the seeded "Secundaria" level) and a course template
// "Curso cohorte <suffix>" (4 sessions of 90 minutes, modules
// "Introducción" and "Cierre") through the API, then:
// 1. Courses → "Cohorts" tab → "New cohort": the template (its only
//    subject preselected), maximum 10, no tutor and no room, weekly on the
//    first Tuesday at least 14 days ahead at 10:00 (axe on the form). The
//    cohort page says "Cohort created", shows the frozen syllabus, "No
//    tutor yet" and the next sessions (axe on the page).
// 2. The template's first module is renamed through the API; after a
//    reload the cohort still shows "Introducción": its syllabus is the
//    snapshot, never the live template.
//
// Cohort enrollment (#245 F1), a second test: a cohort with a maximum of 1
// (no tutor, no room, weekly on that Tuesday at 10:00) and two students
// "Uno/Dos Cohorte <suffix>" are created through the API, then in the
// Students panel: enroll Uno (1 of 1, "Full"); enroll Dos → the 409 panel
// (the cohort is full, "Whole cohort") → "Override and enroll" with a
// reason → "Overridden"; withdraw Uno with a reason; "Show withdrawn" lists
// it with the reason. Axe on the panel and the dialogs. Never demo
// students.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (API calls with the shared Owner session). A Tuesday at
// 10:00 is inside the seeded opening hours and beyond the notice window,
// never a Sunday (the #249 data rule); no tutor and no room, so no tutor or
// room check applies. It never changes the zone, the policies or the
// closures.

const ZONE = 'America/Mexico_City';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const cohorts = common.courses.cohorts;
const templates = common.courses.templates;
// Chromium logs every 4xx resource as a console error: the expected 409.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

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

type TemplateDetail = {
  id: string;
  version: number;
  name: string;
  description: string | null;
  subjects: { id: string }[];
  session_count: number;
  session_minutes: number;
  min_attendance_percent: number | null;
  modules: {
    id: string;
    title: string;
    description: string | null;
    subject_id: string | null;
    planned_sessions: number | null;
    is_required: boolean;
    materials: string | null;
  }[];
};

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

async function createTemplateViaApi(
  page: Page,
  name: string,
  subjectId: string,
): Promise<string> {
  const created = await api(page, '/api/v1/course-templates', {
    method: 'POST',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name,
      subject_ids: [subjectId],
      session_count: 4,
      session_minutes: 90,
      modules: [
        { title: 'Introducción', is_required: true },
        { title: 'Cierre', is_required: false },
      ],
    },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** Renames the template's first module (a new template version). */
async function renameFirstModuleViaApi(page: Page, id: string, title: string) {
  const read = await api(page, `/api/v1/course-templates/${id}`);
  expect(read.status).toBe(200);
  const template = read.body as TemplateDetail;
  const replaced = await api(page, `/api/v1/course-templates/${id}`, {
    method: 'PUT',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name: template.name,
      description: template.description,
      subject_ids: template.subjects.map((subject) => subject.id),
      session_count: template.session_count,
      session_minutes: template.session_minutes,
      min_attendance_percent: template.min_attendance_percent,
      modules: template.modules.map((module, index) => ({
        id: module.id,
        title: index === 0 ? title : module.title,
        description: module.description,
        subject_id: module.subject_id,
        planned_sessions: module.planned_sessions,
        is_required: module.is_required,
        materials: module.materials,
      })),
      assessments: [],
      version: template.version,
    },
  });
  expect(replaced.status).toBe(200);
}

/** A cohort through the API: no tutor, no room, weekly on `startsOn`. */
async function createCohortViaApi(
  page: Page,
  {
    name,
    templateId,
    subjectId,
    maxStudents,
    startsOn,
  }: {
    name: string;
    templateId: string;
    subjectId: string;
    maxStudents: number;
    startsOn: string;
  },
): Promise<string> {
  const created = await api(page, '/api/v1/cohorts', {
    method: 'POST',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name,
      template_id: templateId,
      subject_id: subjectId,
      max_students: maxStudents,
      starts_on: startsOn,
      start_time: '10:00',
      // ISO weekday of a Tuesday.
      recurrence: { weekdays: [2], interval: 1 },
    },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** Searches the picker and checks the student's option. */
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

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(
    results.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
    ),
  ).toEqual([]);
}

test('an Owner creates a cohort whose syllabus stays frozen', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const subjectName = `Materia cohorte ${suffix}`;
  const templateName = `Curso cohorte ${suffix}`;
  const cohortName = `Cohorte E2E ${suffix}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const firstModule = (title: string) =>
    main.getByRole('heading', {
      name: `${fill(templates.moduleHeading, { number: 1 })}: ${title}`,
      exact: true,
    });

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const subjectId = await createSubjectViaApi(page, 'Secundaria', subjectName);
  const templateId = await createTemplateViaApi(page, templateName, subjectId);

  // 1. Courses → Cohorts → New cohort.
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.courses })
    .click();
  await expect(page).toHaveURL('/courses');
  const tabs = main.getByRole('navigation', {
    name: common.courses.tabs.label,
  });
  await expect(
    tabs.getByRole('link', { name: common.courses.tabs.templates }),
  ).toHaveAttribute('aria-current', 'page');
  await tabs.getByRole('link', { name: common.courses.tabs.cohorts }).click();
  await expect(page).toHaveURL('/courses/cohorts');
  await expect(
    main.getByRole('heading', { level: 1, name: cohorts.title }),
  ).toBeVisible();
  await main.getByRole('link', { name: cohorts.new, exact: true }).click();
  await expect(page).toHaveURL('/courses/cohorts/new');
  await expect(
    main.getByRole('heading', { level: 1, name: cohorts.create.title }),
  ).toBeVisible();
  await main.getByLabel(cohorts.fields.name, { exact: true }).fill(cohortName);
  await main
    .getByLabel(cohorts.fields.template, { exact: true })
    .selectOption({ label: templateName });
  await expect(
    main.getByLabel(common.scheduling.fields.subject, { exact: true }),
  ).toHaveValue(subjectId);
  await main.getByLabel(cohorts.fields.max, { exact: true }).fill('10');
  await main
    .getByLabel(common.scheduling.series.startsOn, { exact: true })
    .fill(firstTuesdayAhead());
  await main
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('10:00');
  await expect(
    main.getByRole('checkbox', { name: common.weeklyWindows.weekdays['2'] }),
  ).toBeChecked();
  await expectNoAxeViolations(page);
  await main
    .getByRole('button', { name: cohorts.create.submit, exact: true })
    .click();

  await expect(page).toHaveURL(/\/courses\/cohorts\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: cohortName }),
  ).toBeVisible();
  // QA D1: the offer panel has its own (empty) live region; match the notice.
  await expect(
    main.getByRole('status').filter({ hasText: cohorts.create.done }),
  ).toBeVisible();
  await expect(
    main.getByRole('heading', { name: cohorts.syllabus.heading }),
  ).toBeVisible();
  await expect(firstModule('Introducción')).toBeVisible();
  await expect(main.getByText(cohorts.assignment.noTutor)).toBeVisible();
  await expect(
    main
      .getByRole('list', { name: cohorts.next.heading })
      .locator('a[href^="/schedule/sessions/"]')
      .first(),
  ).toBeVisible();
  await expectNoAxeViolations(page);

  // 2. A template edit never changes the cohort's syllabus.
  await renameFirstModuleViaApi(page, templateId, 'Introducción editada');
  await page.reload();
  await expect(
    main.getByRole('heading', { level: 1, name: cohortName }),
  ).toBeVisible();
  await expect(firstModule('Introducción')).toBeVisible();
  await expect(firstModule('Introducción editada')).toHaveCount(0);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('an Owner enrolls over the maximum with an override, then withdraws', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const enrollment = cohorts.enrollment;
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Cohorte ${suffix}`;
  const [first, second] = ['Uno', 'Dos'].map(
    (given) => `${given} ${familyNames}`,
  ) as [string, string];
  const cohortName = `Cohorte inscripción ${suffix}`;
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
  const notice = (text: string) =>
    main.getByRole('status').filter({ hasText: text });

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const subjectId = await createSubjectViaApi(
    page,
    'Secundaria',
    `Materia inscripción ${suffix}`,
  );
  const templateId = await createTemplateViaApi(
    page,
    `Curso inscripción ${suffix}`,
    subjectId,
  );
  const cohortId = await createCohortViaApi(page, {
    name: cohortName,
    templateId,
    subjectId,
    maxStudents: 1,
    startsOn: firstTuesdayAhead(),
  });
  for (const given of ['Uno', 'Dos']) {
    await createStudentViaApi(page, { givenNames: given, familyNames });
  }

  await page.goto(`/courses/cohorts/${cohortId}`);
  await expect(
    main.getByRole('heading', { level: 1, name: cohortName }),
  ).toBeVisible();
  const panel = main.getByRole('region', { name: enrollment.heading });
  await expect(panel).toContainText(
    fill(enrollment.capacity, { enrolled: 0, max: 1 }),
  );

  // 1. Enroll the first student: the cohort is full.
  await panel
    .getByRole('button', { name: enrollment.enroll, exact: true })
    .click();
  const enrollDialog = page.getByRole('dialog', {
    name: enrollment.dialog.title,
  });
  await pick(enrollDialog, common.groups.enroll.student, familyNames, first);
  const enrolled = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().endsWith(`/api/v1/cohorts/${cohortId}/enrollments`),
  );
  await enrollDialog
    .getByRole('button', { name: enrollment.dialog.submit, exact: true })
    .click();
  expect((await enrolled).status()).toBe(201);
  await expect(enrollDialog).toBeHidden();
  await expect(
    notice(fill(enrollment.dialog.done, { name: first })),
  ).toBeVisible();
  await expect(panel).toContainText(
    fill(enrollment.capacity, { enrolled: 1, max: 1 }),
  );
  await expect(panel.getByText(enrollment.full, { exact: true })).toBeVisible();

  // 2. Enroll the second: over the maximum, then an override.
  await panel
    .getByRole('button', { name: enrollment.enroll, exact: true })
    .click();
  await pick(enrollDialog, common.groups.enroll.student, familyNames, second);
  await enrollDialog
    .getByRole('button', { name: enrollment.dialog.submit, exact: true })
    .click();
  const conflicts = enrollDialog.getByRole('alert');
  await expect(conflicts).toContainText(
    common.scheduling.conflicts.cohort_capacity_exceeded,
  );
  await expect(
    conflicts.getByRole('heading', { name: enrollment.dialog.wholeCohort }),
  ).toBeVisible();
  await expect(
    conflicts.getByText(common.scheduling.severity.soft, { exact: true }),
  ).toHaveCount(1);
  await expectNoAxeViolations(page);
  await enrollDialog
    .getByRole('button', {
      name: enrollment.dialog.overrideAction,
      exact: true,
    })
    .click();
  const overrideDialog = page.getByRole('dialog', {
    name: enrollment.dialog.overrideAction,
  });
  await overrideDialog
    .getByLabel(common.scheduling.override.reason, { exact: true })
    .fill('Hermano de una alumna inscrita; el aula tiene lugar.');
  await overrideDialog
    .getByRole('button', {
      name: enrollment.dialog.overrideAction,
      exact: true,
    })
    .click();
  await expect(overrideDialog).toBeHidden();
  await expect(enrollDialog).toBeHidden();
  await expect(
    notice(fill(enrollment.dialog.done, { name: second })),
  ).toBeVisible();
  await expect(
    panel.getByRole('row').filter({ hasText: second }),
  ).toContainText(enrollment.overridden);
  await expect(panel).toContainText(
    fill(enrollment.capacity, { enrolled: 2, max: 1 }),
  );
  await expectNoAxeViolations(page);

  // 3. Withdraw the first student with a reason.
  await panel
    .getByRole('button', {
      name: fill(enrollment.withdrawOf, { name: first }),
      exact: true,
    })
    .click();
  const withdrawDialog = page.getByRole('dialog', {
    name: enrollment.withdrawDialog.title,
  });
  await withdrawDialog
    .getByLabel(enrollment.withdrawDialog.reason, { exact: true })
    .fill('Cambió de horario escolar.');
  await expectNoAxeViolations(page);
  await withdrawDialog
    .getByRole('button', {
      name: enrollment.withdrawDialog.submit,
      exact: true,
    })
    .click();
  await expect(withdrawDialog).toBeHidden();
  await expect(
    notice(fill(enrollment.withdrawDialog.done, { name: first })),
  ).toBeVisible();

  // 4. "Show withdrawn" lists it with the reason.
  await panel
    .getByRole('checkbox', { name: enrollment.showWithdrawn, exact: true })
    .click();
  await expect(page).toHaveURL(/\?withdrawn=1$/);
  const withdrawn = panel.getByRole('table', {
    name: fill(enrollment.withdrawn_one, { count: 1 }),
  });
  await expect(
    withdrawn.getByRole('row').filter({ hasText: first }),
  ).toContainText('Cambió de horario escolar.');
  await expectNoAxeViolations(page);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
