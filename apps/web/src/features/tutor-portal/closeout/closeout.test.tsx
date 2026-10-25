import { act, createElement } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import { click, typeInto } from '@/test/dom';
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
  ATTENDANCE_ABSENT,
  ATTENDANCE_MARKED,
  ATTENDANCE_UNMARKED,
  NO_CLOSEOUT,
  NOT_CLOSABLE,
  NOT_STARTED,
  OPEN,
  SESSION_ID,
  SUBMITTED,
  ZONE,
} from './fixtures';
import {
  minutesError,
  serverSummaryErrors,
  summaryBody,
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

  it('renders a submitted closeout read-only', async () => {
    await open([SUBMITTED]);

    expect(text()).toContain('Submitted for review');
    expect(text()).toContain('55 of 60 min');
    expect(text()).toContain('Fracciones equivalentes');
    expect(main().querySelector('ol')).toBeNull();
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

describe('the My schedule dialog (G2)', () => {
  const START = Date.parse('2026-11-09T16:00:00Z');

  async function dialog(entry: ScheduleEntry, now: number): Promise<void> {
    stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
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
});
