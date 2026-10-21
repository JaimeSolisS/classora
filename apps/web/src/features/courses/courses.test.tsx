import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { CourseTemplate } from './api';
import {
  ALGEBRA,
  CHEMISTRY_ID,
  MATH_ID,
  pageOf,
  PHYSICS_ID,
  SUBJECT_OPTIONS,
  TEMPLATES,
} from './fixtures';
import {
  emptyValues,
  moveRow,
  newAssessmentRow,
  newModuleRow,
  removeModule,
  serverErrors,
  templateBody,
  templateValues,
  validateTemplate,
  type TemplateFormValues,
} from './rules';

// #64 G1-G7: the Courses item and routes by permission, the list with its
// status filter, the editor (POST and PUT bodies with positions from the
// order and module_index after a reorder or a removal, move up / down with
// focus and an announcement, add / remove focus, the C1 checks as row
// errors), the read-only view for Finance Staff and the server errors.

const LIST = 'GET /api/v1/course-templates';
const CREATE = 'POST /api/v1/course-templates';
const DETAIL = `GET /api/v1/course-templates/${ALGEBRA.id}`;
const PUT = `PUT /api/v1/course-templates/${ALGEBRA.id}`;
const PATCH = `PATCH /api/v1/course-templates/${ALGEBRA.id}`;
const SUBJECTS = 'GET /api/v1/subjects';

const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['course.view', 'course.manage'],
};
const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['course.view'],
};

const subjectPage: Responder = () =>
  jsonResponse(200, {
    items: SUBJECT_OPTIONS.map((option) => ({
      id: option.id,
      name: option.name,
      education_level_id: '01900000-0000-7000-8000-00000000e001',
      education_level_name: option.levelName,
      is_active: true,
      version: 1,
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    })),
    total: SUBJECT_OPTIONS.length,
    limit: 100,
    offset: 0,
  });

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => jsonResponse(200, pageOf(TEMPLATES)),
    [DETAIL]: () => jsonResponse(200, ALGEBRA),
    [SUBJECTS]: subjectPage,
    ...routes,
  });
}

function validation(errors: { path: (string | number)[]; code: string }[]) {
  return () =>
    new Response(
      JSON.stringify({
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'validation_error',
        request_id: 'req-422',
        errors: errors.map((error) => ({ ...error, message: 'Server text' })),
      }),
      {
        status: 422,
        headers: { 'Content-Type': 'application/problem+json' },
      },
    );
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const named = <T extends HTMLElement>(name: string) => {
  const found = main().querySelector<T>(`[aria-label="${name}"]`);
  if (!found) {
    throw new Error(`No control "${name}"`);
  }
  return found;
};
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('');
const rows = () =>
  [...main().querySelectorAll('tbody tr')].map((row) =>
    [...row.querySelectorAll('td')].map((cell) => cell.textContent),
  );
const errorOf = (element: HTMLElement) =>
  document.getElementById(
    (element.getAttribute('aria-describedby') ?? '').split(' ').at(-1) ?? '',
  )?.textContent;

function selectByLabel(text: string): HTMLSelectElement {
  const label = [...main().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  return document.getElementById(label!.htmlFor) as HTMLSelectElement;
}

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function typeText(area: HTMLTextAreaElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function checkSubject(name: string) {
  const label = [...main().querySelectorAll('label')].find((candidate) =>
    candidate.textContent?.startsWith(name),
  );
  await click(document.getElementById(label!.htmlFor)!);
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// --- G1: navigation and routes -------------------------------------------------

describe('the Courses item and routes (G1)', () => {
  it('with course.view: the item, the list and "New template" for managers', async () => {
    const api = stub();

    app = await renderApp('/courses');

    expect(navItems()).toContain('Courses');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Course templates');
    expect(document.title).toBe('Course templates · Classora');
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'New template',
    );
    expect(link?.getAttribute('href')).toBe('/courses/templates/new');
    const [request] = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/course-templates');
    expect(request?.searchParams.get('status')).toBe('active');
  });

  it('Finance Staff see the list without "New template"', async () => {
    stub(FINANCE);

    app = await renderApp('/courses');

    expect(navItems()).toContain('Courses');
    expect(
      [...main().querySelectorAll('a')].some(
        (a) => a.textContent === 'New template',
      ),
    ).toBe(false);
  });

  it('without course.view: no item, the forbidden page and no request', async () => {
    const api = stub({ ...TEST_USER, permissions: ['student.view'] });

    app = await renderApp('/courses');

    expect(navItems()).not.toContain('Courses');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('the new-template route needs course.manage', async () => {
    const api = stub(FINANCE);

    app = await renderApp('/courses/templates/new');

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(SUBJECTS)).toHaveLength(0);
  });
});

// --- G2: the list ---------------------------------------------------------------

describe('the list (G2)', () => {
  it('shows name, subjects, duration, module count and a text status', async () => {
    stub();

    app = await renderApp('/courses');

    expect(rows()).toEqual([
      ['Álgebra intensiva', 'Matemáticas', '10 × 90 min (15 h)', '3', 'Active'],
      [
        'Laboratorio de física',
        'Física, Química',
        '8 × 45 min (6 h)',
        '2',
        'Inactive',
      ],
    ]);
    expect(main().querySelector('tbody a')?.getAttribute('href')).toBe(
      `/courses/templates/${ALGEBRA.id}`,
    );
  });

  it('the status filter goes to the URL and the query', async () => {
    const api = stub();
    app = await renderApp('/courses');

    choose(selectByLabel('Status'), 'inactive');
    await settle();

    const statuses = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/course-templates')
      .map((url) => url.searchParams.get('status'));
    expect(statuses).toEqual(['active', 'inactive']);
  });

  it('shows the empty and the error state', async () => {
    stub(MANAGER, { [LIST]: () => jsonResponse(200, pageOf([])) });
    app = await renderApp('/courses');
    expect(main().textContent).toContain(
      'There are no course templates with this status.',
    );
    app.unmount();

    stub(MANAGER, { [LIST]: () => problem(403, 'permission_denied') });
    app = await renderApp('/courses');
    expect(alerts(main())).toHaveLength(1);
  });
});

// --- Rules (G6) ---------------------------------------------------------------------

function values(change: Partial<TemplateFormValues> = {}): TemplateFormValues {
  return { ...templateValues(ALGEBRA), ...change };
}

describe('the template rules (G6)', () => {
  it('a stored template gives its own PUT body back', () => {
    expect(templateBody(values())).toEqual({
      name: 'Álgebra intensiva',
      description: 'Curso de verano de álgebra.',
      subject_ids: [MATH_ID],
      session_count: 10,
      session_minutes: 90,
      min_attendance_percent: 80,
      modules: ALGEBRA.modules.map((module) => ({
        id: module.id,
        title: module.title,
        description: module.description,
        subject_id: module.subject_id,
        planned_sessions: module.planned_sessions,
        is_required: module.is_required,
        materials: module.materials,
      })),
      assessments: [
        {
          id: ALGEBRA.assessments[0]!.id,
          title: 'Examen de funciones',
          module_index: 1,
          is_required: true,
          min_score_percent: 60,
        },
      ],
    });
    expect(validateTemplate(values())).toEqual({
      fields: {},
      modules: {},
      assessments: {},
    });
  });

  it('module_index follows a reorder and a removal', () => {
    const start = values();
    const reordered = { ...start, modules: moveRow(start.modules, 1, -1) };
    expect(templateBody(reordered).assessments![0]!.module_index).toBe(0);

    const removedFirst = removeModule(start, 0);
    expect(templateBody(removedFirst).assessments![0]!.module_index).toBe(0);
    const removedTarget = removeModule(start, 1);
    expect(
      templateBody(removedTarget).assessments![0]!.module_index,
    ).toBeNull();
  });

  it('new rows send no id and blank texts are null', () => {
    const row = { ...newModuleRow(), title: '  Nuevo ', materials: '  ' };
    const body = templateBody({
      ...emptyValues(),
      name: ' Curso ',
      subjectIds: [MATH_ID],
      sessionCount: '4',
      sessionMinutes: '60',
      modules: [row],
      assessments: [{ ...newAssessmentRow(), title: 'Final' }],
    });

    expect(body).toEqual({
      name: 'Curso',
      description: null,
      subject_ids: [MATH_ID],
      session_count: 4,
      session_minutes: 60,
      min_attendance_percent: null,
      modules: [
        {
          title: 'Nuevo',
          description: null,
          subject_id: null,
          planned_sessions: null,
          is_required: true,
          materials: null,
        },
      ],
      assessments: [
        {
          title: 'Final',
          module_index: null,
          is_required: true,
          min_score_percent: null,
        },
      ],
    });
  });

  it.each([
    [{ name: '  ' }, 'fields', 'name', 'nameRequired'],
    [{ subjectIds: [] }, 'fields', 'subjects', 'subjectsRequired'],
    [
      { subjectIds: [MATH_ID, PHYSICS_ID, CHEMISTRY_ID, ...'abcdefgh'] },
      'fields',
      'subjects',
      'tooManySubjects',
    ],
    [{ sessionCount: '0' }, 'fields', 'sessionCount', 'sessionCountRange'],
    [{ sessionCount: '201' }, 'fields', 'sessionCount', 'sessionCountRange'],
    [
      { sessionMinutes: '14' },
      'fields',
      'sessionMinutes',
      'sessionMinutesRange',
    ],
    [
      { sessionMinutes: '481' },
      'fields',
      'sessionMinutes',
      'sessionMinutesRange',
    ],
    [{ minAttendance: '101' }, 'fields', 'minAttendance', 'percentRange'],
    [{ modules: [] }, 'fields', 'modules', 'modulesRequired'],
  ] as const)('%o is an error on %s.%s', (change, group, field, key) => {
    const errors = validateTemplate(
      values(change as Partial<TemplateFormValues>),
    );
    expect((errors[group] as Record<string, string>)[field]).toBe(
      `courses.templates.validation.${key}`,
    );
  });

  it('row checks: duplicate titles, the module subject, planned sessions, scores', () => {
    const start = values();
    const errors = validateTemplate({
      ...start,
      subjectIds: [PHYSICS_ID],
      sessionCount: '10',
      modules: [
        { ...start.modules[0]!, plannedSessions: '6' },
        { ...start.modules[1]!, title: 'ecuaciones', plannedSessions: '5' },
        start.modules[2]!,
      ],
      assessments: [
        { ...start.assessments[0]!, minScore: '0' },
        { ...newAssessmentRow(), title: 'EXAMEN DE FUNCIONES' },
      ],
    });

    expect(errors.modules).toEqual({
      0: { subjectId: 'courses.templates.validation.moduleSubject' },
      1: {
        title: 'courses.templates.validation.duplicateTitle',
        plannedSessions: 'courses.templates.validation.plannedExceedTotal',
      },
    });
    expect(errors.assessments).toEqual({
      0: { minScore: 'courses.templates.validation.percentRange' },
      1: { title: 'courses.templates.validation.duplicateTitle' },
    });
  });

  it('maps the server field errors to rows and fields', () => {
    const errors = serverErrors(
      [
        { path: ['body', 'subject_ids', 0], code: 'subject_inactive' },
        { path: ['body', 'modules', 1, 'title'], code: 'duplicate_title' },
        { path: ['body', 'modules'], code: 'planned_sessions_exceed_total' },
        {
          path: ['body', 'assessments', 0, 'module_index'],
          code: 'module_index_invalid',
        },
      ],
      values(),
    );

    expect(errors).toEqual({
      fields: { subjects: 'courses.templates.validation.subjectInactive' },
      modules: {
        1: {
          title: 'courses.templates.validation.duplicateTitle',
          plannedSessions: 'courses.templates.validation.plannedExceedTotal',
        },
      },
      assessments: {
        0: { moduleKey: 'courses.templates.validation.moduleIndexInvalid' },
      },
    });
  });
});

// --- G3, G4, G7: the editor --------------------------------------------------------

describe('the new template editor (G3)', () => {
  it('sends the whole document in the order shown and opens the template', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, ALGEBRA),
    });
    app = await renderApp('/courses/templates/new');
    expect(main().querySelector('h1')?.textContent).toBe('New course template');
    expect(main().textContent).toContain(
      'Cohorts already created keep the syllabus they were created with',
    );

    typeInto(fieldByLabel(main(), 'Name'), 'Curso E2E');
    await checkSubject('Matemáticas');
    typeInto(fieldByLabel(main(), 'Number of sessions'), '10');
    typeInto(fieldByLabel(main(), 'Session length (minutes)'), '90');
    expect(main().textContent).toContain('15 h in 10 sessions');
    typeInto(named('Module 1 title'), 'Uno');
    await click(buttonNamed(main(), 'Add module'));
    expect(document.activeElement).toBe(named('Module 2 title'));
    typeInto(named('Module 2 title'), 'Dos');
    await click(buttonNamed(main(), 'Add module'));
    typeInto(named('Module 3 title'), 'Tres');
    choose(named<HTMLSelectElement>('Module 1 subject'), MATH_ID);
    typeText(
      named<HTMLTextAreaElement>('Module 1 materials references'),
      'Libro',
    );
    await click(buttonNamed(main(), 'Add assessment'));
    expect(document.activeElement).toBe(named('Assessment 1 title'));
    typeInto(named('Assessment 1 title'), 'Parcial');
    choose(
      named<HTMLSelectElement>('Assessment 1 module'),
      // The option of module 2 ("Dos").
      [...named<HTMLSelectElement>('Assessment 1 module').options].find(
        (option) => option.textContent === 'Dos',
      )!.value,
    );
    typeInto(fieldByLabel(main(), 'Minimum attendance (%)'), '80');
    // Module 3 moves up: module_index of the assessment becomes 2.
    await click(named('Move up module 3'));
    expect(document.activeElement).toBe(named('Move up module 2'));
    expect(main().textContent).toContain('Module moved to position 2.');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      {
        name: 'Curso E2E',
        description: null,
        subject_ids: [MATH_ID],
        session_count: 10,
        session_minutes: 90,
        min_attendance_percent: 80,
        modules: [
          {
            title: 'Uno',
            description: null,
            subject_id: MATH_ID,
            planned_sessions: null,
            is_required: true,
            materials: 'Libro',
          },
          {
            title: 'Tres',
            description: null,
            subject_id: null,
            planned_sessions: null,
            is_required: true,
            materials: null,
          },
          {
            title: 'Dos',
            description: null,
            subject_id: null,
            planned_sessions: null,
            is_required: true,
            materials: null,
          },
        ],
        assessments: [
          {
            title: 'Parcial',
            module_index: 2,
            is_required: true,
            min_score_percent: null,
          },
        ],
      },
    ]);
    expect(main().querySelector('h1')?.textContent).toBe('Álgebra intensiva');
    expect(statusText()).toContain('Saved.');
  });

  it('checks before sending, focuses the first error and keeps row errors', async () => {
    const api = stub();
    app = await renderApp('/courses/templates/new');

    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    const name = fieldByLabel(main(), 'Name');
    expect(document.activeElement).toBe(name);
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(errorOf(name)).toBe('Enter a name.');
    expect(main().textContent).toContain('Choose at least one subject.');
    const title = named<HTMLInputElement>('Module 1 title');
    expect(title.getAttribute('aria-invalid')).toBe('true');
    expect(errorOf(title)).toBe('Enter a title.');

    // Fixed while typing once checked.
    typeInto(title, 'Uno');
    expect(title.getAttribute('aria-invalid')).toBeNull();
  });

  it('planned sessions above the total are a row error', async () => {
    const api = stub();
    app = await renderApp('/courses/templates/new');
    typeInto(fieldByLabel(main(), 'Name'), 'Curso');
    await checkSubject('Física');
    typeInto(fieldByLabel(main(), 'Number of sessions'), '10');
    typeInto(fieldByLabel(main(), 'Session length (minutes)'), '60');
    typeInto(named('Module 1 title'), 'Uno');
    typeInto(named('Module 1 planned sessions'), '6');
    await click(buttonNamed(main(), 'Add module'));
    typeInto(named('Module 2 title'), 'Dos');
    typeInto(named('Module 2 planned sessions'), '5');

    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    const planned = named('Module 2 planned sessions');
    expect(document.activeElement).toBe(planned);
    expect(errorOf(planned)).toBe(
      'The planned sessions of the modules add up to more than the number of sessions.',
    );
  });

  it('Remove focuses "Add module"; the last module cannot be removed', async () => {
    stub();
    app = await renderApp('/courses/templates/new');
    expect(named<HTMLButtonElement>('Remove module 1').disabled).toBe(true);
    await click(buttonNamed(main(), 'Add module'));

    await click(named('Remove module 1'));

    expect(main().querySelector('[aria-label="Module 2 title"]')).toBeNull();
    expect(document.activeElement).toBe(buttonNamed(main(), 'Add module'));
  });

  it('name_taken shows on the name', async () => {
    stub(MANAGER, { [CREATE]: () => problem(409, 'name_taken') });
    app = await renderApp('/courses/templates/new');
    typeInto(fieldByLabel(main(), 'Name'), 'Curso');
    await checkSubject('Física');
    typeInto(fieldByLabel(main(), 'Number of sessions'), '10');
    typeInto(fieldByLabel(main(), 'Session length (minutes)'), '60');
    typeInto(named('Module 1 title'), 'Uno');

    await click(buttonNamed(main(), 'Save'));

    const name = fieldByLabel(main(), 'Name');
    expect(document.activeElement).toBe(name);
    expect(errorOf(name)).toBe(
      'Another course template already has this name, possibly an inactive one.',
    );
  });
});

describe('editing a stored template (G3)', () => {
  it('sends the ids, the version and module_index after a reorder and a removal', async () => {
    const saved: CourseTemplate = { ...ALGEBRA, version: 2 };
    const api = stub(MANAGER, { [PUT]: () => jsonResponse(200, saved) });
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);
    expect(named<HTMLInputElement>('Module 2 title').value).toBe('Funciones');

    // Repaso (3) moves up above Funciones; then Ecuaciones (1) is removed.
    await click(named('Move up module 3'));
    await click(named('Remove module 1'));
    typeInto(named('Module 1 title'), 'Repaso general');
    await click(buttonNamed(main(), 'Save'));

    const [call] = api.calls(PUT);
    const body = call!.body as Record<string, unknown> & {
      modules: { id?: string; title: string }[];
      assessments: { id?: string; module_index: number | null }[];
    };
    expect(body.version).toBe(1);
    expect(body.modules.map((m) => [m.id, m.title])).toEqual([
      [ALGEBRA.modules[2]!.id, 'Repaso general'],
      [ALGEBRA.modules[1]!.id, 'Funciones'],
    ]);
    expect(body.assessments).toEqual([
      {
        id: ALGEBRA.assessments[0]!.id,
        title: 'Examen de funciones',
        module_index: 1,
        is_required: true,
        min_score_percent: 60,
      },
    ]);
    expect(statusText()).toContain('Saved.');
  });

  it('removing the module of an assessment makes it cover the whole course', async () => {
    const api = stub(MANAGER, {
      [PUT]: () => jsonResponse(200, { ...ALGEBRA, version: 2 }),
    });
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    await click(named('Remove module 2'));
    expect(named<HTMLSelectElement>('Assessment 1 module').value).toBe('');
    await click(buttonNamed(main(), 'Save'));

    const body = api.calls(PUT)[0]!.body as {
      assessments: { module_index: number | null }[];
    };
    expect(body.assessments[0]!.module_index).toBeNull();
  });

  it('Move down at the bottom keeps focus on the row', async () => {
    stub();
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);
    expect(named<HTMLButtonElement>('Move up module 1').disabled).toBe(true);
    expect(named<HTMLButtonElement>('Move down module 3').disabled).toBe(true);

    await click(named('Move down module 2'));

    expect(named<HTMLInputElement>('Module 3 title').value).toBe('Funciones');
    expect(document.activeElement).toBe(named('Move up module 3'));
    expect(main().textContent).toContain('Module moved to position 3.');
  });

  it('a version conflict offers Reload, which refetches', async () => {
    const api = stub(MANAGER, {
      [PUT]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    await click(buttonNamed(main(), 'Save'));
    expect(alerts(main()).join('')).toContain('changed');
    await click(buttonNamed(main(), 'Reload'));

    expect(api.calls(DETAIL).length).toBeGreaterThanOrEqual(2);
  });

  it('server field errors land on their rows', async () => {
    stub(MANAGER, {
      [PUT]: validation([
        {
          path: ['body', 'assessments', 0, 'module_index'],
          code: 'module_index_invalid',
        },
      ]),
    });
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    await click(buttonNamed(main(), 'Save'));

    const select = named<HTMLSelectElement>('Assessment 1 module');
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(select);
    expect(errorOf(select)).toBe('Choose a module of this template.');
  });

  it('Deactivate sends the version and shows the new status', async () => {
    const api = stub(MANAGER, {
      [PATCH]: () =>
        jsonResponse(200, { ...ALGEBRA, is_active: false, version: 2 }),
    });
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    await click(buttonNamed(main(), 'Deactivate'));

    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { version: 1, is_active: false },
    ]);
    expect(buttonNamed(main(), 'Reactivate')).toBeTruthy();
    expect(main().textContent).toContain('Inactive');
  });
});

// --- G5: read-only ----------------------------------------------------------------

describe('the read-only view (G5)', () => {
  it('Finance Staff read the template without any form', async () => {
    const api = stub(FINANCE);

    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    expect(main().querySelector('h1')?.textContent).toBe('Álgebra intensiva');
    expect(main().querySelector('form')).toBeNull();
    expect(main().querySelectorAll('button')).toHaveLength(0);
    expect(main().textContent).toContain('Module 2: Funciones');
    expect(main().textContent).toContain('Examen de funciones');
    expect(main().textContent).toContain('80 %');
    expect(api.calls(SUBJECTS)).toHaveLength(0);
  });

  it('an unknown template says it does not exist', async () => {
    stub(FINANCE, { [DETAIL]: () => problem(404, 'not_found') });

    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This course template doesn't exist.",
    );
  });
});

describe('Spanish strings (G7)', () => {
  it('labels rows in Spanish', async () => {
    applyLanguage('es');
    stub();
    app = await renderApp(`/courses/templates/${ALGEBRA.id}`);

    expect(named<HTMLInputElement>('Título del módulo 1').value).toBe(
      'Ecuaciones',
    );
    expect(buttonNamed(main(), 'Agregar módulo')).toBeTruthy();
    expect(main().textContent).toContain('Criterios de finalización');
  });
});
