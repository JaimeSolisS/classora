import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
  settle,
  settleUntil,
} from '@/test/render-app';
import {
  CONFIRMED_REQUEST,
  GUARDIAN_USER,
  PENDING_REQUEST,
  PHYSICS,
  REJECTED_REQUEST,
  STUDENT_ANA,
  STUDENT_LUIS,
  STUDENT_USER,
  studentItem,
  SUBJECTS,
  subjectItem,
  TIJUANA,
  TIJUANA_SLOTS,
  WEEK_SLOTS,
  WITHDRAWN_REQUEST,
} from './fixtures';
import {
  canGoBack,
  canGoForward,
  previousWeek,
  requestRefusal,
  retryMinutes,
  slotTimes,
  staleWithdrawal,
  subjectsByLevel,
  weekRange,
} from './rules';

// #59 F1-F5: the family pages "Request a session" and "My session
// requests": the role requirement and the nav item, the slots in the
// center zone (America/Cancun while the host zone is Pacific/Kiritimati),
// the POST body, each F3 outcome and the withdrawal; the pure rules (the
// week limits, a Tijuana DST week).

const STUDENTS = 'GET /api/v1/students';
const SUBJECTS_ROUTE = 'GET /api/v1/subjects';
const SLOTS = 'GET /api/v1/available-slots';
const CREATE = 'POST /api/v1/session-requests';
const LIST = 'GET /api/v1/session-requests';
const WITHDRAW = `POST /api/v1/session-requests/${PENDING_REQUEST.id}/withdraw`;

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

const studentPage = (choices = [STUDENT_ANA]) =>
  jsonResponse(200, {
    items: choices.map(studentItem),
    total: choices.length,
    limit: 100,
    offset: 0,
  });

const subjectPage = () =>
  jsonResponse(200, {
    items: SUBJECTS.map(subjectItem),
    total: SUBJECTS.length,
    limit: 100,
    offset: 0,
  });

const requestPage = (items = [PENDING_REQUEST]) =>
  jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });

function stub(routes: Record<string, Responder | Responder[]> = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(GUARDIAN_USER),
    [STUDENTS]: () => studentPage(),
    [SUBJECTS_ROUTE]: () => subjectPage(),
    [SLOTS]: () => jsonResponse(200, WEEK_SLOTS),
    [LIST]: () => requestPage(),
    ...routes,
  });
}

/** The lazy routes need a few more turns to load, then their queries. */
async function open(path: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  await settleUntil(() => {
    const page = rendered.container.querySelector('main');
    return (
      page?.querySelector('h1') && !page.querySelector('[aria-busy="true"]')
    );
  });
  await settle();
  return rendered;
}

function retryAfter(seconds: number): Response {
  const response = problem(429, 'rate_limited');
  const headers = new Headers(response.headers);
  headers.set('Retry-After', String(seconds));
  return new Response(response.body, { status: 429, headers });
}

async function chooseSubject(name = PHYSICS.name) {
  const select = main().querySelector('select')!;
  const option = [...select.options].find((o) => o.textContent === name)!;
  act(() => {
    select.value = option.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await settle();
}

const slotButtons = () =>
  [...main().querySelectorAll('section section li button')].map(
    (button) => button as HTMLButtonElement,
  );

function typeNote(value: string) {
  const field = openDialog()!.querySelector('textarea')!;
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

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2031-01-06T13:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules', () => {
  it('limits the week to today .. today + 60 days', () => {
    expect(weekRange('2031-01-06', '2031-01-06')).toEqual({
      from: '2031-01-06',
      to: '2031-01-12',
    });
    expect(weekRange('2031-03-04', '2031-01-06')).toEqual({
      from: '2031-03-04',
      to: '2031-03-07',
    });
    expect(canGoBack('2031-01-06', '2031-01-06')).toBe(false);
    expect(canGoBack('2031-01-13', '2031-01-06')).toBe(true);
    expect(previousWeek('2031-01-10', '2031-01-06')).toBe('2031-01-06');
    expect(canGoForward('2031-02-28', '2031-01-06')).toBe(true);
    expect(canGoForward('2031-03-01', '2031-01-06')).toBe(false);
  });

  it('formats slots in the center zone across a Tijuana DST change', () => {
    const [friday, monday] = TIJUANA_SLOTS.days
      .flatMap((day) => day.slots)
      .map((slot) => slotTimes(slot, TIJUANA, 'es'));
    expect(friday).toEqual({
      date: '7 mar 2031',
      start: '10:00',
      end: '11:00',
    });
    expect(monday).toEqual({
      date: '10 mar 2031',
      start: '10:00',
      end: '11:00',
    });
  });

  it('groups subjects by level and sorts them', () => {
    expect(
      subjectsByLevel(SUBJECTS).map(([level, list]) => [
        level,
        list.map((s) => s.name),
      ]),
    ).toEqual([
      ['Primaria', ['Lectura']],
      ['Secundaria', ['Álgebra', 'Física']],
    ]);
  });

  it('maps refusals, stale withdrawals and the wait of a 429', () => {
    const error = (code: string, status = 409, extra = {}) =>
      new ApiError({ code, status, ...extra });
    expect(requestRefusal(error('slot_unavailable'))).toBe('slot_unavailable');
    expect(requestRefusal(error('student_consent_required', 403))).toBe(
      'student_consent_required',
    );
    expect(requestRefusal(error('version_conflict'))).toBeNull();
    expect(staleWithdrawal(error('version_conflict'))).toBe(
      'session_request_changed',
    );
    expect(staleWithdrawal(error('session_request_not_pending'))).toBe(
      'session_request_not_pending',
    );
    expect(staleWithdrawal(error('not_found', 404))).toBeNull();
    expect(retryMinutes(error('rate_limited', 429, { retryAfter: 61 }))).toBe(
      2,
    );
    expect(retryMinutes(error('rate_limited', 429, { retryAfter: 5 }))).toBe(1);
    expect(retryMinutes(error('slot_unavailable'))).toBeNull();
  });
});

describe('access', () => {
  it('shows the nav item to guardians and students only', async () => {
    stub();
    app = await renderApp('/');
    const nav = () =>
      [...app!.container.querySelectorAll('header nav a')].map(
        (a) => a.textContent,
      );
    expect(nav()).toContain('Session requests');
    app.unmount();

    stub({ 'GET /api/v1/auth/me': meAs(STUDENT_USER) });
    app = await renderApp('/');
    expect(nav()).toContain('Session requests');
    app.unmount();

    const api = stub({ 'GET /api/v1/auth/me': meAs(TEST_USER) });
    app = await open('/requests/new');
    expect(nav()).not.toContain('Session requests');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(SLOTS)).toHaveLength(0);
  });
});

describe('/requests/new', () => {
  it('lists the week in the center zone, one heading per day', async () => {
    const api = stub();
    app = await open('/requests/new');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(document.title).toContain('Request a session');
    // One student: shown as text, no radio group.
    expect(main().textContent).toContain('Student: Ana López');
    expect(main().querySelectorAll('input[name$="-student"]')).toHaveLength(0);
    expect(main().textContent).toContain(
      'Choose a subject to see the available times.',
    );
    await chooseSubject();

    const [request] = api.calls(SLOTS);
    expect(request).toBeDefined();
    const query = new URL(
      api.fetch.mock.calls.find(([r]) => r.url.includes('/available-slots'))![0]
        .url,
    ).searchParams;
    expect(Object.fromEntries(query)).toEqual({
      student_id: STUDENT_ANA.id,
      subject_id: PHYSICS.id,
      duration_minutes: '60',
      from: '2031-01-06',
      to: '2031-01-12',
    });
    expect(main().textContent).toContain(
      "Times are in the center's time zone (America/Cancun).",
    );
    const headings = [...main().querySelectorAll('h3')].map(
      (h) => h.textContent,
    );
    expect(headings[0]).toBe('Monday, Jan 6, 2031');
    expect(headings).toHaveLength(7);
    expect(slotButtons().map((b) => [b.textContent, b.disabled])).toEqual([
      ['10:00 AM–11:00 AM', false],
      ['10:30 AM–11:30 AM · Requested', true],
      ['11:00 AM–12:00 PM', false],
    ]);
    expect(main().textContent).toContain('No available times');
    expect(buttonNamed(main(), 'Previous week').disabled).toBe(true);
  });

  it('a guardian with two children chooses one; a length change asks again', async () => {
    const api = stub({
      [STUDENTS]: () => studentPage([STUDENT_ANA, STUDENT_LUIS]),
    });
    app = await open('/requests/new');

    const radios = [
      ...main().querySelectorAll<HTMLInputElement>('input[name$="-student"]'),
    ];
    expect(radios).toHaveLength(2);
    await chooseSubject();
    expect(api.calls(SLOTS)).toHaveLength(0);
    await click(radios[1]!);
    expect(api.calls(SLOTS)).toHaveLength(1);
    const length90 = [
      ...main().querySelectorAll<HTMLInputElement>('input[name$="-length"]'),
    ].find((input) => input.value === '90')!;
    await click(length90);
    const urls = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/available-slots');
    expect(urls.at(-1)!.searchParams.get('duration_minutes')).toBe('90');
    expect(urls.at(-1)!.searchParams.get('student_id')).toBe(STUDENT_LUIS.id);
  });

  it('sends the request with the trimmed note and shows the success panel', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PENDING_REQUEST),
    });
    app = await open('/requests/new');
    await chooseSubject();

    await click(slotButtons()[0]!);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'This is a request, not a booking. The center will confirm it or let you know.',
    );
    expect(dialog.textContent).toContain('Física (Secundaria)');
    expect(dialog.textContent).toContain('Jan 6, 2031, 10:00 AM–11:00 AM');
    typeNote('  Prefiere por la mañana  ');
    expect(dialog.textContent).toContain('22 of 500 characters');
    await click(buttonNamed(dialog, 'Send request'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: STUDENT_ANA.id,
      subject_id: PHYSICS.id,
      starts_at: '2031-01-06T15:00:00Z',
      duration_minutes: 60,
      note: 'Prefiere por la mañana',
    });
    expect(openDialog()).toBeNull();
    const status = main().querySelector('[role="status"]')!;
    expect(status.textContent).toContain("Request sent. It's pending review.");
    expect(status.querySelector('a')?.getAttribute('href')).toBe('/requests');
    expect(api.calls(SLOTS).length).toBeGreaterThan(1);
  });

  it('leaves an empty note out of the body', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, PENDING_REQUEST) });
    app = await open('/requests/new');
    await chooseSubject();
    await click(slotButtons()[2]!);
    typeNote('   ');
    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: STUDENT_ANA.id,
      subject_id: PHYSICS.id,
      starts_at: '2031-01-06T16:00:00Z',
      duration_minutes: 60,
    });
  });

  it.each([
    [
      'slot_unavailable',
      'That time is no longer available. Choose another one.',
    ],
    [
      'session_request_overlap',
      'This student already has a pending request at that time.',
    ],
    [
      'session_request_limit_reached',
      'This student already has 5 pending requests. Wait for an answer, or withdraw one, before sending another.',
    ],
  ])('a 409 %s keeps the dialog open with its message', async (code, text) => {
    const api = stub({ [CREATE]: () => problem(409, code) });
    app = await open('/requests/new');
    await chooseSubject();
    const before = api.calls(SLOTS).length;

    await click(slotButtons()[0]!);
    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(alerts(openDialog()!).join(' ')).toContain(text);
    if (code === 'slot_unavailable') {
      expect(api.calls(SLOTS).length).toBeGreaterThan(before);
    }
  });

  it('a 429 says how many minutes to wait', async () => {
    stub({ [CREATE]: () => retryAfter(120) });
    app = await open('/requests/new');
    await chooseSubject();

    await click(slotButtons()[0]!);
    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(alerts(openDialog()!).join(' ')).toContain(
      'Too many requests. Try again in 2 minutes.',
    );
  });

  it('a 403 student_consent_required on the slots explains the consent', async () => {
    stub({ [SLOTS]: () => problem(403, 'student_consent_required') });
    app = await open('/requests/new');
    await chooseSubject();

    expect(alerts(main()).join(' ')).toContain(
      'This student is an adult: they must give you consent',
    );
  });

  it('a slot search error offers a retry', async () => {
    const api = stub({
      [SLOTS]: [
        () => problem(500, 'internal_error'),
        () => jsonResponse(200, WEEK_SLOTS),
      ],
    });
    app = await open('/requests/new');
    await chooseSubject();

    await click(buttonNamed(main(), 'Try again'));

    expect(api.calls(SLOTS)).toHaveLength(2);
    expect(slotButtons()).toHaveLength(3);
  });

  it('closing the dialog returns the focus to the slot', async () => {
    stub();
    app = await open('/requests/new');
    await chooseSubject();
    const slot = slotButtons()[0]!;
    act(() => slot.focus());

    await click(slot);
    await click(buttonNamed(openDialog()!, 'Cancel'));

    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(slot);
  });
});

describe('/requests', () => {
  it('lists the requests with their status in text and the note', async () => {
    stub({
      [LIST]: () =>
        requestPage([
          PENDING_REQUEST,
          WITHDRAWN_REQUEST,
          CONFIRMED_REQUEST,
          REJECTED_REQUEST,
        ]),
    });
    app = await open('/requests');

    expect(main().querySelector('h1')?.textContent).toBe('My session requests');
    const cards = [...main().querySelectorAll('ul > li')];
    expect(cards).toHaveLength(4);
    expect(cards[0]!.textContent).toContain('Física (Secundaria)');
    expect(cards[0]!.textContent).toContain('Pending');
    expect(cards[0]!.textContent).toContain('Jan 6, 2031, 10:00 AM–11:00 AM');
    expect(cards[0]!.textContent).toContain('Prefiere por la mañana');
    expect(cards[1]!.textContent).toContain('Withdrawn');
    expect(cards[1]!.textContent).toContain('45 min');
    expect(cards[2]!.textContent).toContain('Confirmed');
    expect(cards[2]!.textContent).toContain('Luis López');
    expect(cards[3]!.textContent).toContain('Rejected');
    // Only the requester's pending request can be withdrawn.
    expect(
      cards.map((card) => card.querySelector('button')?.textContent ?? null),
    ).toEqual(['Withdraw', null, null, null]);
  });

  it('has an empty state that links to the request page', async () => {
    stub({ [LIST]: () => requestPage([]) });
    app = await open('/requests');

    expect(main().textContent).toContain(
      "You haven't requested any session yet.",
    );
    const links = [...main().querySelectorAll('a')].map((a) =>
      a.getAttribute('href'),
    );
    expect(links).toContain('/requests/new');
  });

  it('withdraws after confirmation with the version and refreshes', async () => {
    const api = stub({
      [LIST]: [
        () => requestPage([PENDING_REQUEST]),
        () =>
          requestPage([
            {
              ...PENDING_REQUEST,
              status: 'withdrawn',
              can_withdraw: false,
              version: 2,
            },
          ]),
      ],
      [WITHDRAW]: () =>
        jsonResponse(200, { ...PENDING_REQUEST, status: 'withdrawn' }),
    });
    app = await open('/requests');

    await click(buttonNamed(main(), 'Withdraw'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Withdraw this request?');
    await click(buttonNamed(dialog, 'Withdraw'));

    expect(api.calls(WITHDRAW)[0]!.body).toEqual({ version: 1 });
    expect(openDialog()).toBeNull();
    expect(api.calls(LIST)).toHaveLength(2);
    expect(main().textContent).toContain('Withdrawn');
    expect(main().querySelector('[role="status"]')?.textContent).toContain(
      'Withdrawn: Física',
    );
    // The withdrawn card takes the focus (its button is gone).
    expect(document.activeElement?.tagName).toBe('LI');
  });

  it.each([
    [
      'session_request_not_pending',
      'This request is no longer pending. The list is updated.',
    ],
    [
      'version_conflict',
      'This request changed since you loaded it. The list is updated.',
    ],
  ])('a 409 %s shows its message and refreshes', async (code, text) => {
    const api = stub({ [WITHDRAW]: () => problem(409, code) });
    app = await open('/requests');

    await click(buttonNamed(main(), 'Withdraw'));
    await click(buttonNamed(openDialog()!, 'Withdraw'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(alerts(main()).join(' ')).toContain(text);
  });
});
