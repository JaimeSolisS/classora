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
  click,
  fieldByLabel,
  openDialog,
  pickOption,
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
  CLASSROOMS,
  CONFLICTS,
  CREATED,
  CARMEN_AVAILABILITY,
  CREATED_WITH_WARNINGS,
  MANY_TUTOR_SUGGESTIONS,
  NO_QUALIFIED_TUTORS,
  NO_SERIES_TUTOR_SUGGESTIONS,
  NO_SUGGESTIONS,
  NO_TUTOR_SUGGESTIONS,
  OVERRIDDEN,
  SERIES_TUTOR_SUGGESTIONS,
  SOFT_CONFLICTS,
  SUBJECTS,
  SUGGESTIONS,
  TUTOR_SUGGESTIONS,
  TUTOR_SUGGESTIONS_WITH_WARNINGS,
  TUTORS,
} from './fixtures';
import { EMPTY_RECURRENCE } from '../series/recurrence';
import {
  calendarWeekPath,
  canFindFreeTimes,
  canSuggestTutors,
  EMPTY_SCHEDULE,
  freeTimesBody,
  isChosenTutorUnsuggested,
  overrideAction,
  overrideCodes,
  overrideReasonError,
  prefillValues,
  scheduleBody,
  serverErrors,
  seriesTutorSuggestionBody,
  sessionOverride,
  slotValues,
  suggestionBody,
  suggestionEmptyState,
  tutorSuggestionKey,
  tutorSuggestionParams,
  validateOverrideReason,
  validateSchedule,
  weeklyAvailabilityText,
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
  permissions: ['schedule.view', 'schedule.manage', 'student.view'],
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
  await pickOption(main(), 'Student', 'Ana', 'Ana López');
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
    // The entry opens the calendar (#57 D1), which links to this page.
    expect(link?.getAttribute('href')).toBe('/schedule');
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
    await pickOption(main(), 'Estudiante', 'Ana', 'Ana López');
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

describe('suggested tutors (#51 G, HF-05b B)', () => {
  const SUGGEST_TUTORS = 'GET /api/v1/tutor-suggestions';
  const SUGGEST_SERIES = 'POST /api/v1/tutor-suggestions/series';
  const ASSIGNER: SessionUserBody = {
    ...SCHEDULER,
    permissions: [...SCHEDULER.permissions, 'schedule.assign'],
  };
  const OWNER: SessionUserBody = {
    ...ASSIGNER,
    permissions: [
      ...ASSIGNER.permissions,
      'tutor.view',
      'tutor.manage',
      'qualification.review',
    ],
  };

  function panel(): HTMLElement | undefined {
    return [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Suggested tutors',
    );
  }

  function tutorSelect(): HTMLSelectElement {
    return [...main().querySelectorAll('label')]
      .filter((l) => l.textContent === 'Tutor')
      .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
  }

  function fillSlot() {
    choose('Subject', SUBJECTS[0]!.id);
    choose('Classroom', CLASSROOMS[0]!.id);
    typeInto(fieldByLabel(main(), 'Date'), '2031-01-06');
    typeInto(fieldByLabel(main(), 'Start'), '10:00');
    typeInto(fieldByLabel(main(), 'End'), '11:00');
  }

  /** Past the 400 ms debounce, with real timers, then settles. */
  async function afterDebounce() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });
    await settle();
  }

  it('sends the slot as instants in the center zone (Tijuana DST)', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      subjectId: 's',
      date: '2026-03-09',
      start: '16:00',
      end: '17:00',
    };
    expect(tutorSuggestionParams(values, 'America/Tijuana')).toEqual({
      subject_id: 's',
      starts_at: '2026-03-09T23:00:00.000Z',
      ends_at: '2026-03-10T00:00:00.000Z',
    });
    expect(
      tutorSuggestionParams(
        { ...values, date: '2026-03-06', classroomId: 'r' },
        'America/Tijuana',
      ),
    ).toEqual({
      subject_id: 's',
      starts_at: '2026-03-07T00:00:00.000Z',
      ends_at: '2026-03-07T01:00:00.000Z',
      classroom_id: 'r',
    });
    expect(canSuggestTutors(values)).toBe(true);
    expect(canSuggestTutors({ ...values, subjectId: '' })).toBe(false);
    expect(canSuggestTutors({ ...values, end: '15:00' })).toBe(false);
  });

  it('loads only for a valid slot (and rule); the tutor is never part of it (E1)', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      subjectId: 's',
      date: '2031-01-06',
      start: '10:00',
      end: '11:00',
    };
    const rule = { ...EMPTY_RECURRENCE, weekdays: [1] };
    const once = tutorSuggestionKey(values, false, rule);
    expect(once).not.toBeNull();
    expect(tutorSuggestionKey({ ...values, subjectId: '' }, false, rule)).toBe(
      null,
    );
    expect(tutorSuggestionKey({ ...values, end: '09:00' }, false, rule)).toBe(
      null,
    );
    expect(tutorSuggestionKey({ ...values, tutorId: 't' }, false, rule)).toBe(
      once,
    );
    expect(
      tutorSuggestionKey({ ...values, classroomId: 'r' }, false, rule),
    ).not.toBe(once);
    // Series: a valid rule loads, an invalid one doesn't.
    const series = tutorSuggestionKey(values, true, rule);
    expect(series).not.toBeNull();
    expect(series).not.toBe(once);
    expect(
      tutorSuggestionKey(values, true, { ...rule, weekdays: [] }),
    ).toBeNull();
    expect(
      tutorSuggestionKey(values, true, { ...rule, ends: 'count', count: '0' }),
    ).toBeNull();
  });

  it('builds the series body without the student, tutor or deadline (E1)', () => {
    expect(
      seriesTutorSuggestionBody(
        {
          ...EMPTY_SCHEDULE,
          student: ANA,
          tutorId: 't',
          subjectId: 's',
          classroomId: 'r',
          date: '2031-01-06',
          start: '10:00',
          end: '11:30',
        },
        { ...EMPTY_RECURRENCE, weekdays: [3, 1], ends: 'count', count: '8' },
      ),
    ).toEqual({
      subject_id: 's',
      classroom_id: 'r',
      starts_on: '2031-01-06',
      start_time: '10:00',
      duration_minutes: 90,
      recurrence: { weekdays: [1, 3], interval: 1, until: null, count: 8 },
      limit: 50,
    });
  });

  it('explains an empty list from the counts (E1)', () => {
    const base = { items: [], eligible_count: 0 };
    expect(suggestionEmptyState(base)).toEqual({ kind: 'unknown' });
    expect(
      suggestionEmptyState({
        ...base,
        qualified_count: 0,
        busy_count: 0,
        unavailable_count: 0,
      }),
    ).toEqual({ kind: 'noneQualified' });
    expect(
      suggestionEmptyState({
        ...base,
        qualified_count: 2,
        busy_count: 0,
        unavailable_count: 2,
      }),
    ).toEqual({
      kind: 'noneFree',
      qualified: 2,
      busy: 0,
      unavailable: 2,
      occurrences: null,
    });
    expect(
      suggestionEmptyState({
        ...base,
        qualified_count: 1,
        busy_count: 1,
        unavailable_count: 0,
        occurrence_count: 4,
      }),
    ).toMatchObject({ kind: 'noneFree', occurrences: 4 });
    expect(
      suggestionEmptyState({
        ...base,
        qualified_count: 0,
        busy_count: 0,
        unavailable_count: 0,
        occurrence_count: 0,
      }),
    ).toEqual({ kind: 'noOccurrences' });
  });

  it('tells whether the chosen tutor is missing from a complete list (E1)', () => {
    const diego = TUTORS[1]!.id;
    const carmen = TUTORS[0]!.id;
    const list = { ...TUTOR_SUGGESTIONS, eligible_count: 3 };
    expect(isChosenTutorUnsuggested(list, '')).toBe(false);
    expect(isChosenTutorUnsuggested(list, diego)).toBe(false);
    expect(isChosenTutorUnsuggested(list, carmen)).toBe(false);
    expect(isChosenTutorUnsuggested(list, 'someone-else')).toBe(true);
    // More eligible than listed: it can't tell.
    expect(
      isChosenTutorUnsuggested({ ...list, eligible_count: 60 }, 'someone-else'),
    ).toBe(false);
  });

  it('summarizes the weekly windows by weekday, then start (E1)', () => {
    const name = (day: number) => ['', 'mon', 'tue', 'wed'][day] ?? '?';
    expect(weeklyAvailabilityText([], name)).toBeNull();
    expect(
      weeklyAvailabilityText(
        [
          { weekday: 3, starts_at: '09:00', ends_at: '13:00' },
          { weekday: 1, starts_at: '16:00', ends_at: '20:00' },
          { weekday: 1, starts_at: '08:00', ends_at: '10:00' },
        ],
        name,
      ),
    ).toBe('mon 08:00–10:00, mon 16:00–20:00, wed 09:00–13:00');
  });

  it('is hidden without schedule.assign', async () => {
    const api = stub(SCHEDULER);
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()).toBeUndefined();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(0);
    expect(
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Suggest tutors',
      ),
    ).toBe(false);
  });

  it('puts Tutor and Classroom after "When" (B1)', async () => {
    stub(ASSIGNER);
    app = await renderApp('/schedule/new');
    const labels = [...main().querySelectorAll('form label, form legend')]
      .map((element) => element.textContent)
      .filter((text) =>
        ['Subject', 'Date and time', 'Date', 'Tutor', 'Classroom'].includes(
          text ?? '',
        ),
      );
    expect(labels).toEqual([
      'Subject',
      'Date and time',
      'Date',
      'Tutor',
      'Classroom',
    ]);
  });

  it('loads by itself 400 ms after the last change, once (B2, fake timers)', async () => {
    let query: URLSearchParams | undefined;
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: (request) => {
        query = new URL(request.url).searchParams;
        return jsonResponse(200, TUTOR_SUGGESTIONS);
      },
    });
    app = await renderApp('/schedule/new');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      fillSlot();
      expect(panel()?.textContent).toContain('Looking for tutors…');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(399);
      });
      expect(api.calls(SUGGEST_TUTORS)).toHaveLength(0);
      // Another change restarts the wait.
      typeInto(fieldByLabel(main(), 'End'), '11:00');
      typeInto(fieldByLabel(main(), 'Start'), '10:00');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
    await settle();

    expect(Object.fromEntries(query!)).toEqual({
      subject_id: SUBJECTS[0]!.id,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
      classroom_id: CLASSROOMS[0]!.id,
      limit: '50',
    });
    const section = panel()!;
    expect(section.textContent).toContain(
      'Qualified for Álgebra, available and without another class on January 6, 2031 from 10:00 to 11:00.',
    );
    const rows = [...section.querySelectorAll('ol > li')].map(
      (li) => li.textContent,
    );
    expect(rows).toEqual([
      'Diego TorresPrefers this room0 min this week · 0 min that dayChoose',
      'Carmen Ruiz2 h 30 min this week · 1 h that day · limit 15 h/week · limit 4 h/dayWithin their limitsChoose',
      'Elena Vega5 h this week · 0 min that dayChoose',
    ]);
    expect(section.textContent).toContain('3 of 4 eligible tutors');
    // Focus never moves on an automatic load (D1).
    expect(document.activeElement).not.toBe(section.querySelector('button'));
  });

  it('refetches on a room change, never on a tutor change; back to the loaded slot: no request (B2)', async () => {
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);

    choose('Tutor', TUTORS[0]!.id);
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);
    expect(panel()).toBeDefined();

    choose('Classroom', '');
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(2);

    // Changed and changed back before the wait ends: no request.
    typeInto(fieldByLabel(main(), 'End'), '11:30');
    typeInto(fieldByLabel(main(), 'End'), '11:00');
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(2);
    expect(panel()!.textContent).toContain('Diego Torres');

    // An incomplete slot hides the panel.
    typeInto(fieldByLabel(main(), 'End'), '');
    expect(panel()).toBeUndefined();
  });

  it('drops a stale answer (B2)', async () => {
    let release: (response: Response) => void = () => {};
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: [
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
        () => jsonResponse(200, TUTOR_SUGGESTIONS_WITH_WARNINGS),
      ],
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);

    typeInto(fieldByLabel(main(), 'End'), '11:30');
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(2);
    release(jsonResponse(200, TUTOR_SUGGESTIONS));
    await settle();

    const text = panel()!.textContent;
    expect(text).toContain('1 eligible tutor.');
    expect(text).not.toContain('Diego Torres');
  });

  it('shows the warnings as text (B3)', async () => {
    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () =>
        jsonResponse(200, TUTOR_SUGGESTIONS_WITH_WARNINGS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    const items = [...panel()!.querySelectorAll('ol > li ul li')].map(
      (li) => li.textContent,
    );
    expect(items).toEqual([
      'Daily teaching limit exceeded on Jan 6, 2031: 270 minutes scheduled, limit 240.',
      'Break too short: 5 minutes between classes, 10 required.',
    ]);
    expect(panel()!.textContent).not.toContain('Within their limits');
  });

  it('"Choose" sets Tutor, marks it Chosen and keeps the list (B4)', async () => {
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();

    await click(buttonNamed(panel()!, 'Choose Diego Torres'));
    await afterDebounce();

    expect(tutorSelect().value).toBe(TUTORS[1]!.id);
    const chosen = buttonNamed(panel()!, 'Chosen: Diego Torres');
    expect(chosen.textContent).toBe('Chosen');
    expect(chosen.getAttribute('aria-pressed')).toBe('true');
    expect(
      buttonNamed(panel()!, 'Choose Carmen Ruiz').getAttribute('aria-pressed'),
    ).toBe('false');
    expect(main().querySelector('[aria-live="polite"]')?.textContent).toBe(
      'Tutor set to Diego Torres. Review the form and schedule the session.',
    );
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('marks a tutor from the URL as Chosen (B4, HF-02 pre-fill)', async () => {
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
    });
    app = await renderApp(
      `/schedule/new?date=2031-01-06&start=10:00&end=11:00&tutor=${TUTORS[0]!.id}`,
    );
    choose('Subject', SUBJECTS[0]!.id);
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);
    expect(
      buttonNamed(panel()!, 'Chosen: Carmen Ruiz').getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('shows five rows, then "View all" (B3)', async () => {
    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, MANY_TUTOR_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()!.querySelectorAll('ol > li')).toHaveLength(5);
    await click(buttonNamed(panel()!, 'View all (7)'));
    expect(panel()!.querySelectorAll('ol > li')).toHaveLength(7);
  });

  it('notes a picked tutor who is not suggested (B6)', async () => {
    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () =>
        jsonResponse(200, {
          ...TUTOR_SUGGESTIONS,
          items: [TUTOR_SUGGESTIONS.items[0]],
          eligible_count: 1,
        }),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(main().textContent).not.toContain('isn’t among the suggested');

    choose('Tutor', TUTORS[0]!.id);
    const note = [...main().querySelectorAll('p')].find((p) =>
      p.textContent?.startsWith('Carmen Ruiz isn’t among the suggested'),
    )!;
    expect(note).toBeDefined();
    expect(tutorSelect().getAttribute('aria-describedby')).toContain(note.id);
  });

  it('explains an empty list; the Tutors link only with qualification.review (B5)', async () => {
    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, NO_QUALIFIED_TUTORS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()!.textContent).toContain(
      'No active tutor has Álgebra assigned.',
    );
    expect(panel()!.querySelector('a')).toBeNull();
    app.unmount();

    stub(OWNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, NO_QUALIFIED_TUTORS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    const link = panel()!.querySelector('a')!;
    expect(link.textContent).toBe('Assign subjects in Tutors');
    expect(link.getAttribute('href')).toBe('/tutors');
    app.unmount();

    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, NO_TUTOR_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()!.textContent).toContain(
      '3 tutors are qualified for Álgebra, but none can at that time: 2 already have a class and 1 isn’t available.',
    );
    app.unmount();

    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () =>
        jsonResponse(200, {
          ...NO_TUTOR_SUGGESTIONS,
          busy_count: 0,
          unavailable_count: 3,
        }),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()!.textContent).toContain(
      '3 tutors are qualified for Álgebra, but none can at that time: 3 aren’t available.',
    );
  });

  it('shows the error state, and a 422 on the fields without moving the focus', async () => {
    stub(ASSIGNER, { [SUGGEST_TUTORS]: () => problem(500, 'internal_error') });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(panel()!.querySelector('[role="alert"]')).not.toBeNull();
    app.unmount();

    stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              {
                path: ['query', 'subject_id'],
                code: 'subject_inactive',
                message: 'x',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    const focused = document.activeElement;
    await afterDebounce();
    expect(panel()).toBeUndefined();
    expect(main().textContent).toContain(
      'This subject or its education level is inactive. Choose another one.',
    );
    expect(document.activeElement).toBe(focused);
  });

  it('asks the series route in series mode, and explains its empty list (B2, B5)', async () => {
    const api = stub(ASSIGNER, {
      [SUGGEST_TUTORS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
      [SUGGEST_SERIES]: [
        () => jsonResponse(200, SERIES_TUTOR_SUGGESTIONS),
        () => jsonResponse(200, NO_SERIES_TUTOR_SUGGESTIONS),
        () =>
          jsonResponse(200, {
            ...NO_SERIES_TUTOR_SUGGESTIONS,
            qualified_count: 0,
            unavailable_count: 0,
            occurrence_count: 0,
          }),
      ],
    });
    app = await renderApp('/schedule/new');
    fillSlot();
    await afterDebounce();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);

    await click(main().querySelector<HTMLInputElement>('[role="switch"]')!);
    await afterDebounce();
    const [series] = api.calls(SUGGEST_SERIES);
    expect(series!.body).toEqual({
      subject_id: SUBJECTS[0]!.id,
      classroom_id: CLASSROOMS[0]!.id,
      starts_on: '2031-01-06',
      start_time: '10:00',
      duration_minutes: 60,
      recurrence: { weekdays: [1], interval: 1, until: null, count: null },
      limit: 50,
    });
    expect(panel()!.textContent).toContain(
      'Qualified for Álgebra, available and free for all 12 sessions in the coming weeks.',
    );
    // No "Within their limits" and no free-time search in series mode.
    expect(panel()!.textContent).not.toContain('Within their limits');

    choose('Classroom', '');
    await afterDebounce();
    expect(panel()!.textContent).toContain(
      '1 tutor is qualified for Álgebra, but can’t at that time: 1 isn’t available in at least one of the 2 sessions.',
    );

    // Changed and changed back before the wait ends: no request.
    choose('Classroom', CLASSROOMS[0]!.id);
    choose('Classroom', '');
    await afterDebounce();
    expect(api.calls(SUGGEST_SERIES)).toHaveLength(2);

    typeInto(fieldByLabel(main(), 'End'), '11:15');
    await afterDebounce();
    expect(panel()!.textContent).toContain(
      'The series has no sessions to check in the coming weeks.',
    );
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);
  });
});

describe('the chosen tutor (HF-05b C)', () => {
  const SUGGEST_TUTORS = 'GET /api/v1/tutor-suggestions';
  const AVAILABILITY = `GET /api/v1/tutors/${TUTORS[0]!.id}/availability`;
  const OWNER: SessionUserBody = {
    ...SCHEDULER,
    permissions: [
      ...SCHEDULER.permissions,
      'schedule.assign',
      'tutor.view',
      'tutor.manage',
    ],
  };

  function freeTimes(): HTMLElement | undefined {
    return [...main().querySelectorAll('section')].find((s) =>
      s.querySelector('h2')?.textContent?.startsWith('Other free times'),
    );
  }

  it('builds the 7-day request and needs a tutor and every field (E1)', () => {
    const values = {
      ...EMPTY_SCHEDULE,
      student: ANA,
      subjectId: 's',
      tutorId: 't',
      date: '2031-01-06',
      start: '10:00',
      end: '11:00',
    };
    expect(canFindFreeTimes(values)).toBe(true);
    expect(canFindFreeTimes({ ...values, tutorId: '' })).toBe(false);
    expect(canFindFreeTimes({ ...values, student: null })).toBe(false);
    expect(freeTimesBody(values, 'America/Mexico_City')).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: 's',
      tutor_user_id: 't',
      classroom_id: null,
      starts_at: '2031-01-06T16:00:00.000Z',
      ends_at: '2031-01-06T17:00:00.000Z',
      limit: 5,
      search_from: '2031-01-06',
      search_to: '2031-01-12',
    });
  });

  it('shows the weekly availability, or none with the edit link (C1)', async () => {
    const api = stub(OWNER, {
      [AVAILABILITY]: [
        () => jsonResponse(200, CARMEN_AVAILABILITY),
        () => jsonResponse(200, { ...CARMEN_AVAILABILITY, weekly: [] }),
      ],
    });
    app = await renderApp('/schedule/new');
    choose('Tutor', TUTORS[0]!.id);
    await settleUntil(() =>
      main().textContent?.includes('weekly availability'),
    );
    expect(main().textContent).toContain(
      'Carmen Ruiz’s weekly availability: Mon 16:00–20:00, Wed 09:00–13:00.',
    );
    expect(main().textContent).toContain(
      'Date exceptions are checked when searching or scheduling.',
    );
    // Cached by tutor: choosing again doesn't read it again.
    choose('Tutor', '');
    choose('Tutor', TUTORS[0]!.id);
    await settle();
    expect(api.calls(AVAILABILITY)).toHaveLength(1);
    app.unmount();

    stub(OWNER, {
      [AVAILABILITY]: () =>
        jsonResponse(200, { ...CARMEN_AVAILABILITY, weekly: [] }),
    });
    app = await renderApp('/schedule/new');
    choose('Tutor', TUTORS[0]!.id);
    await settleUntil(() => main().textContent?.includes('no weekly'));
    expect(main().textContent).toContain(
      'Carmen Ruiz has no weekly availability recorded.',
    );
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Record their availability',
    );
    expect(link?.getAttribute('href')).toBe(
      `/tutors/${TUTORS[0]!.id}#availability`,
    );
  });

  it('reads no availability without tutor.view', async () => {
    const api = stub(SCHEDULER);
    app = await renderApp('/schedule/new');
    choose('Tutor', TUTORS[0]!.id);
    await settle();
    expect(api.calls(AVAILABILITY)).toHaveLength(0);
    expect(main().textContent).not.toContain('weekly availability');
  });

  it('finds free times once complete; choosing one fills the fields and reloads the tutors (C2, C3)', async () => {
    const api = stub(OWNER, {
      [AVAILABILITY]: () => jsonResponse(200, CARMEN_AVAILABILITY),
      [SUGGEST]: [
        () => jsonResponse(200, SUGGESTIONS),
        () => jsonResponse(200, NO_SUGGESTIONS),
      ],
      [SUGGEST_TUTORS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
    });
    app = await renderApp('/schedule/new');
    expect(
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Find free times',
      ),
    ).toBe(false);
    choose('Tutor', TUTORS[0]!.id);
    const button = buttonNamed(main(), 'Find free times');
    expect(button.disabled).toBe(true);
    await fillForm();
    expect(button.disabled).toBe(false);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });
    await settle();
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(1);

    await click(button);
    expect(api.calls(SUGGEST)[0]!.body).toMatchObject({
      tutor_user_id: TUTORS[0]!.id,
      search_from: '2031-01-06',
      search_to: '2031-01-12',
      limit: 5,
    });
    const section = freeTimes()!;
    expect(section.querySelector('h2')?.textContent).toBe(
      'Other free times for Carmen Ruiz',
    );
    await click(section.querySelectorAll<HTMLButtonElement>('ul button')[0]!);

    expect(fieldByLabel(main(), 'Date').value).toBe('2031-01-06');
    expect(fieldByLabel(main(), 'Start').value).toBe('11:00');
    expect(fieldByLabel(main(), 'End').value).toBe('12:00');
    expect(freeTimes()).toBeUndefined();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });
    await settle();
    expect(document.activeElement?.textContent).toBe('Schedule session');
    expect(api.calls(SUGGEST_TUTORS)).toHaveLength(2);
    expect(api.calls(CREATE)).toHaveLength(0);

    // The empty answer; a tutor change clears the list.
    await click(buttonNamed(main(), 'Find free times'));
    expect(freeTimes()!.textContent).toContain(
      'No free times for Carmen Ruiz in those 7 days.',
    );
    choose('Tutor', TUTORS[1]!.id);
    expect(freeTimes()).toBeUndefined();

    // Hidden in series mode.
    await click(main().querySelector<HTMLInputElement>('[role="switch"]')!);
    expect(
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Find free times',
      ),
    ).toBe(false);
  });
});

describe('pre-filled from the calendar (HF-02 C1)', () => {
  const OPTIONS = { tutors: TUTORS, classrooms: CLASSROOMS };
  const params = (query: string) => new URLSearchParams(query);
  const TUTOR = TUTORS[1]!.id;
  const ROOM = CLASSROOMS[0]!.id;

  it('reads the date, times, tutor and room; the student and subject stay empty', () => {
    expect(
      prefillValues(
        params(
          `date=2031-03-10&start=10:00&end=11:30&tutor=${TUTOR}&room=${ROOM}`,
        ),
        OPTIONS,
      ),
    ).toEqual({
      ...EMPTY_SCHEDULE,
      date: '2031-03-10',
      start: '10:00',
      end: '11:30',
      tutorId: TUTOR,
      classroomId: ROOM,
    });
  });

  it('is null without any valid parameter', () => {
    expect(prefillValues(params(''), OPTIONS)).toBeNull();
    expect(
      prefillValues(params('date=2031-02-30&start=25:00&tutor=x'), OPTIONS),
    ).toBeNull();
  });

  it('ignores an invalid date or time, without an error', () => {
    expect(
      prefillValues(params('date=2031-02-30&start=10:00&end=11:00'), OPTIONS),
    ).toMatchObject({ date: '', start: '10:00', end: '11:00' });
    expect(
      prefillValues(params('date=2031-03-10&start=9:00&end=11:00'), OPTIONS),
    ).toMatchObject({ date: '2031-03-10', start: '', end: '' });
    expect(
      prefillValues(params('date=2031-03-10&start=10:00&end=24:00'), OPTIONS),
    ).toMatchObject({ start: '10:00', end: '' });
  });

  it('drops an end that is not after the start', () => {
    expect(
      prefillValues(params('date=2031-03-10&start=10:00&end=10:00'), OPTIONS),
    ).toMatchObject({ start: '10:00', end: '' });
    expect(
      prefillValues(params('date=2031-03-10&start=10:00&end=09:45'), OPTIONS),
    ).toMatchObject({ start: '10:00', end: '' });
  });

  it('ignores a tutor or room that is not a loaded active option', () => {
    const unknown = '00000000-0000-4000-8000-0000000009ff';
    expect(
      prefillValues(
        params(`date=2031-03-10&tutor=${unknown}&room=${unknown}`),
        OPTIONS,
      ),
    ).toMatchObject({ tutorId: '', classroomId: '' });
    expect(
      prefillValues(params(`date=2031-03-10&tutor=${TUTOR}`), {
        tutors: [],
        classrooms: [],
      }),
    ).toMatchObject({ tutorId: '' });
  });

  it('links back to the week with the pre-filled tutor and room (C7)', () => {
    expect(
      calendarWeekPath('2031-03-10', { tutorId: TUTOR, classroomId: '' }),
    ).toBe(`/schedule?view=week&date=2031-03-10&tutor=${TUTOR}`);
    expect(
      calendarWeekPath('2031-03-10', { tutorId: '', classroomId: ROOM }),
    ).toBe(`/schedule?view=week&date=2031-03-10&room=${ROOM}`);
  });
});

describe('the pre-filled page (HF-02 F2)', () => {
  const TUTOR = TUTORS[1]!.id;
  const ROOM = CLASSROOMS[0]!.id;
  const PREFILLED = `/schedule/new?date=2031-03-10&start=10:00&end=11:30&tutor=${TUTOR}&room=${ROOM}`;

  function selected(label: string): string {
    const field = [...main().querySelectorAll('label')]
      .filter((l) => l.textContent === label)
      .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
    return field.value;
  }

  function calendarLink(): HTMLAnchorElement | undefined {
    return [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'View in calendar',
    );
  }

  it('renders the URL values in the fields, as a one-off session', async () => {
    const api = stub();
    app = await renderApp(PREFILLED);

    expect(fieldByLabel(main(), 'Date').value).toBe('2031-03-10');
    expect(fieldByLabel(main(), 'Start').value).toBe('10:00');
    expect(fieldByLabel(main(), 'End').value).toBe('11:30');
    expect(selected('Tutor')).toBe(TUTOR);
    expect(selected('Classroom')).toBe(ROOM);
    expect(selected('Subject')).toBe('');
    expect(main().textContent).not.toContain('Ana López');
    const repeats = main().querySelector<HTMLInputElement>('[role="switch"]');
    expect(repeats?.checked).toBe(false);
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('keeps the date and times when "Repeats weekly" turns on (C3)', async () => {
    stub();
    app = await renderApp(PREFILLED);

    await click(main().querySelector<HTMLInputElement>('[role="switch"]')!);

    expect(fieldByLabel(main(), 'Starts on').value).toBe('2031-03-10');
    expect(fieldByLabel(main(), 'Start').value).toBe('10:00');
    expect(fieldByLabel(main(), 'End').value).toBe('11:30');
    // 2031-03-10 is a Monday: its weekday is pre-checked.
    const monday = [...main().querySelectorAll('label')].find(
      (label) => label.textContent?.trim() === 'Monday',
    );
    const box = monday?.querySelector('input') ?? null;
    expect(box?.checked ?? false).toBe(true);
  });

  it('shows "View in calendar" after a pre-filled save, and not after a plain one', async () => {
    const api = stub(SCHEDULER, { [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp(PREFILLED);
    await pickStudent();
    choose('Subject', SUBJECTS[0]!.id);

    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      tutor_user_id: TUTOR,
      classroom_id: ROOM,
      starts_at: '2031-03-10T16:00:00.000Z',
      ends_at: '2031-03-10T17:30:00.000Z',
    });
    expect(main().textContent).toContain('Session scheduled');
    expect(calendarLink()?.getAttribute('href')).toBe(
      `/schedule?view=week&date=2031-03-10&tutor=${TUTOR}&room=${ROOM}`,
    );

    // "Schedule another" keeps today's behaviour (C6).
    await click(buttonNamed(main(), 'Schedule another'));
    expect(fieldByLabel(main(), 'Date').value).toBe('2031-03-10');
    expect(fieldByLabel(main(), 'Start').value).toBe('');
    expect(selected('Tutor')).toBe('');
    app.unmount();

    stub(SCHEDULER, { [CREATE]: () => jsonResponse(201, CREATED) });
    app = await renderApp('/schedule/new');
    await fillForm();
    await click(buttonNamed(main(), 'Schedule session'));
    expect(main().textContent).toContain('Session scheduled');
    expect(calendarLink()).toBeUndefined();
  });

  it('gives a Coordinator the forbidden page (A3)', async () => {
    stub({
      ...TEST_USER,
      roles: ['academic_coordinator'],
      permissions: ['schedule.view', 'schedule.assign', 'student.view'],
    });
    app = await renderApp(PREFILLED);

    expect(main().textContent).toContain("You don't have access to this page");
    expect(main().querySelector('form')).toBeNull();
  });
});
