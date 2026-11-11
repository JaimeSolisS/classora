import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import {
  CANCELLED_SESSION,
  CONFIRMED_SESSION,
  MAKEUP_AVAILABLE,
  MAKEUP_ENDED,
  MAKEUP_NOT_ALLOWED,
  MAKEUP_SCHEDULED,
  MAKEUP_SESSION,
} from '../session/fixtures';
import type { ClassSessionDetail } from '../session/api';
import { makeupStatus } from '../session/rules';
import {
  ANA,
  CLASSROOMS,
  CREATED,
  NO_SUGGESTIONS,
  SOFT_CONFLICTS,
  GENERAL_SUBJECT,
  SUBJECTS,
  TUTORS,
} from './fixtures';
import {
  EMPTY_SCHEDULE,
  makeupTargetProblem,
  makeupValues,
  scheduleBody,
  splitServerErrors,
  suggestionBody,
  takenMakeupId,
  type ScheduleValues,
} from './rules';

// #436: the makeup block of the session page (A) and makeup mode on
// /schedule/new (B), with the refusals' messages (C).

const ID = CANCELLED_SESSION.id;
const TARGET_PATH = `/schedule/sessions/${ID}`;
const DETAIL = `GET /api/v1/class-sessions/${ID}`;
const CREATE = 'POST /api/v1/class-sessions';
const SUGGEST = 'POST /api/v1/class-sessions/suggestions';
const MAKEUP_URL = `/schedule/new?makeupOf=${ID}`;

const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'schedule.view',
    'schedule.manage',
    'schedule.override',
    'student.view',
  ],
};
const VIEWER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view'],
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = MANAGER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    'GET /api/v1/subjects': page(
      SUBJECTS.map((s) => ({
        id: s.id,
        name: s.name,
        education_level_id: '00000000-0000-4000-8000-0000000004f1',
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
        capacity: 10,
        is_active: true,
        availability: [],
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    [DETAIL]: () => jsonResponse(200, MAKEUP_AVAILABLE),
    ...routes,
  });
}

/** The routes of the session page for `session`. */
function sessionRoutes(session: ClassSessionDetail) {
  const base = `/api/v1/class-sessions/${session.id}`;
  return {
    [`GET ${base}`]: () => jsonResponse(200, session),
    [`GET ${base}/offers`]: () => jsonResponse(200, { items: [] }),
    [`GET ${base}/substitutions`]: () => jsonResponse(200, { items: [] }),
    'GET /api/v1/closeouts': page([]),
  };
}

function invalid(...errors: [field: string, code: string][]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: errors.map(([field, code]) => ({
        path: ['body', field],
        code,
        message: 'x',
      })),
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function taken(details?: Record<string, string>): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'makeup_already_scheduled',
      request_id: 'req-409',
      ...(details ? { details } : {}),
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function softConflicts(): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-409',
      conflicts: SOFT_CONFLICTS,
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const links = () => [...main().querySelectorAll('a')];
const linkTo = (href: string) =>
  links().find((a) => a.getAttribute('href') === href);

function typeReason(value: string) {
  const area = openDialog()!.querySelector('textarea')!;
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    )?.set?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function fillWhen() {
  typeInto(fieldByLabel(main(), 'Date'), '2031-01-06');
  typeInto(fieldByLabel(main(), 'Start'), '10:00');
  typeInto(fieldByLabel(main(), 'End'), '11:00');
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
});

describe('the session page (A)', () => {
  it('links a scheduled makeup (A1)', async () => {
    stub(sessionRoutes(MAKEUP_SCHEDULED));
    app = await renderApp(TARGET_PATH);
    expect(main().textContent).toContain('Makeup scheduled');
    expect(linkTo(`/schedule/sessions/${MAKEUP_SESSION.id}`)).toBeDefined();
    expect(linkTo(MAKEUP_URL)).toBeUndefined();
  });

  it('shows the deadline and the action to schedule.manage (A1)', async () => {
    stub(sessionRoutes(MAKEUP_AVAILABLE));
    app = await renderApp(TARGET_PATH);
    expect(main().textContent).toContain('A makeup must start by');
    expect(main().textContent).toContain('(America/Mexico_City)');
    expect(linkTo(MAKEUP_URL)?.textContent).toBe('Schedule makeup');
  });

  it('keeps the text and drops the action without it (A4)', async () => {
    stub(sessionRoutes(MAKEUP_AVAILABLE), VIEWER);
    app = await renderApp(TARGET_PATH);
    expect(main().textContent).toContain('A makeup must start by');
    expect(linkTo(MAKEUP_URL)).toBeUndefined();
  });

  it('says when the window has ended, without the action (A1)', async () => {
    stub(sessionRoutes(MAKEUP_ENDED));
    app = await renderApp(TARGET_PATH);
    expect(main().textContent).toContain('The makeup window has ended.');
    expect(linkTo(MAKEUP_URL)).toBeUndefined();
  });

  it('says when the cancellation allows none (A1)', async () => {
    stub(sessionRoutes(MAKEUP_NOT_ALLOWED));
    app = await renderApp(TARGET_PATH);
    expect(main().textContent).toContain(
      "This cancellation doesn't allow a makeup.",
    );
    expect(linkTo(MAKEUP_URL)).toBeUndefined();
  });

  it('shows nothing for a group or course session (A2)', async () => {
    for (const kind of ['group', 'course'] as const) {
      stub(sessionRoutes({ ...MAKEUP_AVAILABLE, kind }));
      app = await renderApp(TARGET_PATH);
      expect(main().textContent).not.toContain('Makeup');
      app.unmount();
      app = undefined;
    }
  });

  it('shows nothing on an ordinary session', async () => {
    stub(
      sessionRoutes({
        ...CONFIRMED_SESSION,
        starts_at: '2099-01-01T16:00:00Z',
      }),
    );
    app = await renderApp(`/schedule/sessions/${CONFIRMED_SESSION.id}`);
    expect(main().textContent).not.toContain('Makeup');
  });

  it('links what a session makes up, in any state (A3)', async () => {
    for (const session of [
      { ...MAKEUP_SESSION, starts_at: '2099-01-01T16:00:00Z' },
      { ...MAKEUP_AVAILABLE, id: MAKEUP_SESSION.id, makeup_of_session_id: ID },
    ]) {
      stub(sessionRoutes(session), VIEWER);
      app = await renderApp(`/schedule/sessions/${session.id}`);
      expect(main().textContent).toContain('Makeup of');
      expect(linkTo(TARGET_PATH)).toBeDefined();
      app.unmount();
      app = undefined;
    }
  });

  it('takes the deadline itself as open', () => {
    const until = MAKEUP_AVAILABLE.makeup!.until!;
    expect(makeupStatus(MAKEUP_AVAILABLE, new Date(until))?.kind).toBe(
      'available',
    );
    expect(
      makeupStatus(MAKEUP_AVAILABLE, new Date(Date.parse(until) + 1))?.kind,
    ).toBe('ended');
  });
});

describe('the request bodies (B6)', () => {
  const VALUES: ScheduleValues = {
    ...EMPTY_SCHEDULE,
    student: ANA,
    subjectId: SUBJECTS[0]!.id,
    date: '2031-01-06',
    start: '10:00',
    end: '11:00',
  };
  const ZONE = 'America/Mexico_City';

  it('adds the link only in makeup mode', () => {
    const plain = scheduleBody(VALUES, ZONE);
    expect(JSON.stringify(plain)).toBe(
      JSON.stringify({
        kind: 'individual',
        student_id: ANA.id,
        subject_id: SUBJECTS[0]!.id,
        tutor_user_id: null,
        classroom_id: null,
        starts_at: '2031-01-06T16:00:00.000Z',
        ends_at: '2031-01-06T17:00:00.000Z',
      }),
    );
    expect(scheduleBody(VALUES, ZONE, undefined, ID)).toEqual({
      ...plain,
      makeup_of_session_id: ID,
    });
  });

  it('never sends it to the suggestions', () => {
    expect(suggestionBody(VALUES, ZONE)).not.toHaveProperty(
      'makeup_of_session_id',
    );
  });
});

describe('target rules (B2, B3)', () => {
  const NOW = Date.parse('2030-01-01T00:00:00Z');

  it('names each reason it is unusable', () => {
    expect(makeupTargetProblem(MAKEUP_AVAILABLE, NOW)).toBeNull();
    expect(makeupTargetProblem(CONFIRMED_SESSION, NOW)).toBe('notCancelled');
    expect(
      makeupTargetProblem({ ...MAKEUP_AVAILABLE, kind: 'group' }, NOW),
    ).toBe('notIndividual');
    expect(makeupTargetProblem(MAKEUP_NOT_ALLOWED, NOW)).toBe('notAllowed');
    expect(makeupTargetProblem(MAKEUP_SCHEDULED, NOW)).toBe('alreadyScheduled');
    expect(makeupTargetProblem(MAKEUP_ENDED, NOW)).toBe('windowPassed');
  });

  it('uses the first participant', () => {
    const [first] = MAKEUP_AVAILABLE.participants;
    const values = makeupValues({
      ...MAKEUP_AVAILABLE,
      participants: [
        first!,
        { ...first!, student_id: 'other', display_name: 'Otro' },
      ],
    });
    expect(values).toEqual({
      ...EMPTY_SCHEDULE,
      student: { id: first!.student_id, name: first!.display_name },
      subjectId: MAKEUP_AVAILABLE.subject.id,
    });
  });
});

describe('error placement (C1)', () => {
  const link = {
    path: ['body', 'makeup_of_session_id'],
    code: 'makeup_window_passed',
    message: 'x',
  };
  const student = {
    path: ['body', 'student_id'],
    code: 'student_inactive',
    message: 'x',
  };
  const tutor = {
    path: ['body', 'tutor_user_id'],
    code: 'tutor_inactive',
    message: 'x',
  };

  it('keeps the plain form as it was', () => {
    expect(splitServerErrors([student, tutor], false)).toEqual({
      fields: {
        student: 'errors:student_inactive',
        tutor: 'errors:tutor_inactive',
      },
      page: [],
    });
  });

  it('sends the link and the fixed fields to the page', () => {
    expect(splitServerErrors([student, tutor, link], true)).toEqual({
      fields: { tutor: 'errors:tutor_inactive' },
      page: ['errors:student_inactive', 'errors:makeup_window_passed'],
    });
  });

  it('reads the taken session only from its own code', () => {
    expect(takenMakeupId(new Error('x'))).toBeNull();
  });
});

describe('makeup mode (B)', () => {
  it('starts with the fixed student and subject, and nothing else (B3-B5)', async () => {
    const inactive = {
      ...MAKEUP_AVAILABLE,
      subject: {
        ...MAKEUP_AVAILABLE.subject,
        id: '00000000-0000-4000-8000-0000000004ff',
        name: 'Latín',
        is_active: false,
      },
    };
    stub({ [DETAIL]: () => jsonResponse(200, inactive) });
    app = await renderApp(MAKEUP_URL);

    const text = main().textContent;
    expect(text).toContain(MAKEUP_AVAILABLE.participants[0]!.display_name);
    expect(text).toContain('Latín');
    expect(text).toContain('Makeup of the cancelled session');
    expect(text).toContain('Must start by');
    expect(linkTo(TARGET_PATH)?.textContent).toBe(
      'Back to the cancelled session',
    );
    expect(main().querySelector('input[type="radio"]')).toBeNull();
    expect(
      [...main().querySelectorAll('label')].map((l) => l.textContent),
    ).not.toContain('Subject');
    expect(main().querySelector('[role="switch"]')).toBeNull();
    expect(fieldByLabel(main(), 'Date').value).toBe('');
    expect(fieldByLabel(main(), 'Start').value).toBe('');
    expect(fieldByLabel(main(), 'End').value).toBe('');
    const selects = [...main().querySelectorAll('select')];
    expect(selects.map((select) => select.value)).toEqual(['', '']);
  });

  it('sends the link and shows it on success (B6, B8)', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    const [first] = MAKEUP_AVAILABLE.participants;
    expect(api.calls(CREATE)[0]!.body).toEqual({
      kind: 'individual',
      student_id: first!.student_id,
      subject_id: MAKEUP_AVAILABLE.subject.id,
      tutor_user_id: null,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
      makeup_of_session_id: ID,
    });
    expect(main().textContent).toContain('Session scheduled');
    expect(main().textContent).toContain('Makeup of');
    expect(linkTo(TARGET_PATH)).toBeDefined();
  });

  it('keeps the target subject with General in the options (HF-12)', async () => {
    const api = stub({
      'GET /api/v1/subjects': page(
        [GENERAL_SUBJECT, ...SUBJECTS].map((s) => ({
          id: s.id,
          name: s.name,
          education_level_id: '00000000-0000-4000-8000-0000000004f1',
          education_level_name: s.levelName,
          is_active: true,
          is_general: s.isGeneral ?? false,
          version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        })),
      ),
      [CREATE]: () => jsonResponse(201, CREATED),
    });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      subject_id: MAKEUP_AVAILABLE.subject.id,
    });
  });

  it('ignores the calendar pre-fill parameters (HF-02 C4)', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp(
      `${MAKEUP_URL}&date=2031-03-10&start=08:00&end=09:30` +
        `&tutor=${TUTORS[1]!.id}&room=${CLASSROOMS[0]!.id}`,
    );

    // The makeup values: the target's student and subject, nothing else.
    const text = main().textContent;
    expect(text).toContain(MAKEUP_AVAILABLE.participants[0]!.display_name);
    expect(text).toContain('Makeup of the cancelled session');
    expect(fieldByLabel(main(), 'Date').value).toBe('');
    expect(fieldByLabel(main(), 'Start').value).toBe('');
    expect(fieldByLabel(main(), 'End').value).toBe('');
    const selects = [...main().querySelectorAll('select')];
    expect(selects.map((select) => select.value)).toEqual(['', '']);

    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      tutor_user_id: null,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      makeup_of_session_id: ID,
    });
    expect(main().textContent).toContain('Session scheduled');
    expect(
      [...main().querySelectorAll('a')].some(
        (a) => a.textContent === 'View in calendar',
      ),
    ).toBe(false);
  });

  it('keeps the success panel: the target is read once', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp(MAKEUP_URL);
    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));

    // TanStack Query's focusManager listens on window, for this event only.
    window.dispatchEvent(new Event('visibilitychange'));
    await settle();

    expect(api.calls(DETAIL)).toHaveLength(1);
    expect(main().textContent).toContain('Session scheduled');
  });

  it('leaves the mode with "Schedule another" (B8)', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp(MAKEUP_URL);
    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));

    await click(buttonNamed(main(), 'Schedule another'));
    await settle();

    expect(window.location.search).toBe('');
    expect(main().textContent).not.toContain('Must start by');
    expect(main().querySelector('[role="switch"]')).not.toBeNull();
    expect(fieldByLabel(main(), 'Student').value).toBe('');
    expect(fieldByLabel(main(), 'Date').value).toBe('');
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  it('asks for times without the link, and overrides with it (B6, B7)', async () => {
    const api = stub({
      [CREATE]: [softConflicts, () => jsonResponse(201, CREATED)],
      [SUGGEST]: () => jsonResponse(200, NO_SUGGESTIONS),
    });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(SUGGEST)[0]!.body).not.toHaveProperty(
      'makeup_of_session_id',
    );
    await click(buttonNamed(main(), 'Override and schedule'));
    typeReason('Autorizado');
    await click(buttonNamed(openDialog()!, 'Override and schedule'));

    expect(api.calls(CREATE)[1]!.body).toMatchObject({
      makeup_of_session_id: ID,
      override: { reason: 'Autorizado' },
    });
  });

  it('closes the override dialog when the target was taken (C2)', async () => {
    stub({
      [CREATE]: [
        softConflicts,
        () => taken({ class_session_id: MAKEUP_SESSION.id }),
      ],
      [SUGGEST]: () => jsonResponse(200, NO_SUGGESTIONS),
    });
    app = await renderApp(MAKEUP_URL);
    fillWhen();
    await click(buttonNamed(main(), 'Schedule session'));
    await click(buttonNamed(main(), 'Override and schedule'));
    typeReason('Autorizado');

    await click(buttonNamed(openDialog()!, 'Override and schedule'));
    await settle();

    expect(openDialog()).toBeNull();
    expect(linkTo(`/schedule/sessions/${MAKEUP_SESSION.id}`)).toBeDefined();
  });

  it.each([
    ['makeup_target_unknown', "doesn't exist"],
    ['makeup_target_not_cancelled', 'no longer cancelled'],
    ['makeup_target_not_individual', 'Only an individual session'],
    ['makeup_not_allowed', "doesn't allow a makeup"],
    ['makeup_student_mismatch', "cancelled session's student"],
    ['makeup_subject_mismatch', "cancelled session's subject"],
    ['makeup_window_passed', 'must start by its deadline'],
  ])('shows %s at page level and keeps the form (C1)', async (code, text) => {
    stub({ [CREATE]: () => invalid(['makeup_of_session_id', code]) });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain(text);
    expect(fieldByLabel(main(), 'Date').value).toBe('2031-01-06');
  });

  it('shows an archived student as an alert, and a joined tutor on its field', async () => {
    stub({
      [CREATE]: () =>
        invalid(
          ['student_id', 'student_inactive'],
          ['tutor_user_id', 'tutor_inactive'],
        ),
    });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain('This student is inactive');
    expect(main().textContent).toContain('This tutor');
  });

  it('links the live makeup on a 409 (C2)', async () => {
    stub({
      [CREATE]: () => taken({ class_session_id: MAKEUP_SESSION.id }),
    });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain('already has a makeup');
    expect(linkTo(`/schedule/sessions/${MAKEUP_SESSION.id}`)?.textContent).toBe(
      'Open the makeup',
    );
  });

  it('shows the 409 without a link when the index caught the race (C2)', async () => {
    stub({ [CREATE]: () => taken() });
    app = await renderApp(MAKEUP_URL);
    fillWhen();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(alerts(main()).join(' ')).toContain('already has a makeup');
    expect(linkTo(`/schedule/sessions/${MAKEUP_SESSION.id}`)).toBeUndefined();
  });
});

describe('not usable (B2)', () => {
  const PLAIN_LINK = 'Schedule a regular session';

  function expectNotice(text: string) {
    expect(main().textContent).toContain(text);
    expect(linkTo('/schedule/new')?.textContent).toBe(PLAIN_LINK);
    expect(main().querySelector('form')).toBeNull();
  }

  it('a malformed id, without a request', async () => {
    const api = stub();
    app = await renderApp('/schedule/new?makeupOf=nope');
    expectNotice("wasn't found");
    expect(api.calls(DETAIL)).toHaveLength(0);
  });

  it('an empty id', async () => {
    stub();
    app = await renderApp('/schedule/new?makeupOf=');
    expectNotice("wasn't found");
  });

  it('an unknown session', async () => {
    stub({
      [DETAIL]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Not found',
            status: 404,
            code: 'not_found',
            request_id: 'req-404',
          }),
          {
            status: 404,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    app = await renderApp(MAKEUP_URL);
    expectNotice("wasn't found");
  });

  it.each([
    ['not cancelled', CONFIRMED_SESSION, "isn't cancelled"],
    [
      'not individual',
      { ...MAKEUP_AVAILABLE, kind: 'group' },
      'Only an individual session',
    ],
    ['not allowed', MAKEUP_NOT_ALLOWED, "doesn't allow a makeup"],
    ['a live makeup', MAKEUP_SCHEDULED, 'already has a makeup'],
    ['a passed deadline', MAKEUP_ENDED, 'window for this session has ended'],
  ])('%s', async (_name, session, text) => {
    stub({ [DETAIL]: () => jsonResponse(200, session) });
    app = await renderApp(MAKEUP_URL);
    expectNotice(text);
  });

  it('a read error shows the alert with a retry', async () => {
    const api = stub({
      [DETAIL]: [
        () => jsonResponse(403, {}),
        () => jsonResponse(200, MAKEUP_AVAILABLE),
      ],
    });
    app = await renderApp(MAKEUP_URL);
    expect(alerts(main())).toHaveLength(1);
    expect(main().querySelector('form')).toBeNull();

    await click(buttonNamed(main(), 'Try again'));
    await settle();

    expect(api.calls(DETAIL)).toHaveLength(2);
    expect(main().querySelector('form')).not.toBeNull();
  });

  it('the plain link leaves the mode', async () => {
    stub();
    app = await renderApp('/schedule/new?makeupOf=nope');
    await click(linkTo('/schedule/new')!);
    await settle();
    expect(main().querySelector('form')).not.toBeNull();
    expect(main().querySelector('[role="switch"]')).not.toBeNull();
  });
});

describe('the plain form', () => {
  it('reads no target and keeps its switch', async () => {
    const api = stub();
    app = await renderApp('/schedule/new');
    expect(api.calls(DETAIL)).toHaveLength(0);
    expect(main().querySelector('[role="switch"]')).not.toBeNull();
    expect(main().textContent).not.toContain('Must start by');
  });
});
