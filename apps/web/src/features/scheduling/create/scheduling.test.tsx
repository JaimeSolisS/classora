import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import {
  ANA,
  CLASSROOMS,
  CONFLICTS,
  CREATED,
  CREATED_WITH_WARNINGS,
  NO_SUGGESTIONS,
  OVERRIDDEN,
  SOFT_CONFLICTS,
  SUBJECTS,
  SUGGESTIONS,
  TUTORS,
} from './fixtures';
import {
  EMPTY_SCHEDULE,
  overrideAction,
  overrideCodes,
  overrideReasonError,
  scheduleBody,
  serverErrors,
  sessionOverride,
  slotValues,
  suggestionBody,
  validateOverrideReason,
  validateSchedule,
} from './rules';

// #48 F1-F4: the route behind schedule.manage and its nav entry, the client
// rules, the instant conversion in the center zone, the 409 conflict panel
// (order, severity labels, focus) and the 422 field errors. #49 F2-F5: the
// override action's branches, the reason rules, the body sent and the
// outcomes of the resend. #50 C1-C3: the suggestions request after a 409,
// choosing a slot, the states of the section and the workload warnings.

const CREATE = 'POST /api/v1/class-sessions';
const SUGGEST = 'POST /api/v1/class-sessions/suggestions';

const SCHEDULER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.manage', 'student.view'],
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

function stub(
  user: SessionUserBody = SCHEDULER,
  routes: Record<string, Responder | Responder[]> = {},
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
    'GET /api/v1/students': page([
      {
        id: ANA.id,
        given_names: 'Ana',
        family_names: 'López',
        is_active: true,
        is_minor: true,
        school_grade: null,
        education_level: null,
      },
    ]),
    ...routes,
  });
}

function conflictResponse(
  // Soft first on purpose: the panel still lists the hard one first.
  conflicts: unknown[] = [CONFLICTS[1], CONFLICTS[2], CONFLICTS[0]],
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-409',
      detail: 'Server detail that must never reach the UI',
      conflicts,
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function invalidResponse(): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: [
        {
          path: ['body', 'tutor_user_id'],
          code: 'tutor_inactive',
          message: 'x',
        },
        {
          path: ['body', 'ends_at'],
          code: 'crosses_local_date',
          message: 'x',
        },
      ],
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function choose(label: string, value: string) {
  const select = [...main().querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function pickStudent() {
  typeInto(fieldByLabel(main(), 'Student'), 'Ana');
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
  const radio = main().querySelector<HTMLInputElement>(
    `input[type="radio"][value="${ANA.id}"]`,
  )!;
  await click(radio);
}

async function fillForm() {
  await pickStudent();
  choose('Subject', SUBJECTS[0]!.id);
  choose('Tutor', TUTORS[0]!.id);
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

describe('client rules (F2)', () => {
  it('requires the student, subject, date and times', () => {
    expect(validateSchedule(EMPTY_SCHEDULE)).toEqual({
      student: 'scheduling.validation.studentRequired',
      subject: 'scheduling.validation.subjectRequired',
      date: 'scheduling.validation.dateRequired',
      start: 'scheduling.validation.startRequired',
      end: 'scheduling.validation.endRequired',
    });
  });

  it('wants the end after the start (same date)', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      student: ANA,
      subjectId: 'x',
      date: '2031-01-06',
      start: '10:00',
    };
    expect(validateSchedule({ ...values, end: '10:00' })).toEqual({
      end: 'scheduling.validation.endAfterStart',
    });
    expect(validateSchedule({ ...values, end: '09:59' }).end).toBe(
      'scheduling.validation.endAfterStart',
    );
    expect(validateSchedule({ ...values, end: '10:15' })).toEqual({});
  });

  it('converts the local times to instants in the center zone (Tijuana DST)', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      student: ANA,
      subjectId: 's',
      date: '2026-03-06',
      start: '16:00',
      end: '17:00',
    };
    // Before the change (PST, UTC-8), after it (PDT, UTC-7).
    expect(scheduleBody(values, 'America/Tijuana')).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: 's',
      tutor_user_id: null,
      classroom_id: null,
      starts_at: '2026-03-07T00:00:00.000Z',
      ends_at: '2026-03-07T01:00:00.000Z',
    });
    const after = scheduleBody(
      { ...values, date: '2026-03-09', tutorId: 't', classroomId: 'r' },
      'America/Tijuana',
    );
    expect([after.starts_at, after.tutor_user_id, after.classroom_id]).toEqual([
      '2026-03-09T23:00:00.000Z',
      't',
      'r',
    ]);
  });

  it('maps the 422 field errors to their fields (F3)', () => {
    expect(
      serverErrors([
        { path: ['body', 'student_id'], code: 'student_unknown', message: '' },
        { path: ['body', 'subject_id'], code: 'subject_inactive', message: '' },
        {
          path: ['body', 'classroom_id'],
          code: 'classroom_inactive',
          message: '',
        },
        { path: ['body', 'starts_at'], code: 'not_whole_minutes', message: '' },
        { path: ['body', 'ends_at'], code: 'missing', message: '' },
        { path: ['body', 'kind'], code: 'literal_error', message: '' },
      ]),
    ).toEqual({
      student: 'errors:student_unknown',
      subject: 'errors:subject_inactive',
      classroom: 'errors:classroom_inactive',
      start: 'errors:not_whole_minutes',
      end: 'errors:validation_error',
    });
  });
});

describe('the page (F1, F3)', () => {
  it('is behind schedule.manage, with a Schedule nav entry', async () => {
    stub({ ...TEST_USER, permissions: ['student.view'] });
    app = await renderApp('/schedule/new');
    expect(main().textContent).not.toContain('Schedule a session');
    expect(
      [...app.container.querySelectorAll('nav a')].some(
        (a) => a.textContent === 'Schedule',
      ),
    ).toBe(false);
    app.unmount();

    stub();
    app = await renderApp('/schedule/new');
    expect(main().querySelector('h1')?.textContent).toBe('Schedule a session');
    const link = [...app.container.querySelectorAll('nav a')].find(
      (a) => a.textContent === 'Schedule',
    );
    expect(link?.getAttribute('href')).toBe('/schedule/new');
    expect(main().textContent).toContain(
      'Times in the center time zone (America/Mexico_City).',
    );
  });

  it('shows the client errors without a request', async () => {
    const api = stub();
    app = await renderApp('/schedule/new');

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(fieldByLabel(main(), 'Date').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(main().textContent).toContain('Choose a student.');
  });

  it('sends the instants and shows the success panel', async () => {
    const api = stub(SCHEDULER, { [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: SUBJECTS[0]!.id,
      tutor_user_id: TUTORS[0]!.id,
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
    });
    expect(main().textContent).toContain('Session scheduled');
    expect(main().textContent).toContain('Offered to Carmen Ruiz');

    await click(buttonNamed(main(), 'Schedule another'));
    expect(fieldByLabel(main(), 'Date').value).toBe('2031-01-06');
    expect(fieldByLabel(main(), 'Start').value).toBe('');
  });

  it('lists the conflicts hard first, with severity labels, and keeps the form', async () => {
    stub(SCHEDULER, { [CREATE]: () => conflictResponse() });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    const panel = main().querySelector('[role="alert"]')!;
    const items = [...panel.querySelectorAll('li')].map((li) => li.textContent);
    expect(items).toEqual([
      'Not allowedThe student already has a class at this time.',
      'Can be overriddenThe tutor is not available at this time (time off).',
      'Can be overriddenThe time is outside the opening hours of Jan 11, 2031 (09:00 to 14:00).',
    ]);
    expect(document.activeElement).toBe(panel.querySelector('h2'));
    expect(panel.textContent).not.toContain('Server detail');
    expect(fieldByLabel(main(), 'Start').value).toBe('10:00');
  });

  it('puts the 422 field errors on their fields', async () => {
    stub(SCHEDULER, { [CREATE]: invalidResponse });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    const end = fieldByLabel(main(), 'End');
    expect(end.getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(
      'A session must start and end on the same day.',
    );
    expect(main().textContent).toContain(
      "This tutor's account is not active. Choose another tutor.",
    );
    expect(main().querySelector('[role="alert"]')).toBeNull();
  });
});

const OVERRIDER: SessionUserBody = {
  ...SCHEDULER,
  permissions: ['schedule.manage', 'schedule.override', 'student.view'],
};

function typeReason(value: string) {
  const dialog = openDialog()!;
  const label = [...dialog.querySelectorAll('label')].find(
    (l) => l.textContent === 'Reason',
  )!;
  const area = document.getElementById(label.htmlFor) as HTMLTextAreaElement;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
  return area;
}

function reasonError(): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: [
        {
          path: ['body', 'override', 'reason'],
          code: 'reason_too_long',
          message: 'x',
        },
      ],
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

describe('override rules (#49 F2, F3)', () => {
  it('offers the button only to holders, and only without hard conflicts', () => {
    expect(overrideAction(SOFT_CONFLICTS, true)).toBe('override');
    expect(overrideAction(CONFLICTS, true)).toBe('fixHardFirst');
    expect(overrideAction(SOFT_CONFLICTS, false)).toBe('notAllowed');
    expect(overrideAction(CONFLICTS, false)).toBe('notAllowed');
  });

  it('overrides every soft code shown, once, with the trimmed reason', () => {
    expect(overrideCodes(CONFLICTS)).toEqual([
      'tutor_unavailable',
      'outside_operating_hours',
    ]);
    expect(
      overrideCodes([...SOFT_CONFLICTS, { ...SOFT_CONFLICTS[0]! }]),
    ).toEqual(['outside_operating_hours', 'tutor_not_qualified']);
    expect(sessionOverride(SOFT_CONFLICTS, '  Acordado  ')).toEqual({
      codes: ['outside_operating_hours', 'tutor_not_qualified'],
      reason: 'Acordado',
    });
  });

  it('wants a reason of 1-1000 characters once trimmed', () => {
    expect(validateOverrideReason('')).toBe(
      'scheduling.override.reasonRequired',
    );
    expect(validateOverrideReason('   ')).toBe(
      'scheduling.override.reasonRequired',
    );
    expect(validateOverrideReason(` ${'a'.repeat(1000)} `)).toBeUndefined();
    expect(validateOverrideReason('a'.repeat(1001))).toBe(
      'scheduling.override.reasonTooLong',
    );
  });

  it('maps the 422 on the override to the reason', () => {
    const at = (path: (string | number)[], code: string) => ({
      path,
      code,
      message: '',
    });
    expect(
      overrideReasonError([
        at(['body', 'override', 'reason'], 'reason_required'),
      ]),
    ).toBe('scheduling.override.reasonRequired');
    expect(
      overrideReasonError([
        at(['body', 'override', 'reason'], 'reason_too_long'),
      ]),
    ).toBe('scheduling.override.reasonTooLong');
    expect(
      overrideReasonError([
        at(['body', 'override', 'codes', 0], 'code_unknown'),
      ]),
    ).toBe('errors:validation_error');
    expect(
      overrideReasonError([at(['body', 'ends_at'], 'crosses_local_date')]),
    ).toBeNull();
  });
});

describe('overriding on the page (#49 F2-F4)', () => {
  it('tells a non-holder who can override, without a button', async () => {
    stub(SCHEDULER, { [CREATE]: () => conflictResponse(SOFT_CONFLICTS) });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(main().textContent).toContain(
      'Only an Owner, or an Administrator with the override permission, can override these conflicts.',
    );
    expect(
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Override and schedule',
      ),
    ).toBe(false);
  });

  it('asks a holder to fix the hard conflicts first', async () => {
    stub(OVERRIDER, { [CREATE]: () => conflictResponse() });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    expect(main().textContent).toContain(
      'Fix the conflicts that aren’t allowed first.',
    );
    expect(
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Override and schedule',
      ),
    ).toBe(false);
  });

  it('resends the same body with every soft code and the reason', async () => {
    const api = stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_CONFLICTS),
        () => jsonResponse(201, OVERRIDDEN),
      ],
    });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));

    await click(buttonNamed(main(), 'Override and schedule'));
    const dialog = openDialog()!;
    expect(
      [...dialog.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual([
      'The center is closed on that day of the week (Jan 12, 2031).',
      'The tutor is not qualified for this subject.',
    ]);
    // An empty reason never reaches the server.
    await click(buttonNamed(dialog, 'Override and schedule'));
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(dialog.textContent).toContain('Give a reason for the override.');

    const area = typeReason('  La familia lo pidió  ');
    expect(area.getAttribute('aria-invalid')).toBe('true');
    await click(buttonNamed(openDialog()!, 'Override and schedule'));

    const [first, second] = api.calls(CREATE);
    expect(second!.body).toEqual({
      ...(first!.body as object),
      override: {
        codes: ['outside_operating_hours', 'tutor_not_qualified'],
        reason: 'La familia lo pidió',
      },
    });
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('Session scheduled');
    expect(main().textContent).toContain('Scheduled with an override');
    expect(main().textContent).toContain(
      'Overridden rules: Opening hours, Tutor qualification.',
    );
  });

  it('closes the dialog on a new 409 and shows the new list', async () => {
    stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_CONFLICTS),
        () => conflictResponse(CONFLICTS),
      ],
    });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    await click(buttonNamed(main(), 'Override and schedule'));

    typeReason('Acordado');
    await click(buttonNamed(openDialog()!, 'Override and schedule'));

    expect(openDialog()).toBeNull();
    const panel = main().querySelector('[role="alert"]')!;
    expect(panel.querySelectorAll('li')).toHaveLength(3);
    expect(panel.textContent).toContain('Not allowed');
    expect(document.activeElement).toBe(panel.querySelector('h2'));
  });

  it('shows a 403 in the dialog and a 422 on the reason', async () => {
    stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_CONFLICTS),
        () => problem(403, 'permission_denied'),
        reasonError,
      ],
    });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    await click(buttonNamed(main(), 'Override and schedule'));

    typeReason('Acordado');
    await click(buttonNamed(openDialog()!, 'Override and schedule'));
    expect(openDialog()!.querySelector('[role="alert"]')).not.toBeNull();

    await click(buttonNamed(openDialog()!, 'Override and schedule'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'The reason can have at most 1000 characters.',
    );
    const area = dialog.querySelector('textarea')!;
    expect(area.getAttribute('aria-invalid')).toBe('true');
  });
});

// --- #50: suggested times and workload warnings --------------------------------

describe('suggested times (#50 C1)', () => {
  it('sends the form values without an override or a window', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      student: ANA,
      subjectId: 's',
      tutorId: 't',
      date: '2031-01-06',
      start: '10:00',
      end: '11:00',
    };
    expect(suggestionBody(values, 'America/Mexico_City')).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: 's',
      tutor_user_id: 't',
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
      limit: 5,
    });
  });

  it('turns a slot into local fields in the center zone (Tijuana DST)', () => {
    // 2026-03-08: 01:30 is still PST (UTC-8); 03:00 is already PDT (UTC-7).
    expect(
      slotValues(
        { starts_at: '2026-03-08T09:30:00Z', ends_at: '2026-03-08T10:00:00Z' },
        'America/Tijuana',
      ),
    ).toEqual({ date: '2026-03-08', start: '01:30', end: '03:00' });
    // 2026-11-01: the first 01:30 (PDT) and an end in PST.
    expect(
      slotValues(
        { starts_at: '2026-11-01T08:30:00Z', ends_at: '2026-11-01T10:00:00Z' },
        'America/Tijuana',
      ),
    ).toEqual({ date: '2026-11-01', start: '01:30', end: '02:00' });
  });

  it('asks for free times after a 409 and fills the fields with the chosen one', async () => {
    const api = stub(SCHEDULER, {
      [CREATE]: () => conflictResponse(),
      [SUGGEST]: () => jsonResponse(200, SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));
    await settle();

    const [request] = api.calls(SUGGEST);
    expect(request!.body).toEqual({
      ...(api.calls(CREATE)[0]!.body as object),
      limit: 5,
    });
    expect(request!.body).not.toHaveProperty('search_from');
    expect(request!.body).not.toHaveProperty('search_to');
    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Suggested times',
    )!;
    const buttons = [...section.querySelectorAll('button')].map(
      (b) => b.textContent,
    );
    expect(buttons).toEqual([
      'January 6, 2031, 11:00 AM to 12:00 PM',
      'January 6, 2031, 11:15 AM to 12:15 PM',
      'January 6, 2031, 9:00 AM to 10:00 AM',
    ]);
    expect(section.textContent).toContain(
      'Times in the center time zone (America/Mexico_City).',
    );
    // The second slot's note.
    expect(section.textContent).toContain('Workload warning');
    expect(section.textContent).toContain(
      'Daily teaching limit exceeded on Jan 6, 2031: 300 minutes scheduled, limit 240.',
    );

    await click(buttonNamed(section, 'January 6, 2031, 11:00 AM to 12:00 PM'));

    expect(fieldByLabel(main(), 'Date').value).toBe('2031-01-06');
    expect(fieldByLabel(main(), 'Start').value).toBe('11:00');
    expect(fieldByLabel(main(), 'End').value).toBe('12:00');
    expect(main().querySelector('[role="alert"]')).toBeNull();
    expect(main().textContent).not.toContain('Suggested times');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Schedule session'),
    );
    expect(main().querySelector('[aria-live="polite"]')?.textContent).toBe(
      'Time changed to January 6, 2031, 11:00 AM to 12:00 PM. Review it and schedule the session.',
    );
    // Not submitted: one create only.
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  it('shows the loading, empty and error states', async () => {
    stub(SCHEDULER, { [CREATE]: () => conflictResponse() });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    expect(main().textContent).toContain('Looking for other times…');
    app.unmount();

    stub(SCHEDULER, {
      [CREATE]: () => conflictResponse(),
      [SUGGEST]: () => jsonResponse(200, NO_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    await settle();
    expect(main().textContent).toContain(
      'No other free times between January 6, 2031 and January 12, 2031.',
    );
    app.unmount();

    stub(SCHEDULER, {
      [CREATE]: () => conflictResponse(),
      [SUGGEST]: () => problem(500, 'internal_error'),
    });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    await settle();
    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Suggested times',
    )!;
    expect(section.querySelector('[role="alert"]')).not.toBeNull();
    expect(fieldByLabel(main(), 'Start').value).toBe('10:00');
  });
});

describe('workload warnings (#50 C2)', () => {
  it('lists the warnings of the 201 in the success panel', async () => {
    stub(SCHEDULER, {
      [CREATE]: () => jsonResponse(201, CREATED_WITH_WARNINGS),
    });
    app = await renderApp('/schedule/new');
    await fillForm();

    await click(buttonNamed(main(), 'Schedule session'));

    const status = [...main().querySelectorAll('[role="status"]')].find((s) =>
      s.textContent?.includes('Workload warnings'),
    )!;
    expect(
      [...status.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual([
      'Daily teaching limit exceeded on Jan 6, 2031: 300 minutes scheduled, limit 240.',
      'Weekly teaching limit exceeded in the week of Jan 6, 2031: 1260 minutes scheduled, limit 1200.',
      'Break too short: 1 minute between classes, 15 required.',
    ]);
  });

  it('shows nothing extra without warnings, and Spanish messages', async () => {
    stub(SCHEDULER, { [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    expect(main().textContent).toContain('Session scheduled');
    expect(main().textContent).not.toContain('Workload warnings');
    app.unmount();

    applyLanguage('es');
    stub(SCHEDULER, {
      [CREATE]: () => jsonResponse(201, CREATED_WITH_WARNINGS),
    });
    app = await renderApp('/schedule/new');
    typeInto(fieldByLabel(main(), 'Estudiante'), 'Ana');
    await act(async () => {
      await new Promise((resolve) =>
        setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
      );
    });
    await settle();
    await click(
      main().querySelector<HTMLInputElement>(
        `input[type="radio"][value="${ANA.id}"]`,
      )!,
    );
    choose('Materia', SUBJECTS[0]!.id);
    typeInto(fieldByLabel(main(), 'Fecha'), '2031-01-06');
    typeInto(fieldByLabel(main(), 'Inicio'), '10:00');
    typeInto(fieldByLabel(main(), 'Fin'), '11:00');
    await click(buttonNamed(main(), 'Programar sesión'));
    expect(main().textContent).toContain('Avisos de carga de trabajo');
    expect(main().textContent).toContain(
      'Descanso demasiado corto: 1 minuto entre clases, se requieren 15.',
    );
  });
});
