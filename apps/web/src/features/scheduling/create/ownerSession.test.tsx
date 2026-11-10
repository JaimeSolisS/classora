import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  alerts,
  click,
  comboboxOption,
  fieldByLabel,
  openDialog,
  setFieldValue,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  ANA,
  BRUNO,
  CLASSROOMS,
  COHORT_OPTION,
  COURSE_CREATED,
  GROUP_CONFLICTS,
  GROUP_CREATED,
  GROUP_OPTION,
  OVERRIDDEN,
  SOFT_CONFLICTS,
  SUBJECTS,
  TUTORS,
} from './fixtures';
import {
  EMPTY_SCHEDULE,
  isRoomTooSmall,
  ownerRefusal,
  ownerServerErrors,
  ownerSessionBody,
  validateOwnerSchedule,
} from './rules';
import { ApiError } from '@/app/api';

// HF-11b (#593): the "Class type" selector of "Schedule a session", the
// group and cohort pickers and summary, the body sent, the pre-fill kept
// across types, and the refusals and conflicts of a group or course
// session. The individual flow is covered, unchanged, by
// scheduling.test.tsx and makeup.test.tsx.

const CREATE = 'POST /api/v1/class-sessions';
const GROUPS = 'GET /api/v1/groups';
const COHORTS = 'GET /api/v1/cohorts';
const GROUP = `GET /api/v1/groups/${GROUP_OPTION.id}`;
const COHORT = `GET /api/v1/cohorts/${COHORT_OPTION.id}`;

const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'schedule.view',
    'schedule.manage',
    'schedule.override',
    'student.view',
    'course.view',
  ],
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

const LEVEL = {
  id: '00000000-0000-4000-8000-0000000004f1',
  name: 'Secundaria',
};

function rosterItem(student: { id: string; name: string }, withdrawn = false) {
  return {
    id: `${student.id.slice(0, -3)}9${student.id.slice(-2)}`,
    student: { id: student.id, display_name: student.name, is_active: true },
    enrolled_at: '2031-01-01T00:00:00Z',
    enrolled_by: { id: TEST_USER.id, display_name: 'Demo Owner' },
    withdrawn_at: withdrawn ? '2031-01-02T00:00:00Z' : null,
    withdrawn_by: null,
    withdrawal_reason: null,
    overridden: false,
    version: 1,
  };
}

const GROUP_SUBJECT = {
  id: SUBJECTS[0]!.id,
  name: SUBJECTS[0]!.name,
  level: LEVEL,
};
const COHORT_SUBJECT = {
  id: SUBJECTS[1]!.id,
  name: SUBJECTS[1]!.name,
  level: LEVEL,
};

const GROUP_ITEM = {
  id: GROUP_OPTION.id,
  name: GROUP_OPTION.name,
  subject: GROUP_SUBJECT,
  max_students: 10,
  enrolled_count: 2,
  tutor: null,
  next_starts_at: null,
};

const OTHER_GROUP_ITEM = {
  ...GROUP_ITEM,
  id: '00000000-0000-4000-8000-0000000006a2',
  name: 'Taller de lectura',
};

/** The group's detail; Bruno, then Ana (sorted by the form), and a withdrawal. */
function groupDetail(students = [BRUNO, ANA]) {
  return {
    ...GROUP_ITEM,
    effective_capacity: 10,
    current_series: null,
    next_occurrences: [],
    version: 1,
    created_at: '2031-01-01T00:00:00Z',
    updated_at: '2031-01-01T00:00:00Z',
    roster: [
      ...students.map((student) => rosterItem(student)),
      rosterItem(
        { id: '00000000-0000-4000-8000-0000000004a9', name: 'Ya Salió' },
        true,
      ),
    ],
  };
}

const COHORT_ITEM = {
  id: COHORT_OPTION.id,
  name: COHORT_OPTION.name,
  template: { id: '00000000-0000-4000-8000-0000000006c1', name: 'Olimpiada' },
  subject: COHORT_SUBJECT,
  max_students: 10,
  tutor: null,
  assignment_status: null,
  first_session_on: '2031-01-06',
  last_session_on: '2031-03-28',
  next_starts_at: null,
  status: 'in_progress',
  enrolled_count: 1,
};

const COHORT_DETAIL = {
  ...COHORT_ITEM,
  version: 1,
  created_at: '2031-01-01T00:00:00Z',
  updated_at: '2031-01-01T00:00:00Z',
  roster: [rosterItem(ANA)],
};

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    'GET /api/v1/subjects': page(
      SUBJECTS.map((s) => ({
        id: s.id,
        name: s.name,
        education_level_id: LEVEL.id,
        education_level_name: s.levelName,
        is_active: true,
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    'GET /api/v1/tutors': page(
      TUTORS.map((t) => ({
        user_id: t.id,
        username: t.id.slice(-4),
        display_name: t.name,
        state: 'active',
        email: null,
        phone: null,
      })),
    ),
    'GET /api/v1/classrooms': page(
      CLASSROOMS.map((c) => ({
        id: c.id,
        name: c.name,
        capacity: 1,
        is_active: true,
        availability: [],
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    'GET /api/v1/students': page([]),
    // More than one page: typing searches the server (`q`).
    [GROUPS]: (request) => {
      const q = new URL(request.url).searchParams.get('q');
      return jsonResponse(200, {
        items: q ? [GROUP_ITEM] : [GROUP_ITEM, OTHER_GROUP_ITEM],
        next_cursor: q ? null : 'more',
      });
    },
    [GROUP]: () => jsonResponse(200, groupDetail()),
    [COHORTS]: () =>
      jsonResponse(200, { items: [COHORT_ITEM], next_cursor: null }),
    [COHORT]: () => jsonResponse(200, COHORT_DETAIL),
    ...routes,
  });
}

function fieldError(
  status: number,
  errors: { path: (string | number)[]; code: string }[],
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code: 'validation_error',
      request_id: 'req-422',
      errors: errors.map((error) => ({ ...error, message: 'x' })),
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function conflictResponse(conflicts: unknown[]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-409',
      conflicts,
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function typeRadio(name: string): HTMLInputElement | undefined {
  return (
    [...main().querySelectorAll('label')]
      .find(
        (label) => label.textContent === name && label.querySelector('input'),
      )
      ?.querySelector<HTMLInputElement>('input[type="radio"]') ?? undefined
  );
}

async function chooseType(name: string) {
  await click(typeRadio(name)!);
}

function picker(): HTMLInputElement {
  return main().querySelector<HTMLInputElement>('[role="combobox"]')!;
}

/** Types into the group or cohort picker and picks the option `name`. */
async function pickOwner(query: string, name: string) {
  const field = picker();
  act(() => field.focus());
  typeInto(field, query);
  await settleUntil(() => comboboxOption(name));
  await click(comboboxOption(name)!);
}

function selectByLabel(label: string): HTMLSelectElement {
  return [...main().querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
}

function choose(label: string, value: string) {
  const select = selectByLabel(label);
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function fillWhen() {
  typeInto(fieldByLabel(main(), 'Date'), '2031-01-06');
  typeInto(fieldByLabel(main(), 'Start'), '10:00');
  typeInto(fieldByLabel(main(), 'End'), '11:00');
}

async function pickGroup() {
  await chooseType('Group');
  await pickOwner('Club', 'Club de Matemáticas · Álgebra');
  await settleUntil(() => main().textContent?.includes('Bruno'));
}

const hasLabel = (text: string) =>
  [...main().querySelectorAll('label')].some((l) => l.textContent === text);

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules (HF-11b)', () => {
  const values = {
    ...EMPTY_SCHEDULE,
    date: '2031-01-06',
    start: '10:00',
    end: '11:00',
  };

  it('needs the group or cohort, then the date and times', () => {
    expect(validateOwnerSchedule(EMPTY_SCHEDULE, 'group', null)).toEqual({
      owner: 'scheduling.create.owner.groupRequired',
      date: 'scheduling.validation.dateRequired',
      start: 'scheduling.validation.startRequired',
      end: 'scheduling.validation.endRequired',
    });
    expect(validateOwnerSchedule(values, 'course', null)).toEqual({
      owner: 'scheduling.create.owner.courseRequired',
    });
    // No student or subject is asked for.
    expect(validateOwnerSchedule(values, 'group', 'g')).toEqual({});
  });

  it('builds the group and course bodies without a student or subject', () => {
    expect(
      ownerSessionBody('group', 'g', values, 'America/Mexico_City'),
    ).toEqual({
      kind: 'group',
      group_id: 'g',
      tutor_user_id: null,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
    });
    expect(
      ownerSessionBody(
        'course',
        'c',
        { ...values, tutorId: 't', classroomId: 'r' },
        'America/Mexico_City',
        { codes: ['notice_window'], reason: 'r' },
      ),
    ).toEqual({
      override: { codes: ['notice_window'], reason: 'r' },
      kind: 'course',
      cohort_id: 'c',
      tutor_user_id: 't',
      classroom_id: 'r',
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
    });
  });

  it('puts the owner codes on the picker and the cohort dates on the date', () => {
    expect(
      ownerServerErrors(
        [
          { path: ['body', 'group_id'], code: 'student_inactive', message: '' },
          {
            path: ['body', 'tutor_user_id'],
            code: 'tutor_unknown',
            message: '',
          },
        ],
        'group',
      ),
    ).toEqual({
      owner: 'scheduling.create.owner.studentInactiveGroup',
      tutor: 'errors:tutor_unknown',
    });
    expect(
      ownerServerErrors(
        [
          { path: ['body', 'cohort_id'], code: 'cohort_unknown', message: '' },
          {
            path: ['body', 'starts_at'],
            code: 'session_outside_cohort_dates',
            message: '',
          },
        ],
        'course',
      ),
    ).toEqual({
      owner: 'errors:cohort_unknown',
      date: 'errors:session_outside_cohort_dates',
    });
    expect(
      ownerServerErrors(
        [
          {
            path: ['body', 'cohort_id'],
            code: 'subject_inactive',
            message: '',
          },
        ],
        'course',
      ),
    ).toEqual({ owner: 'scheduling.create.owner.subjectInactiveCourse' });
  });

  it('tells the ended and roster-changed refusals apart', () => {
    const error = (code: string) =>
      new ApiError({ code, status: 409, requestId: 'r' });
    expect(ownerRefusal(error('group_ended'))).toBe('ended');
    expect(ownerRefusal(error('cohort_ended'))).toBe('ended');
    expect(ownerRefusal(error('roster_changed'))).toBe('rosterChanged');
    expect(ownerRefusal(error('scheduling_conflict'))).toBeNull();
    expect(ownerRefusal(new Error('x'))).toBeNull();
  });

  it('warns only when the roster is larger than the room', () => {
    expect(isRoomTooSmall(undefined, 30)).toBe(false);
    expect(isRoomTooSmall(4, 4)).toBe(false);
    expect(isRoomTooSmall(4, 5)).toBe(true);
  });
});

describe('the type selector (AC1)', () => {
  it('starts on Individual, with the individual fields', async () => {
    stub();
    app = await renderApp('/schedule/new');

    const legend = main().querySelector('fieldset legend');
    expect(legend?.textContent).toBe('Class type');
    expect(typeRadio('Individual')?.checked).toBe(true);
    expect(typeRadio('Group')?.checked).toBe(false);
    expect(typeRadio('Course')?.checked).toBe(false);
    expect(hasLabel('Student')).toBe(true);
    expect(hasLabel('Subject')).toBe(true);
    expect(main().querySelector('[role="switch"]')).not.toBeNull();
  });

  it('offers Course only with course.view, and nothing without either list', async () => {
    stub({}, { ...OWNER, permissions: ['schedule.view', 'schedule.manage'] });
    app = await renderApp('/schedule/new');
    expect(typeRadio('Group')).toBeDefined();
    expect(typeRadio('Course')).toBeUndefined();
    app.unmount();

    stub({}, { ...OWNER, permissions: ['schedule.manage'] });
    app = await renderApp('/schedule/new');
    expect(typeRadio('Individual')).toBeUndefined();
    expect(main().textContent).not.toContain('Class type');
  });
});

describe('a group session (AC2)', () => {
  it('hides the individual fields and searches the groups through q', async () => {
    const api = stub();
    app = await renderApp('/schedule/new');

    await chooseType('Group');

    expect(hasLabel('Student')).toBe(false);
    expect(hasLabel('Subject')).toBe(false);
    expect(main().querySelector('[role="switch"]')).toBeNull();
    expect(hasLabel('Group')).toBe(true);

    const groupUrls = () =>
      api.fetch.mock.calls
        .map(([request]) => new URL(request.url))
        .filter((url) => url.pathname === '/api/v1/groups');
    const field = picker();
    act(() => field.focus());
    typeInto(field, 'Club');
    // More groups than one page: the text goes to the server.
    await settleUntil(() =>
      groupUrls().some((url) => url.searchParams.get('q') === 'Club'),
    );
    expect(
      groupUrls().every((url) => url.searchParams.get('status') === 'active'),
    ).toBe(true);
    await settleUntil(() => comboboxOption('Club de Matemáticas · Álgebra'));
    await click(comboboxOption('Club de Matemáticas · Álgebra')!);

    await settleUntil(() => main().textContent?.includes('Bruno'));
    const summary = main().querySelector('section')!;
    expect(summary.querySelector('h2')?.textContent).toBe(
      'Club de Matemáticas',
    );
    expect(summary.textContent).toContain('Álgebra (Secundaria)');
    expect(summary.textContent).toContain('Enrolled students (2)');
    // By name; the withdrawn student is not listed.
    expect(
      [...summary.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual(['Ana López', 'Bruno Díaz Hernández de la Fuente']);
    expect(summary.textContent).not.toContain('Ya Salió');
    expect(main().textContent).toContain(
      'Club de Matemáticas: Álgebra, 2 students enrolled.',
    );
  });

  it('posts the group body and shows the group success panel', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, GROUP_CREATED) });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      kind: 'group',
      group_id: GROUP_OPTION.id,
      tutor_user_id: null,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
    });
    expect(main().querySelector('h2')?.textContent).toBe(
      'Group session scheduled',
    );
    expect(main().textContent).toContain('Club de Matemáticas');
    expect(main().textContent).toContain('2 students');
    expect(main().textContent).toContain('Unassigned');
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Open the group',
    );
    expect(link?.getAttribute('href')).toBe(`/groups/${GROUP_OPTION.id}`);
    expect(
      [...main().querySelectorAll('a')].some(
        (a) => a.textContent === 'Open the session',
      ),
    ).toBe(true);
  });

  it('asks for the group before sending anything', async () => {
    const api = stub();
    app = await renderApp('/schedule/new');
    await chooseType('Group');
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(main().textContent).toContain('Choose a group.');
    expect(document.activeElement).toBe(picker());
  });

  it('shows the room capacities and warns when the roster is larger', async () => {
    stub();
    app = await renderApp('/schedule/new');
    await pickGroup();
    await settleUntil(() =>
      [...selectByLabel('Classroom').options].some((o) =>
        o.textContent?.includes('capacity'),
      ),
    );

    choose('Classroom', CLASSROOMS[0]!.id);

    expect(
      [...selectByLabel('Classroom').options].map((o) => o.textContent),
    ).toContain('Aula 1 (capacity 1)');
    expect(main().textContent).toContain(
      "This classroom holds 1 and 2 students are enrolled: it can't take this class.",
    );
  });
});

describe('a course session (AC3)', () => {
  it('shows the dates and posts the course body', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, COURSE_CREATED) });
    app = await renderApp('/schedule/new');
    await chooseType('Course');
    await pickOwner('Olim', 'Olimpiada de física 2031 · Física');
    await settleUntil(() => main().textContent?.includes('Ana López'));

    expect(main().textContent).toContain('Jan 6, 2031 to Mar 28, 2031');
    expect(fieldByLabel(main(), 'Date').getAttribute('aria-describedby')).toBe(
      main().querySelector('p[id$="-date-help"]')?.getAttribute('id'),
    );
    expect(main().textContent).toContain(
      'The session must fall between Jan 6, 2031 and Mar 28, 2031.',
    );
    const urls = api.fetch.mock.calls
      .map(([request]) => new URL(request.url))
      .filter((url) => url.pathname === '/api/v1/cohorts');
    expect(urls[0]?.searchParams.get('status')).toBe('current');

    fillWhen();
    choose('Tutor', TUTORS[0]!.id);
    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      kind: 'course',
      cohort_id: COHORT_OPTION.id,
      tutor_user_id: TUTORS[0]!.id,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
    });
    expect(main().querySelector('h2')?.textContent).toBe(
      'Course session scheduled',
    );
    expect(main().textContent).toContain('Offered to Carmen Ruiz');
    expect(
      [...main().querySelectorAll('a')]
        .find((a) => a.textContent === 'Open the course')
        ?.getAttribute('href'),
    ).toBe(`/courses/cohorts/${COHORT_OPTION.id}`);
  });

  it('puts session_outside_cohort_dates on the date', async () => {
    stub({
      [CREATE]: () =>
        fieldError(422, [
          { path: ['body', 'starts_at'], code: 'session_outside_cohort_dates' },
        ]),
    });
    app = await renderApp('/schedule/new');
    await chooseType('Course');
    await pickOwner('Olim', 'Olimpiada de física 2031 · Física');
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    const date = fieldByLabel(main(), 'Date');
    expect(date.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(date);
    expect(main().textContent).toContain(
      "The date must fall between the cohort's first and last sessions.",
    );
    expect(fieldByLabel(main(), 'Start').getAttribute('aria-invalid')).toBe(
      null,
    );
  });
});

describe('the pre-fill across types (AC4)', () => {
  const TUTOR = TUTORS[1]!.id;
  const ROOM = CLASSROOMS[0]!.id;

  it('keeps the date, times, tutor and room when switching to Group and back', async () => {
    stub();
    app = await renderApp(
      `/schedule/new?date=2031-03-10&start=10:00&end=11:30&tutor=${TUTOR}&room=${ROOM}`,
    );

    const check = () => {
      expect(fieldByLabel(main(), 'Date').value).toBe('2031-03-10');
      expect(fieldByLabel(main(), 'Start').value).toBe('10:00 AM');
      expect(fieldByLabel(main(), 'End').value).toBe('11:30 AM');
      expect(selectByLabel('Tutor').value).toBe(TUTOR);
      expect(selectByLabel('Classroom').value).toBe(ROOM);
    };
    await chooseType('Group');
    check();
    await pickOwner('Club', 'Club de Matemáticas · Álgebra');
    await chooseType('Course');
    check();
    // The other type's selection is cleared.
    expect(picker().value).toBe('');
    await chooseType('Individual');
    check();
    expect(hasLabel('Student')).toBe(true);
    expect(selectByLabel('Subject').value).toBe('');
  });
});

describe('refusals and conflicts (AC5)', () => {
  it('names the student of a student_overlap', async () => {
    const api = stub({ [CREATE]: () => conflictResponse(GROUP_CONFLICTS) });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    const panel = main().querySelector('[role="alert"]')!;
    expect(
      [...panel.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual([
      'Not allowedBruno Díaz Hernández de la Fuente already has a class at this time.',
      'Can be overriddenThe tutor is not qualified for this subject.',
    ]);
    // No suggested times for a group.
    expect(api.calls('POST /api/v1/class-sessions/suggestions')).toHaveLength(
      0,
    );
  });

  it('overrides a soft conflict and resends the group body with it', async () => {
    const api = stub({
      [CREATE]: [
        () => conflictResponse(SOFT_CONFLICTS),
        () => jsonResponse(201, { ...OVERRIDDEN, kind: 'group' }),
      ],
    });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));

    await click(buttonNamed(main(), 'Override and schedule'));
    const dialog = openDialog()!;
    setFieldValue(dialog.querySelector('textarea')!, 'Clase de repaso');
    await click(buttonNamed(openDialog()!, 'Override and schedule'));

    const [first, second] = api.calls(CREATE);
    expect(second!.body).toEqual({
      ...(first!.body as object),
      override: {
        codes: ['outside_operating_hours', 'tutor_not_qualified'],
        reason: 'Clase de repaso',
      },
    });
    expect((second!.body as { kind: string }).kind).toBe('group');
    expect(main().textContent).toContain('Group session scheduled');
    expect(main().textContent).toContain('Scheduled with an override');
  });

  it('shows group_ended as an alert and clears the choice', async () => {
    stub({ [CREATE]: () => problem(409, 'group_ended') });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain(
      'This group has ended and has no more sessions. Choose another group.',
    );
    expect(picker().value).toBe('');
    expect(main().textContent).not.toContain('Enrolled students');
  });

  it('shows cohort_ended as an alert and clears the choice', async () => {
    stub({ [CREATE]: () => problem(409, 'cohort_ended') });
    app = await renderApp('/schedule/new');
    await chooseType('Course');
    await pickOwner('Olim', 'Olimpiada de física 2031 · Física');
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain(
      'This course has ended. Choose another course.',
    );
    expect(picker().value).toBe('');
  });

  it('reads the roster again after roster_changed', async () => {
    const api = stub({
      [CREATE]: () => problem(409, 'roster_changed'),
      [GROUP]: [
        () => jsonResponse(200, groupDetail()),
        () => jsonResponse(200, groupDetail([BRUNO])),
      ],
    });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));
    await settleUntil(() => !main().textContent?.includes('Ana López'));

    expect(api.calls(GROUP)).toHaveLength(2);
    expect(alerts(main()).join(' ')).toContain(
      'The enrollment changed while you scheduled. Check the students and try again.',
    );
    expect(main().textContent).toContain('Enrolled student (1)');
    expect(picker().value).toBe('Club de Matemáticas');
  });

  it('puts an inactive student on the picker', async () => {
    stub({
      [CREATE]: () =>
        fieldError(422, [
          { path: ['body', 'group_id'], code: 'student_inactive' },
        ]),
    });
    app = await renderApp('/schedule/new');
    await pickGroup();
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(picker().getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(
      'A student enrolled in this group is inactive. Withdraw them first.',
    );
  });

  it('shows the empty-roster notice and still submits', async () => {
    const api = stub({
      [GROUP]: () => jsonResponse(200, groupDetail([])),
      [CREATE]: () => jsonResponse(201, { ...GROUP_CREATED, participants: [] }),
    });
    app = await renderApp('/schedule/new');
    await chooseType('Group');
    await pickOwner('Club', 'Club de Matemáticas · Álgebra');
    await settleUntil(() => main().textContent?.includes('No students'));

    expect(main().textContent).toContain(
      'No students are enrolled yet. Students who enroll later join this class.',
    );
    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));
    await settle();

    expect(api.calls(CREATE)).toHaveLength(1);
    expect(main().textContent).toContain('Group session scheduled');
    expect(main().textContent).toContain('0 students');
  });
});

describe('HF-13 errors follow the values (#598 AC1)', () => {
  it.each([
    ['Group', 'Choose a group.', 'Club', 'Club de Matemáticas · Álgebra'],
    ['Course', 'Choose a course.', 'Olim', 'Olimpiada de física 2031 · Física'],
  ])(
    'clears the time errors of a %s session once the times are typed',
    async (type, ownerRequired, query, option) => {
      const api = stub();
      app = await renderApp('/schedule/new');
      await chooseType(type);

      await click(buttonNamed(main(), 'Schedule session'));
      expect(main().textContent).toContain('Enter the start time.');
      expect(main().textContent).toContain(ownerRequired);

      fillWhen();
      expect(main().textContent).not.toContain('Enter the start time.');
      expect(main().textContent).not.toContain('Enter the end time.');
      expect(main().textContent).not.toContain('Enter the date.');
      expect(
        fieldByLabel(main(), 'Start').getAttribute('aria-invalid'),
      ).toBeNull();
      // The group or course is still missing.
      expect(main().textContent).toContain(ownerRequired);

      await pickOwner(query, option);
      expect(main().textContent).not.toContain(ownerRequired);
      expect(api.calls(CREATE)).toHaveLength(0);
    },
  );
});
