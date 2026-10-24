import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
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
