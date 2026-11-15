import { act, createElement } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { familyItem, tutorItem } from '@/features/my-schedule/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import { alerts, click, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { CloseoutPage } from './CloseoutPage';
import {
  APPROVED_RECORD,
  ATTENDANCE_ABSENT,
  ATTENDANCE_COMPLETED,
  ATTENDANCE_LOCKED,
  ATTENDANCE_MARKED,
  ATTENDANCE_UNMARKED,
  NO_CLOSEOUT,
  NOT_CLOSABLE,
  NOT_STARTED,
  NOT_SUBMITTER_CHANGES_REQUESTED,
  OPEN,
  SESSION_ID,
  SUBMITTED,
  SUMMARY,
  TUTOR_APPROVED,
  TUTOR_ON_BEHALF_APPROVED,
  TUTOR_ON_BEHALF_SUBMITTED,
  TUTOR_CHANGES_REQUESTED,
  TUTOR_CHANGES_REQUESTED_ON_CORRECTION,
  TUTOR_CORRECTION_SUBMITTED,
  TUTOR_RESUBMITTED,
  ZONE,
} from './fixtures';
import {
  minutesError,
  noteError,
  serverSummaryErrors,
  summaryBody,
  summaryDraftOf,
  summaryErrors,
  tutorCloseoutLink,
  EMPTY_SUMMARY,
} from './rules';

// #77 H6: the tutor's closeout flow (step gating: Next waits for every
// mark, the minutes range, the required summary fields toggled by
// all-absent; only valid bodies sent; 422 and 409 mapping; the read-only
// and blocked renders; the stepper's accessible names in es and en) and
// the My schedule dialog's closeout link by state and time (a frozen
// clock).

const PATH = `/my-schedule/sessions/${SESSION_ID}/closeout`;
const GET = `GET /api/v1/class-sessions/${SESSION_ID}/closeout`;
const POST = `POST /api/v1/class-sessions/${SESSION_ID}/closeout`;
const ATTENDANCE = `GET /api/v1/class-sessions/${SESSION_ID}/attendance`;
const PUT = `PUT /api/v1/class-sessions/${SESSION_ID}/attendance`;
const RESUBMIT = `PUT /api/v1/class-sessions/${SESSION_ID}/closeout`;

function eager(list: RouteObject[]): RouteObject[] {
  return list.map((route) => {
    if (route.path === 'my-schedule/sessions/:sessionId/closeout') {
      return {
        path: route.path,
        handle: route.handle as unknown,
        element: createElement(CloseoutPage),
      };
    }
    return route.children
      ? { ...route, children: eager(route.children) }
      : route;
  });
}

const ROUTES = eager(routes);

const TUTOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';

function textarea(label: string): HTMLTextAreaElement {
  const found = [...main().querySelectorAll('label')].find((l) =>
    l.textContent?.startsWith(label),
  );
  const field = found ? document.getElementById(found.htmlFor) : null;
  if (!(field instanceof HTMLTextAreaElement)) {
    throw new Error(`No textarea ${label}`);
  }
  return field;
}

function typeText(field: HTMLTextAreaElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function minutes(): HTMLInputElement {
  const input = main().querySelector('input[type="number"]');
  if (!(input instanceof HTMLInputElement)) {
    throw new Error('No minutes input');
  }
  return input;
}

const stepHeading = () => main().querySelector('h3')?.textContent ?? '';
const current = () =>
  main().querySelector('[aria-current="step"]')?.textContent ?? '';

async function open(
  closeouts: unknown[],
  attendances: unknown[] = [ATTENDANCE_MARKED],
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(TUTOR),
    [GET]: closeouts.map((body) => () => jsonResponse(200, body)),
    [ATTENDANCE]: attendances.map((body) => () => jsonResponse(200, body)),
  });
  app = await renderApp(PATH, ROUTES);
  await settleUntil(() => text().includes('Matemáticas'));
  await settle();
}

async function toSummary(): Promise<void> {
  await click(buttonNamed(main(), 'Next'));
  await click(buttonNamed(main(), 'Next'));
}

async function fillRequired(): Promise<void> {
  typeText(textarea('Topics'), '  Fracciones  ');
  typeText(textarea('Performance'), 'Bien');
  typeText(textarea('Next steps'), 'Decimales');
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('the rules', () => {
  it('mirrors the API summary rules, all-absent included', () => {
    expect(summaryErrors(EMPTY_SUMMARY, false)).toEqual({
      topics: 'summary_required',
      performance: 'summary_required',
      next_steps: 'summary_required',
    });
    expect(summaryErrors(EMPTY_SUMMARY, true)).toEqual({});
    expect(
      summaryErrors(
        { ...EMPTY_SUMMARY, topics: 'x'.repeat(2001), homework: ' ' },
        true,
      ),
    ).toEqual({ topics: 'too_long' });
    expect(
      summaryErrors(
        {
          topics: ` ${'x'.repeat(2000)} `,
          performance: 'a',
          homework: '',
          next_steps: 'b',
        },
        false,
      ),
    ).toEqual({});
  });

  it.each([
    ['0', null],
    ['720', null],
    ['55', null],
    ['-1', 'minutes_range'],
    ['721', 'minutes_range'],
    ['', 'minutes_range'],
    ['5.5', 'minutes_range'],
  ])('minutes %s → %s', (value, expected) => {
    expect(minutesError(value)).toBe(expected);
  });

  it('sends a trimmed summary without empty fields', () => {
    expect(
      summaryBody({
        topics: '  A ',
        performance: 'B',
        homework: '   ',
        next_steps: 'C',
      }),
    ).toEqual({ topics: 'A', performance: 'B', next_steps: 'C' });
  });

  it('maps a server 422 onto the summary fields', () => {
    const error = new ApiError({
      code: 'validation_error',
      status: 422,
      problem: {
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'validation_error',
        request_id: 'r',
        errors: [
          {
            path: ['body', 'summary', 'topics'],
            code: 'summary_required',
            message: 'x',
          },
          {
            path: ['body', 'summary', 'homework'],
            code: 'too_long',
            message: 'x',
          },
        ],
      },
    });
    expect(serverSummaryErrors(error)).toEqual({
      topics: 'summary_required',
      homework: 'too_long',
    });
    expect(serverSummaryErrors(new Error('x'))).toEqual({});
  });

  const START = Date.parse('2026-11-09T16:00:00Z');
  const item = (state: string) => ({
    state,
    starts_at: '2026-11-09T16:00:00Z',
  });

  it.each([
    ['confirmed', START - 1, null],
    ['confirmed', START, 'submit'],
    ['confirmed', START + 7 * 86_400_000, 'submit'],
    ['completed', START - 1, 'view'],
    ['offered', START + 1, null],
    ['cancelled', START + 1, null],
    ['skipped_closure', START + 1, null],
  ])('a tutor %s item at %s links to %s', (state, now, expected) => {
    expect(tutorCloseoutLink(item(state), now)).toBe(expected);
  });

  // #305 C1: the dialog's closeout status (undefined while loading or on
  // an error) picks between View and Fix.
  it.each([
    ['completed', undefined, 'view'],
    ['completed', null, 'view'],
    ['completed', 'submitted', 'view'],
    ['completed', 'approved', 'view'],
    ['completed', 'changes_requested', 'fix'],
    ['confirmed', 'changes_requested', 'submit'],
    ['offered', 'changes_requested', null],
  ])(
    'a tutor %s item with closeout %s links to %s',
    (state, status, expected) => {
      expect(tutorCloseoutLink(item(state), START + 1, status)).toBe(expected);
    },
  );

  it('prefills an edit mode from the closeout and checks the note', () => {
    expect(summaryDraftOf({ ...SUMMARY, homework: null })).toEqual({
      ...SUMMARY,
      homework: '',
    });
    expect(summaryDraftOf(null)).toEqual(EMPTY_SUMMARY);
    expect(noteError('  ', false)).toBeNull();
    expect(noteError('  ', true)).toBe('reason_required');
    expect(noteError(` ${'x'.repeat(1000)} `, true)).toBeNull();
    expect(noteError('x'.repeat(1001), false)).toBe('reason_too_long');
  });
});

describe('the closeout flow (G1)', () => {
  it('announces the steps in English and Spanish', async () => {
    await open([OPEN]);

    expect(main().querySelector('h1')?.textContent).toBe('Close out session');
    expect(text()).toContain('Matemáticas (Secundaria)');
    expect(stepHeading()).toBe('Step 1 of 4: Attendance');
    expect(current()).toBe('1. Attendance');
    expect(main().querySelector('ol')?.getAttribute('aria-label')).toBe(
      'Closeout steps',
    );

    await i18n.changeLanguage('es');
    await settle();
    expect(stepHeading()).toBe('Paso 1 de 4: Asistencia');
    expect(main().querySelector('ol')?.getAttribute('aria-label')).toBe(
      'Pasos del cierre',
    );
    expect(buttonNamed(main(), 'Siguiente')).toBeTruthy();
  });

  it('keeps Next disabled until every participant is marked', async () => {
    await open([OPEN, OPEN], [ATTENDANCE_UNMARKED, ATTENDANCE_MARKED]);
    api.set({ [PUT]: () => jsonResponse(200, ATTENDANCE_MARKED) });

    expect(buttonNamed(main(), 'Next').disabled).toBe(true);
    expect(text()).toContain('Mark these students first:');
    expect(text()).toContain('Bruno Gómez');

    const bruno = [...main().querySelectorAll('fieldset')].find(
      (f) => f.querySelector('legend')?.textContent === 'Bruno Gómez',
    );
    const present = [...(bruno?.querySelectorAll('label') ?? [])]
      .find((l) => l.textContent === 'Present')
      ?.querySelector('input');
    await click(present!);
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(() => !buttonNamed(main(), 'Next').disabled);

    expect(api.calls(PUT)[0]?.body).toEqual({
      version: 3,
      marks: [
        {
          student_id: ATTENDANCE_UNMARKED.items[1]!.student.id,
          status: 'present',
        },
      ],
    });
    expect(text()).not.toContain('Mark these students first:');
  });

  it('checks the minutes range, prefilled with the scheduled minutes', async () => {
    await open([OPEN]);
    await click(buttonNamed(main(), 'Next'));

    expect(stepHeading()).toBe('Step 2 of 4: Teaching time');
    expect(minutes().value).toBe('60');
    expect(text()).toContain('Scheduled: 60 min');
    typeInto(minutes(), '721');
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 2 of 4: Teaching time');
    expect(text()).toContain('Enter a whole number of minutes from 0 to 720.');
    expect(minutes().getAttribute('aria-invalid')).toBe('true');

    typeInto(minutes(), '55');
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 3 of 4: Summary');
  });

  it('requires topics, performance and next steps, then sends a valid body', async () => {
    await open([OPEN, SUBMITTED]);
    api.set({ [POST]: () => jsonResponse(201, SUBMITTED) });
    await click(buttonNamed(main(), 'Next'));
    typeInto(minutes(), '55');
    await click(buttonNamed(main(), 'Next'));

    expect(text()).toContain('Topics (required)');
    expect(text()).toContain('Homework (optional)');
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 3 of 4: Summary');
    expect(textarea('Topics').getAttribute('aria-invalid')).toBe('true');
    expect(text()).toContain('Fill in this part of the summary.');

    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 4 of 4: Review');
    expect(text()).toContain('55 of 60 min');
    expect(text()).toContain('Fracciones');
    expect(buttonNamed(main(), 'Edit Summary')).toBeTruthy();

    await click(buttonNamed(main(), 'Submit for review'));
    await settleUntil(() => text().includes('Closeout submitted for review.'));

    expect(api.calls(POST)[0]?.body).toEqual({
      version: 4,
      actual_minutes: 55,
      summary: {
        topics: 'Fracciones',
        performance: 'Bien',
        next_steps: 'Decimales',
      },
    });
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Closeout submitted for review.',
    );
    expect(text()).toContain('Submitted for review');
  });

  it('makes the summary optional when everyone is absent', async () => {
    await open([OPEN], [ATTENDANCE_ABSENT]);
    await toSummary();

    expect(text()).toContain('Everyone was absent');
    expect(text()).toContain('Topics (optional)');
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 4 of 4: Review');
  });

  it('keeps typed text when moving between steps', async () => {
    await open([OPEN]);
    await toSummary();
    typeText(textarea('Topics'), 'Fracciones');
    await settle();
    await click(buttonNamed(main(), 'Back'));
    await click(buttonNamed(main(), 'Next'));

    expect(textarea('Topics').value).toBe('Fracciones');
  });

  it('maps a server 422 back onto the summary fields', async () => {
    await open([OPEN]);
    api.set({
      [POST]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            code: 'validation_error',
            request_id: 'r',
            errors: [
              {
                path: ['body', 'summary', 'performance'],
                code: 'summary_required',
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
    await toSummary();
    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Submit for review'));
    await settleUntil(() => stepHeading() === 'Step 3 of 4: Summary');

    expect(textarea('Performance').getAttribute('aria-invalid')).toBe('true');
  });

  it('offers Reload after a version conflict', async () => {
    await open([OPEN, OPEN]);
    api.set({ [POST]: () => problem(409, 'version_conflict') });
    await toSummary();
    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Submit for review'));
    await settleUntil(() => text().includes('Session changed elsewhere'));

    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => api.calls(GET).length === 2);
  });

  it('goes back to attendance when it is incomplete', async () => {
    await open([OPEN, OPEN], [ATTENDANCE_MARKED, ATTENDANCE_UNMARKED]);
    api.set({ [POST]: () => problem(409, 'closeout_attendance_incomplete') });
    await toSummary();
    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Submit for review'));
    await settleUntil(() => stepHeading() === 'Step 1 of 4: Attendance');
    await settleUntil(() => text().includes('Bruno Gómez'));

    expect(text()).toContain('Some students have no attendance mark yet.');
  });

  it('shows the read-only view once already submitted', async () => {
    await open([OPEN, SUBMITTED]);
    api.set({ [POST]: () => problem(409, 'closeout_already_submitted') });
    await toSummary();
    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Submit for review'));
    await settleUntil(() => text().includes('Submitted for review'));
  });

  it("shows no on-behalf line on the tutor's own submission (#301b E5)", async () => {
    await open([TUTOR_RESUBMITTED]);
    expect(text()).not.toContain('on your behalf');
  });

  it('renders a submitted closeout read-only', async () => {
    await open([SUBMITTED]);

    expect(text()).toContain('Submitted for review');
    expect(text()).toContain('55 of 60 min');
    expect(text()).toContain('Fracciones equivalentes');
    expect(main().querySelector('ol')).toBeNull();
  });

  // #305: the review status views, each with a heading; the action only
  // when its flag is set.
  it.each([
    [
      'changes requested',
      TUTOR_CHANGES_REQUESTED,
      'Changes requested by the reviewer',
      [
        'Añade la tarea asignada y los siguientes pasos con más detalle.',
        '55 of 60 min',
      ],
      'Edit and resubmit',
    ],
    [
      'changes requested on a correction',
      TUTOR_CHANGES_REQUESTED_ON_CORRECTION,
      'Changes requested by the reviewer',
      [
        'Indica el motivo del simulacro.',
        'Approved 50 min stays in force until it is reviewed.',
        '45 of 60 min',
      ],
      'Edit and resubmit',
    ],
    [
      'approved',
      TUTOR_APPROVED,
      'Approved · 50 min',
      ['by Laura Méndez, November 11, 2026', 'Fracciones equivalentes'],
      'Request a correction',
    ],
    [
      'correction under review',
      TUTOR_CORRECTION_SUBMITTED,
      'Correction under review',
      [
        'Approved 50 min stays in force until it is reviewed.',
        '45 of 60 min',
        'Your reason: Salimos 5 minutos antes por un simulacro.',
      ],
      null,
    ],
    [
      'under review',
      TUTOR_RESUBMITTED,
      'Submitted for review',
      ['58 of 60 min'],
      null,
    ],
    [
      'changes requested, not the submitter',
      NOT_SUBMITTER_CHANGES_REQUESTED,
      'Changes requested by the reviewer',
      [],
      null,
    ],
    [
      'approved, no correction flag',
      { ...TUTOR_APPROVED, can_request_correction: false },
      'Approved · 50 min',
      [],
      null,
    ],
    // #301b E5: submitted on the tutor's behalf (no "Fix closeout"), and
    // approved (a correction can still be requested).
    [
      'submitted on behalf',
      TUTOR_ON_BEHALF_SUBMITTED,
      'Submitted for review',
      [
        'Submitted on your behalf by Laura Méndez, Nov 10, 2026, 10:00 AM',
        'Reason: El tutor dejó el centro antes de enviar el cierre.',
      ],
      null,
    ],
    [
      'approved on behalf',
      TUTOR_ON_BEHALF_APPROVED,
      'Approved · 50 min',
      ['Submitted on your behalf by Laura Méndez'],
      'Request a correction',
    ],
  ])('shows the %s view', async (_, data, heading, lines, action) => {
    await open([data]);

    expect(main().querySelector('h3')?.textContent).toBe(heading);
    for (const line of lines) {
      expect(text()).toContain(line);
    }
    expect(main().querySelector('ol')).toBeNull();
    const buttons = [...main().querySelectorAll('button')].map(
      (b) => b.textContent,
    );
    expect(buttons).toEqual(action ? [action] : []);
    // No attendance request while only the view shows.
    expect(api.calls(ATTENDANCE)).toHaveLength(0);
  });

  it.each([
    [
      NOT_STARTED,
      'Closeout opens when the class starts at Nov 9, 2026, 10:00 AM.',
    ],
    [NOT_CLOSABLE, "This class can't be closed out. State: Cancelled."],
    [NO_CLOSEOUT, 'No closeout recorded'],
  ])('explains a blocked session (%#)', async (data, message) => {
    await open([data]);

    expect(text()).toContain(message);
    expect(main().querySelector('ol')).toBeNull();
  });

  it('shows the not-found page for a 404', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(TUTOR),
      [GET]: () => problem(404, 'not_found'),
    });
    app = await renderApp(PATH, ROUTES);
    await settleUntil(
      () => main().querySelector('h1')?.textContent === 'Page not found',
    );
  });

  it('loads the page lazily', async () => {
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'my-schedule/sessions/:sessionId/closeout');
    const lazy = route?.lazy as
      (() => Promise<{ Component: unknown }>) | undefined;

    expect(route?.handle).toEqual({ role: 'tutor' });
    expect((await lazy!()).Component).toBe(CloseoutPage);
  });
});

function validation(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  });
}

// #322 E1 (#84 E3): the summary step says approved summaries reach the
// class's students and families, in English and Spanish, and in the
// resubmit mode too.
describe('the family visibility notice (#322 E1)', () => {
  const NOTICE =
    "Once approved, this summary is visible to the class's students and their families.";

  it('shows on the summary step only', async () => {
    await open([OPEN]);
    expect(text()).not.toContain(NOTICE);
    await toSummary();
    expect(stepHeading()).toBe('Step 3 of 4: Summary');
    expect(text()).toContain(NOTICE);
    await fillRequired();
    await click(buttonNamed(main(), 'Next'));
    expect(text()).not.toContain(NOTICE);
  });

  it('is translated', async () => {
    applyLanguage('es');
    await open([OPEN]);
    await click(buttonNamed(main(), 'Siguiente'));
    await click(buttonNamed(main(), 'Siguiente'));
    expect(text()).toContain(
      'Una vez aprobado, este resumen lo verán los alumnos de la clase y sus familias.',
    );
  });

  it('shows in the resubmit mode', async () => {
    await open([TUTOR_CHANGES_REQUESTED]);
    await click(buttonNamed(main(), 'Edit and resubmit'));
    await settleUntil(() => main().querySelector('fieldset') !== null);
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 3 of 4: Summary');
    expect(text()).toContain(NOTICE);
  });
});

describe('the edit modes (#305)', () => {
  const RECORD_VERSION = TUTOR_CHANGES_REQUESTED.closeout!.version;

  async function openResubmit(
    closeouts: unknown[] = [TUTOR_CHANGES_REQUESTED, TUTOR_RESUBMITTED],
    attendances: unknown[] = [ATTENDANCE_COMPLETED],
  ): Promise<void> {
    await open(closeouts, attendances);
    await click(buttonNamed(main(), 'Edit and resubmit'));
    await settleUntil(() => main().querySelector('fieldset') !== null);
  }

  async function toReview(): Promise<void> {
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Next'));
    expect(stepHeading()).toBe('Step 4 of 4: Review');
  }

  const noteField = (label: string) => textarea(label);

  it('resubmits with the closeout version, prefilled values and a note', async () => {
    await openResubmit();
    api.set({ [RESUBMIT]: () => jsonResponse(200, TUTOR_RESUBMITTED) });

    // Focus moves to the step heading; attendance is editable (AM1).
    expect(stepHeading()).toBe('Step 1 of 4: Attendance');
    expect(document.activeElement).toBe(main().querySelector('h3'));
    expect(buttonNamed(main(), 'Save attendance')).toBeTruthy();
    expect(buttonNamed(main(), 'Next').disabled).toBe(false);
    await click(buttonNamed(main(), 'Next'));
    expect(minutes().value).toBe('55');
    typeInto(minutes(), '58');
    await click(buttonNamed(main(), 'Next'));
    expect(textarea('Topics').value).toBe(SUMMARY.topics);
    expect(textarea('Homework').value).toBe(SUMMARY.homework);
    await click(buttonNamed(main(), 'Next'));
    expect(text()).toContain('Note for the reviewer (optional)');
    typeText(noteField('Note for the reviewer'), '  Añadí la tarea.  ');
    await settle();
    await click(buttonNamed(main(), 'Resubmit'));
    await settleUntil(() =>
      text().includes('Closeout resubmitted for review.'),
    );

    expect(api.calls(RESUBMIT)[0]?.body).toEqual({
      version: RECORD_VERSION,
      actual_minutes: 58,
      summary: SUMMARY,
      reason: 'Añadí la tarea.',
    });
    const status = main().querySelector('[role="status"]');
    expect(status?.textContent).toBe('Closeout resubmitted for review.');
    expect(document.activeElement).toBe(status);
    expect(main().querySelector('h3')?.textContent).toBe(
      'Submitted for review',
    );
    expect(text()).toContain('58 of 60 min');
  });

  it('leaves the reason out when the note is empty', async () => {
    await openResubmit();
    api.set({ [RESUBMIT]: () => jsonResponse(200, TUTOR_RESUBMITTED) });
    await toReview();
    await click(buttonNamed(main(), 'Resubmit'));
    await settleUntil(() => api.calls(RESUBMIT).length === 1);

    expect(api.calls(RESUBMIT)[0]?.body).toEqual({
      version: RECORD_VERSION,
      actual_minutes: 55,
      summary: SUMMARY,
    });
  });

  it('saves attendance through the PUT in resubmit', async () => {
    await openResubmit(
      [TUTOR_CHANGES_REQUESTED],
      [ATTENDANCE_COMPLETED, ATTENDANCE_COMPLETED],
    );
    api.set({ [PUT]: () => jsonResponse(200, ATTENDANCE_COMPLETED) });
    const bruno = [...main().querySelectorAll('fieldset')].find(
      (f) => f.querySelector('legend')?.textContent === 'Bruno Gómez',
    );
    const absent = [...(bruno?.querySelectorAll('label') ?? [])]
      .find((l) => l.textContent === 'Absent')
      ?.querySelector('input');
    await click(absent!);
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(() => api.calls(PUT).length === 1);

    expect(api.calls(PUT)[0]?.body).toEqual({
      version: ATTENDANCE_COMPLETED.version,
      marks: [
        {
          student_id: ATTENDANCE_COMPLETED.items[1]!.student.id,
          status: 'absent',
        },
      ],
    });
    await settleUntil(() => api.calls(GET).length >= 2);
  });

  it('returns to the view when an attendance save finds it locked (B2)', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED, TUTOR_RESUBMITTED]);
    api.set({ [PUT]: () => problem(409, 'attendance_locked') });
    const bruno = [...main().querySelectorAll('fieldset')].find(
      (f) => f.querySelector('legend')?.textContent === 'Bruno Gómez',
    );
    const absent = [...(bruno?.querySelectorAll('label') ?? [])]
      .find((l) => l.textContent === 'Absent')
      ?.querySelector('input');
    await click(absent!);
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(
      () => main().querySelector('h3')?.textContent === 'Submitted for review',
    );

    expect(main().querySelector('ol')).toBeNull();
  });

  it('shows attendance read-only in a correction and requires the reason', async () => {
    await open(
      [TUTOR_APPROVED, TUTOR_CORRECTION_SUBMITTED],
      [ATTENDANCE_LOCKED],
    );
    await click(buttonNamed(main(), 'Request a correction'));
    await settleUntil(() =>
      text().includes('Attendance is closed. Ask the center to correct it.'),
    );
    api.set({
      [RESUBMIT]: () => jsonResponse(200, TUTOR_CORRECTION_SUBMITTED),
    });

    expect(main().querySelector('fieldset')).toBeNull();
    expect(() => buttonNamed(main(), 'Save attendance')).toThrow();
    expect(buttonNamed(main(), 'Next').disabled).toBe(false);
    await click(buttonNamed(main(), 'Next'));
    expect(minutes().value).toBe(String(APPROVED_RECORD.actual_minutes));
    typeInto(minutes(), '45');
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Next'));

    const reason = noteField('Reason for the correction');
    expect(reason.getAttribute('aria-required')).toBe('true');
    await click(buttonNamed(main(), 'Request correction'));
    expect(api.calls(RESUBMIT)).toHaveLength(0);
    expect(reason.getAttribute('aria-invalid')).toBe('true');
    expect(text()).toContain('Give a reason for the correction.');
    expect(reason.getAttribute('aria-describedby')).toContain(
      `${reason.id}-error`,
    );
    expect(document.activeElement).toBe(reason);

    typeText(reason, '  Salimos antes por un simulacro.  ');
    await settle();
    await click(buttonNamed(main(), 'Request correction'));
    await settleUntil(() => text().includes('Correction submitted.'));

    expect(api.calls(RESUBMIT)[0]?.body).toEqual({
      version: APPROVED_RECORD.version,
      actual_minutes: 45,
      summary: SUMMARY,
      reason: 'Salimos antes por un simulacro.',
    });
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Correction submitted. Approved 50 min stays in force until it is reviewed.',
    );
    expect(main().querySelector('h3')?.textContent).toBe(
      'Correction under review',
    );
  });

  it('cancels without asking when nothing changed, and focuses the view', async () => {
    await openResubmit();
    await click(buttonNamed(main(), 'Cancel'));

    expect(openDialog()).toBeNull();
    const heading = main().querySelector('h3');
    expect(heading?.textContent).toBe('Changes requested by the reviewer');
    expect(document.activeElement).toBe(heading);
  });

  it('asks before cancelling typed changes', async () => {
    await openResubmit();
    await click(buttonNamed(main(), 'Next'));
    typeInto(minutes(), '58');
    await click(buttonNamed(main(), 'Cancel'));

    expect(openDialog()?.textContent).toContain('Leave the closeout?');
    await click(buttonNamed(openDialog()!, 'Stay'));
    expect(minutes().value).toBe('58');
    await click(buttonNamed(main(), 'Cancel'));
    await click(buttonNamed(openDialog()!, 'Leave'));
    expect(main().querySelector('h3')?.textContent).toBe(
      'Changes requested by the reviewer',
    );
  });

  it('leaves an untouched edit mode without asking (prefilled baseline)', async () => {
    await openResubmit();
    await click(buttonNamed(main(), 'Next'));
    await click(buttonNamed(main(), 'Next'));
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to My schedule',
    );
    await click(back!);

    await settleUntil(() => /^\/my-schedule\?/.test(app!.location()));
    expect(openDialog()).toBeNull();
  });

  it('asks before leaving an edit mode with changes', async () => {
    await openResubmit();
    await click(buttonNamed(main(), 'Next'));
    typeInto(minutes(), '58');
    await settle();
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to My schedule',
    );
    await click(back!);

    expect(openDialog()?.textContent).toContain('Leave the closeout?');
    expect(app!.location()).toBe(PATH);
  });

  async function failWith(response: () => Response): Promise<void> {
    api.set({ [RESUBMIT]: response });
    await toReview();
    await click(buttonNamed(main(), 'Resubmit'));
    await settleUntil(() => api.calls(RESUBMIT).length === 1);
    await settle();
  }

  it('offers Reload after a version conflict', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() => problem(409, 'version_conflict'));

    expect(alerts(main()).join(' ')).toContain('Session changed elsewhere');
    const before = api.calls(GET).length;
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => api.calls(GET).length > before);
  });

  it('reloads to the view when it is already under review', async () => {
    await openResubmit();
    await failWith(() => problem(409, 'closeout_under_review'));
    await settleUntil(
      () => main().querySelector('h3')?.textContent === 'Submitted for review',
    );

    expect(alerts(main())).toContain('This closeout is already under review.');
  });

  it.each([
    ['reason_required', 'Give a reason for the correction.'],
    ['reason_too_long', 'At most 1000 characters.'],
  ])('puts a server %s on the note field', async (code, message) => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() =>
      validation(422, {
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'validation_error',
        request_id: 'r',
        errors: [{ path: ['body', 'reason'], code, message: 'x' }],
      }),
    );

    expect(stepHeading()).toBe('Step 4 of 4: Review');
    const note = noteField('Note for the reviewer');
    expect(note.getAttribute('aria-invalid')).toBe('true');
    expect(text()).toContain(message);
    expect(document.activeElement).toBe(note);
  });

  it('shows its own alert when nothing changed', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() => problem(422, 'closeout_unchanged'));

    expect(stepHeading()).toBe('Step 4 of 4: Review');
    expect(alerts(main())).toEqual([
      'Nothing changed. Change the minutes or the summary, or cancel.',
    ]);
  });

  it('goes back to attendance when it is incomplete', async () => {
    await openResubmit(
      [TUTOR_CHANGES_REQUESTED],
      [ATTENDANCE_COMPLETED, { ...ATTENDANCE_UNMARKED, state: 'completed' }],
    );
    await failWith(() => problem(409, 'closeout_attendance_incomplete'));
    await settleUntil(() => stepHeading() === 'Step 1 of 4: Attendance');
    await settleUntil(() => text().includes('Mark these students first:'));

    expect(text()).toContain('Some students have no attendance mark yet.');
  });

  it('maps a summary 422 back onto step 3', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() =>
      validation(422, {
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'validation_error',
        request_id: 'r',
        errors: [
          {
            path: ['body', 'summary', 'topics'],
            code: 'too_long',
            message: 'x',
          },
        ],
      }),
    );
    await settleUntil(() => stepHeading() === 'Step 3 of 4: Summary');

    expect(textarea('Topics').getAttribute('aria-invalid')).toBe('true');
  });

  it('shows the not-found page for a 404', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() => problem(404, 'not_found'));
    await settleUntil(
      () => main().querySelector('h1')?.textContent === 'Page not found',
    );
  });

  it('shows anything else as an error alert', async () => {
    await openResubmit([TUTOR_CHANGES_REQUESTED]);
    await failWith(() => problem(403, 'closeout_tutor_only'));

    expect(stepHeading()).toBe('Step 4 of 4: Review');
    expect(alerts(main()).join(' ')).toContain(
      "Only the class's tutor submits its closeout.",
    );
  });
});

describe('the My schedule dialog (G2)', () => {
  const START = Date.parse('2026-11-09T16:00:00Z');

  async function dialog(
    entry: ScheduleEntry,
    now: number,
    routes: Record<string, () => Response> = {},
  ): Promise<void> {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR), ...routes });
    const element = createElement(EntryDialog, {
      entry,
      context: {
        t: i18n.getFixedT('en', 'common'),
        timeZone: ZONE,
        language: 'en',
      },
      onClose: () => undefined,
      onReturnFocus: () => undefined,
      now: () => now,
    });
    app = await renderApp('/', [{ path: '/', element }]);
    await settle();
  }

  function link(): HTMLAnchorElement | undefined {
    return [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ].find(
      (a) =>
        a.textContent?.includes('closeout') ||
        a.textContent?.includes('Close out'),
    );
  }

  const closeoutGet = (id: string) =>
    `GET /api/v1/class-sessions/${id}/closeout`;
  const dialogText = () =>
    document.querySelector('[role="dialog"]')?.textContent ?? '';

  it('offers Close out session on a started confirmed item', async () => {
    const item = tutorItem({ starts_at: '2026-11-09T16:00:00Z' });
    await dialog({ audience: 'tutor', item }, START + 60_000);

    expect(link()?.textContent).toBe('Close out session');
    expect(link()?.getAttribute('href')).toBe(
      `/my-schedule/sessions/${item.id}/closeout`,
    );
  });

  it('has no link before the start, and View closeout once completed', async () => {
    const item = tutorItem({ starts_at: '2026-11-09T16:00:00Z' });
    await dialog({ audience: 'tutor', item }, START - 60_000);
    expect(link()).toBeUndefined();
    app?.unmount();

    await dialog(
      { audience: 'tutor', item: { ...item, state: 'completed' } },
      START - 60_000,
    );
    expect(link()?.textContent).toBe('View closeout');
  });

  // #305 C1: a tutor's completed item reads its closeout lazily.
  it('shows the badge and Fix closeout when changes are requested', async () => {
    const item = tutorItem({ state: 'completed' });
    await dialog({ audience: 'tutor', item }, START, {
      [closeoutGet(item.id)]: () =>
        jsonResponse(200, {
          ...TUTOR_CHANGES_REQUESTED,
          class_session_id: item.id,
        }),
    });
    await settleUntil(() => link()?.textContent === 'Fix closeout');

    expect(api.calls(closeoutGet(item.id))).toHaveLength(1);
    expect(dialogText()).toContain('Changes requested');
    expect(link()?.getAttribute('href')).toBe(
      `/my-schedule/sessions/${item.id}/closeout`,
    );
    expect(link()?.className).toContain('bg-primary');
  });

  it.each([
    ['submitted', () => jsonResponse(200, SUBMITTED)],
    ['approved', () => jsonResponse(200, TUTOR_APPROVED)],
    ['no closeout', () => jsonResponse(200, NO_CLOSEOUT)],
    ['an error', () => problem(500, 'internal_error')],
  ])('keeps View closeout with %s', async (_, respond) => {
    const item = tutorItem({ state: 'completed' });
    await dialog({ audience: 'tutor', item }, START, {
      [closeoutGet(item.id)]: respond,
    });
    await settleUntil(() => api.calls(closeoutGet(item.id)).length === 1);
    await settle();

    expect(link()?.textContent).toBe('View closeout');
    expect(dialogText()).not.toContain('Changes requested');
    expect(alerts(document.querySelector('[role="dialog"]')!)).toEqual([]);
  });

  it('keeps View closeout while loading', async () => {
    const item = tutorItem({ state: 'completed' });
    // No route: the request stays pending.
    await dialog({ audience: 'tutor', item }, START);

    expect(link()?.textContent).toBe('View closeout');
    expect(dialogText()).not.toContain('Changes requested');
  });

  it('reads no closeout for family or not-completed entries', async () => {
    const confirmed = tutorItem({ starts_at: '2026-11-09T16:00:00Z' });
    await dialog({ audience: 'tutor', item: confirmed }, START + 60_000);
    expect(
      api.requests.filter((r) => r.path.endsWith('/closeout')),
    ).toHaveLength(0);
    app?.unmount();

    const family = familyItem({ state: 'completed' });
    await dialog({ audience: 'family', item: family }, START + 60_000);
    expect(
      api.requests.filter((r) => r.path.endsWith('/closeout')),
    ).toHaveLength(0);
  });
});
