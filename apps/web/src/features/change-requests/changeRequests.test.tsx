import { act, createElement, type ReactNode } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import {
  familyItem,
  familyStudents,
  LUCIA,
  MATEO,
  SELF,
  TUTOR_ID,
  tutorItem,
} from '@/features/my-schedule/fixtures';
import { MyScheduleView } from '@/features/my-schedule/MySchedulePage';
import { NOTIFICATION_LINKS } from '@/features/notifications/messages';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog, pressEscape } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
  settle,
  settleUntil,
} from '@/test/render-app';
import type { ChangeRequestItem } from './api';
import { ChangeRequestsPage } from './ChangeRequestsPage';
import {
  ALL_REQUESTS,
  APPROVED,
  APPROVED_EXCEPTION,
  changeRequest,
  created,
  EARLY,
  FAMILY_ENTRY,
  LATE,
  PENDING_CANCELLATION,
  PENDING_RESCHEDULE,
  POLICY,
  REJECTED,
  SECOND_CHILD,
  slotsOf,
  TUTOR_ENTRY,
  WITHDRAWN,
  ZONE,
} from './fixtures';
import { outcomeText, type ListLabelContext } from './labels';
import {
  RequestChangeAction,
  type RequestChangeActionProps,
} from './RequestChangeAction';
import {
  canRequestChange,
  createBody,
  listSearchParams,
  parseListState,
  slotPage,
  tutorProposal,
} from './rules';

// #62b D4: the A1 visibility rule (each state, kind, time and role), the
// POST bodies (cancel; a family reschedule with the slot's starts_at; a
// tutor reschedule from a Tijuana DST date), the A3 preview in the dialog,
// each A4 message, the list (outcome texts, "as requested", no tutor name
// for families, withdraw), the route and nav item, and the C1 links. The
// host zone is the unit project's foreign zone (vite.config.ts); the
// center zones are Mexico City and Tijuana.

// FullCalendar is replaced by a stub that renders each event's content as a
// button calling `eventClick` (as in features/my-schedule).
interface StubCalendarProps {
  events: { id: string; extendedProps: { entry: ScheduleEntry } }[];
  eventContent: (info: unknown) => ReactNode;
  eventClick: (info: unknown) => void;
  datesSet: (info: { startStr: string; endStr: string }) => void;
}

vi.mock('@fullcalendar/react', async () => {
  const { useEffect } = await import('react');
  function FullCalendar(props: StubCalendarProps) {
    const { datesSet } = props;
    useEffect(() => {
      datesSet({ startStr: '2026-11-09', endStr: '2026-11-16' });
    }, [datesSet]);
    return (
      <div>
        {props.events.map((event) => (
          <div
            key={event.id}
            role="button"
            tabIndex={0}
            data-testid="event"
            data-session-id={event.id}
            onClick={(clicked) =>
              props.eventClick({
                jsEvent: { preventDefault: () => {} },
                event,
                el: clicked.currentTarget,
              })
            }
          >
            {props.eventContent({ event, view: { type: 'listWeek' } })}
          </div>
        ))}
      </div>
    );
  }
  return {
    default: FullCalendar,
    useCalendarController: () => ({
      view: { type: 'listWeek', title: 'Nov 9 – 15, 2026' },
      changeView: vi.fn(),
      gotoDate: vi.fn(),
    }),
  };
});

const PENDING_ROUTE = 'GET /api/v1/change-requests';
const CREATE = 'POST /api/v1/change-requests';
const POLICIES = 'GET /api/v1/scheduling-policies';
const SLOTS = 'GET /api/v1/available-slots';
const FAMILY_STUDENTS = 'GET /api/v1/schedule/family-students';

const GUARDIAN: SessionUserBody = {
  ...TEST_USER,
  id: '00000000-0000-4000-8000-000000247e01',
  roles: ['guardian'],
  permissions: ['student.view'],
};
const TUTOR: SessionUserBody = {
  ...GUARDIAN,
  id: TUTOR_ID,
  roles: ['tutor'],
};

let app: RenderedApp | undefined;
let api: ApiStub;

const page = (items: ChangeRequestItem[] = [], total = items.length) =>
  jsonResponse(200, { items, total, limit: 25, offset: 0 });

const policyOverview = (policy = POLICY) =>
  jsonResponse(200, { current: policy, upcoming: [], history: [] });

function stub(routes: Record<string, Responder | Responder[]> = {}) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(GUARDIAN),
    [PENDING_ROUTE]: () => page(),
    [POLICIES]: () => policyOverview(),
    [SLOTS]: (request) => {
      const url = new URL(request.url);
      return jsonResponse(
        200,
        slotsOf(url.searchParams.get('from')!, url.searchParams.get('to')!),
      );
    },
    [CREATE]: () => jsonResponse(201, created()),
    ...routes,
  });
  return api;
}

function harness(element: ReactNode): RouteObject[] {
  return [
    { path: '/', element },
    { path: '/change-requests', element: createElement('p', null, 'list') },
  ];
}

async function mountAction(
  props: Partial<RequestChangeActionProps> = {},
): Promise<void> {
  app = await renderApp(
    '/',
    harness(
      createElement(RequestChangeAction, {
        entry: FAMILY_ENTRY,
        whose: 'student',
        studentId: LUCIA.student_id,
        timeZone: ZONE,
        now: () => EARLY,
        ...props,
      }),
    ),
  );
}

const text = () => document.body.textContent ?? '';

/** The topmost open dialog (the request dialog opens over the details). */
function dialog(): HTMLElement {
  const found = [
    ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
  ].at(-1);
  if (!found) {
    throw new Error('No dialog');
  }
  return found;
}

function radio(label: string): HTMLInputElement {
  const found = [...dialog().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === label,
  );
  const input = found?.querySelector('input');
  if (!input) {
    throw new Error(`No radio "${label}"`);
  }
  return input;
}

function fieldNamed(label: string): HTMLInputElement | HTMLTextAreaElement {
  const found = [...dialog().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === label,
  );
  const field = found ? document.getElementById(found.htmlFor) : null;
  if (!(
    field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement
  )) {
    throw new Error(`No field "${label}"`);
  }
  return field;
}

function setValue(
  field: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const prototype =
    field instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** Opens the dialog and goes to step 2 for `kind`. */
async function toDetails(
  kind: 'Cancel this class' | 'Move it to another time',
) {
  await click(buttonNamed(document.body, 'Request a change'));
  await click(radio(kind));
  await click(buttonNamed(dialog(), 'Next'));
}

async function send(): Promise<void> {
  await click(buttonNamed(dialog(), 'Send request'));
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('who sees "Request a change" (A1)', () => {
  const future = Date.parse('2026-11-09T16:00:00Z') - 60_000;
  const family = (overrides: Partial<ReturnType<typeof familyItem>>) =>
    ({ audience: 'family', item: familyItem(overrides) }) as ScheduleEntry;
  const tutor = (overrides: Partial<ReturnType<typeof tutorItem>>) =>
    ({ audience: 'tutor', item: tutorItem(overrides) }) as ScheduleEntry;

  it.each([
    ['confirmed', true],
    ['offered', false],
    ['completed', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ] as const)('a tutor item %s: %s', (state, shown) => {
    expect(canRequestChange(tutor({ state }), 'teaching', future)).toBe(shown);
  });

  it.each([
    ['confirmed', true],
    ['completed', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ] as const)('a family item %s: %s', (state, shown) => {
    expect(canRequestChange(family({ state }), 'student', future)).toBe(shown);
  });

  it.each(['group', 'course'] as const)('never a %s session', (kind) => {
    expect(canRequestChange(family({ kind }), 'student', future)).toBe(false);
    expect(canRequestChange(tutor({ kind }), 'teaching', future)).toBe(false);
  });

  it('only before the start (not at it, not after)', () => {
    const start = Date.parse(FAMILY_ENTRY.item.starts_at);
    expect(canRequestChange(FAMILY_ENTRY, 'student', start - 1)).toBe(true);
    expect(canRequestChange(FAMILY_ENTRY, 'student', start)).toBe(false);
    expect(canRequestChange(FAMILY_ENTRY, 'student', start + 1)).toBe(false);
  });

  it('only the caller’s own: a family item in a family schedule, a tutor item in My teaching', () => {
    expect(canRequestChange(FAMILY_ENTRY, 'student', future)).toBe(true);
    expect(canRequestChange(FAMILY_ENTRY, 'teaching', future)).toBe(false);
    expect(canRequestChange(TUTOR_ENTRY, 'teaching', future)).toBe(true);
    expect(canRequestChange(TUTOR_ENTRY, 'student', future)).toBe(false);
  });

  it('asks for the pending request only when eligible; none for an offered item', async () => {
    stub();
    await mountAction({
      entry: tutor({ state: 'offered' }),
      whose: 'teaching',
      studentId: null,
    });
    expect(text()).not.toContain('Request a change');
    expect(api.calls(PENDING_ROUTE)).toHaveLength(0);
  });

  it('a pending request replaces the button with the pending text and a link', async () => {
    stub({ [PENDING_ROUTE]: () => page([PENDING_CANCELLATION]) });
    await mountAction();
    expect(api.calls(PENDING_ROUTE)).toHaveLength(1);
    expect(text()).toContain('Change requested — pending');
    expect(text()).not.toContain('Request a change');
    const link = document.querySelector('a[href^="/change-requests"]');
    expect(link?.textContent).toBe('See My change requests');
  });
});

describe('the request dialog (A2-A4)', () => {
  it('the pending check names the session and the pending status', async () => {
    stub();
    await mountAction();
    const fetched = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .find((url) => url.pathname === '/api/v1/change-requests');
    expect(fetched?.searchParams.get('class_session_id')).toBe(
      FAMILY_ENTRY.item.id,
    );
    expect(fetched?.searchParams.get('status')).toBe('pending');
  });

  it('cancel: the note, an on-time preview, the POST body and the pending state', async () => {
    stub();
    await mountAction();
    await toDetails('Cancel this class');
    expect(text()).toContain('Step 2 of 2');
    expect(text()).toContain(
      'On time — a makeup class is allowed until Dec 9, 2026',
    );
    expect(text()).toContain('Estimate. The center’s decision is final.');
    setValue(
      fieldNamed('Note for the center (optional)'),
      '  Tiene consulta.  ',
    );
    await send();

    const [post] = api.calls(CREATE);
    expect(post?.body).toEqual({
      class_session_id: FAMILY_ENTRY.item.id,
      kind: 'cancellation',
      note: 'Tiene consulta.',
    });
    expect(openDialog()).toBeNull();
    expect(text()).toContain(
      'Request sent. You’ll get a notification when it’s decided.',
    );
    expect(text()).toContain('Change requested — pending');
    expect(document.activeElement?.getAttribute('role')).toBe('status');
  });

  it('a late preview: less than 24 h before the class, no makeup', async () => {
    stub();
    await mountAction({ now: () => LATE });
    await toDetails('Cancel this class');
    expect(text()).toContain(
      'Less than 24 h before the class: a late cancellation, no makeup',
    );
  });

  it('a tutor: excused', async () => {
    stub({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    await mountAction({
      entry: TUTOR_ENTRY,
      whose: 'teaching',
      studentId: null,
      now: () => LATE,
    });
    await toDetails('Cancel this class');
    expect(text()).toContain('Teacher request: excused — a makeup class');
  });

  it('without the policy the preview is left out and sending still works', async () => {
    stub({ [POLICIES]: () => problem(500, 'internal_error') });
    await mountAction();
    await toDetails('Cancel this class');
    expect(text()).not.toContain('Policy outcome');
    expect(alerts(dialog())).toEqual([]);
    await send();
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  it('a cancellation without a note sends no note', () => {
    expect(
      createBody({ sessionId: 's', kind: 'cancellation', note: '   ' }),
    ).toEqual({ class_session_id: 's', kind: 'cancellation' });
    expect(
      createBody({
        sessionId: 's',
        kind: 'cancellation',
        proposedStartsAt: '2026-11-10T18:00:00Z',
        note: '',
      }),
    ).toEqual({ class_session_id: 's', kind: 'cancellation' });
  });

  it('family reschedule: 14 dates from today, the chosen slot’s starts_at is sent', async () => {
    stub();
    await mountAction();
    await toDetails('Move it to another time');
    expect(text()).toContain('Times that overlap this class aren’t shown.');
    const [search] = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/available-slots');
    expect(Object.fromEntries(search!.searchParams)).toEqual({
      student_id: LUCIA.student_id,
      subject_id: FAMILY_ENTRY.item.subject.id,
      duration_minutes: '60',
      from: '2026-11-08',
      to: '2026-11-21',
    });
    expect(text()).toContain(
      'On time — the class is moved, so there is no makeup',
    );

    // Sending without a slot asks for one.
    await send();
    expect(alerts(dialog())).toContain('Choose a new time.');
    expect(api.calls(CREATE)).toHaveLength(0);

    const slot = buttonNamed(dialog(), '12:00 PM–1:00 PM');
    await click(slot);
    expect(slot.getAttribute('aria-pressed')).toBe('true');
    await send();
    expect(api.calls(CREATE)[0]?.body).toEqual({
      class_session_id: FAMILY_ENTRY.item.id,
      kind: 'reschedule',
      proposed_starts_at: '2026-11-10T18:00:00Z',
    });
  });

  it('family reschedule pages by 14 dates up to 60 days ahead', () => {
    const today = '2026-11-08' as never;
    expect(slotPage(today, today)).toEqual({
      from: '2026-11-08',
      to: '2026-11-21',
    });
    expect(slotPage('2027-01-03' as never, today)).toEqual({
      from: '2027-01-03',
      to: '2027-01-07',
    });
  });

  it('a length #59 can’t search: the reschedule option is disabled with the reason', async () => {
    stub();
    await mountAction({
      entry: {
        audience: 'family',
        item: familyItem({ ends_at: '2026-11-09T16:50:00Z' }),
      },
    });
    await click(buttonNamed(document.body, 'Request a change'));
    expect(radio('Move it to another time').disabled).toBe(true);
    expect(text()).toContain(
      'This class can’t be moved from here. Ask the center.',
    );
  });

  it('tutor reschedule: a Tijuana DST date in the center zone, the end shown', async () => {
    stub({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    await mountAction({
      entry: TUTOR_ENTRY,
      whose: 'teaching',
      studentId: null,
      timeZone: 'America/Tijuana',
    });
    await toDetails('Move it to another time');
    expect(text()).toContain('New time (center time, America/Tijuana)');
    // 2027-03-14 02:30 doesn't exist in Tijuana (spring forward): 03:30 PDT.
    setValue(fieldNamed('Date'), '2027-03-14');
    setValue(fieldNamed('Start time'), '02:30');
    expect(text()).toContain('Ends at 4:30 AM');
    await send();
    expect(api.calls(CREATE)[0]?.body).toEqual({
      class_session_id: TUTOR_ENTRY.item.id,
      kind: 'reschedule',
      proposed_starts_at: '2027-03-14T10:30:00.000Z',
    });
  });

  it('tutor proposals: the fall-back hour takes the earlier instant; the past and blanks are refused', () => {
    const now = Date.parse('2026-10-01T00:00:00Z');
    expect(
      tutorProposal('2026-11-01', '01:30', 'America/Tijuana', now),
    ).toEqual({ startsAt: '2026-11-01T08:30:00.000Z' });
    expect(
      tutorProposal('2026-09-30', '10:00', 'America/Tijuana', now),
    ).toEqual({ problem: 'past' });
    expect(tutorProposal('', '10:00', 'America/Tijuana', now)).toEqual({
      problem: 'required',
    });
    expect(
      tutorProposal('2026-11-01', '10:00:30', 'America/Tijuana', now),
    ).toEqual({ problem: 'invalid' });
  });

  it('a tutor time before now is refused before sending', async () => {
    stub({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    await mountAction({
      entry: TUTOR_ENTRY,
      whose: 'teaching',
      studentId: null,
    });
    await toDetails('Move it to another time');
    setValue(fieldNamed('Date'), '2026-11-01');
    setValue(fieldNamed('Start time'), '10:00');
    await send();
    expect(alerts(dialog())).toContain('Choose a time after now.');
    expect(fieldNamed('Start time').getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('a bounds 422 goes on the tutor’s time field', async () => {
    stub({
      'GET /api/v1/auth/me': meAs(TUTOR),
      [CREATE]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Problem',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              {
                path: ['body', 'proposed_starts_at'],
                code: 'crosses_local_date',
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
    await mountAction({
      entry: TUTOR_ENTRY,
      whose: 'teaching',
      studentId: null,
    });
    await toDetails('Move it to another time');
    setValue(fieldNamed('Date'), '2026-11-12');
    setValue(fieldNamed('Start time'), '23:30');
    await send();
    expect(alerts(dialog())).toContain(
      'A session must start and end on the same day.',
    );
    expect(fieldNamed('Start time').getAttribute('aria-invalid')).toBe('true');
  });

  function fieldProblem(code: string): Response {
    return new Response(
      JSON.stringify({
        type: 'about:blank',
        title: 'Problem',
        status: 422,
        code: 'validation_error',
        request_id: `req-${code}`,
        errors: [{ path: ['body', 'proposed_starts_at'], code, message: 'x' }],
      }),
      { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
    );
  }

  function tooMany(seconds: number): Response {
    const response = problem(429, 'rate_limited');
    const headers = new Headers(response.headers);
    headers.set('Retry-After', String(seconds));
    return new Response(response.body, { status: 429, headers });
  }

  it.each([
    [
      'slot_unavailable',
      () => problem(409, 'slot_unavailable'),
      'That time is no longer available. Choose another one.',
    ],
    [
      'change_request_pending',
      () => problem(409, 'change_request_pending'),
      'This class already has a pending change request.',
    ],
    [
      'change_request_session_started',
      () => problem(409, 'change_request_session_started'),
      'This class has already started, so it can no longer be changed by request.',
    ],
    [
      'class_session_not_changeable',
      () => problem(409, 'class_session_not_changeable'),
      'Only a confirmed class can be changed by request.',
    ],
    [
      'change_request_kind_unsupported',
      () => problem(409, 'change_request_kind_unsupported'),
      'Group and course classes can’t be changed by request. Ask the center.',
    ],
    [
      'student_consent_required',
      () => problem(403, 'student_consent_required'),
      i18n.getFixedT('en', 'errors')('student_consent_required'),
    ],
    [
      'rate_limited',
      () => tooMany(150),
      'Too many requests. Try again in 3 minutes.',
    ],
    [
      'proposal_in_past',
      () => fieldProblem('proposal_in_past'),
      'Choose a time in the future.',
    ],
    [
      'proposal_unchanged',
      () => fieldProblem('proposal_unchanged'),
      'Choose a time different from the class’s current time.',
    ],
    [
      'not_found',
      () => problem(404, 'not_found'),
      'This class is no longer available.',
    ],
  ])('%s has its own message', async (_code, respond, message) => {
    stub({ [CREATE]: respond });
    const onScheduleStale = vi.fn();
    await mountAction({ onScheduleStale });
    await toDetails('Move it to another time');
    await click(buttonNamed(dialog(), '12:00 PM–1:00 PM'));
    await send();
    expect(alerts(dialog())).toContain(message);
    // The dialog stays open with the choice; nothing else is shown.
    expect(openDialog()).not.toBeNull();
    expect(onScheduleStale).toHaveBeenCalledTimes(
      _code === 'not_found' ? 1 : 0,
    );
  });

  it('slot_unavailable refetches the slots and clears the choice', async () => {
    stub({ [CREATE]: () => problem(409, 'slot_unavailable') });
    await mountAction();
    await toDetails('Move it to another time');
    const before = api.calls(SLOTS).length;
    await click(buttonNamed(dialog(), '12:00 PM–1:00 PM'));
    await send();
    expect(api.calls(SLOTS).length).toBeGreaterThan(before);
    expect(
      buttonNamed(dialog(), '12:00 PM–1:00 PM').getAttribute('aria-pressed'),
    ).toBe('false');
  });

  it('change_request_pending refetches the session’s pending request', async () => {
    stub({ [CREATE]: () => problem(409, 'change_request_pending') });
    await mountAction();
    await toDetails('Cancel this class');
    const before = api.calls(PENDING_ROUTE).length;
    await send();
    expect(api.calls(PENDING_ROUTE).length).toBeGreaterThan(before);
  });

  it('Escape closes it and the focus returns to the button', async () => {
    stub();
    await mountAction();
    const opener = buttonNamed(document.body, 'Request a change');
    opener.focus();
    await click(opener);
    expect(openDialog()).not.toBeNull();
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('Back returns to step 1 with the choice kept', async () => {
    stub();
    await mountAction();
    await toDetails('Move it to another time');
    await click(buttonNamed(dialog(), 'Back'));
    expect(text()).toContain('Step 1 of 2');
    expect(radio('Move it to another time').checked).toBe(true);
    expect(document.activeElement?.textContent).toBe('Step 1 of 2');
  });
});

describe('from the My schedule details dialog', () => {
  it('a guardian opens a confirmed class and requests a cancellation', async () => {
    stub();
    const schedule = vi.fn(() =>
      Promise.resolve({ entries: [FAMILY_ENTRY], truncated: false }),
    );
    app = await renderApp(
      '/',
      harness(
        createElement(MyScheduleView, {
          userId: GUARDIAN.id,
          isTutor: false,
          hasFamilyRole: true,
          timeZone: ZONE,
          today: '2026-11-09' as never,
          state: { whose: null, view: 'list', date: '2026-11-09' as never },
          onStateChange: () => {},
          now: () => EARLY,
          loaders: {
            fetchFamilyStudents: () =>
              Promise.resolve(familyStudents(LUCIA, MATEO)),
            fetchTutorSchedule: schedule,
            fetchFamilySchedule: schedule,
          },
        }),
      ),
    );
    for (
      let i = 0;
      i < 10 && !document.querySelector('.cl-event-content');
      i += 1
    ) {
      await settle();
    }
    const event = document.querySelector<HTMLElement>('[data-testid="event"]');
    expect(event).not.toBeNull();
    await click(event!);
    expect(openDialog()?.textContent).toContain('Request a change');
    await click(buttonNamed(openDialog()!, 'Request a change'));
    await click(radio('Cancel this class'));
    await click(buttonNamed(dialog(), 'Next'));
    await send();
    expect(api.calls(CREATE)[0]?.body).toMatchObject({
      class_session_id: FAMILY_ENTRY.item.id,
      kind: 'cancellation',
    });
    expect(openDialog()?.textContent).toContain('Change requested — pending');
  });
});

describe('My change requests (B)', () => {
  const context = (language: 'en' | 'es' = 'en'): ListLabelContext => ({
    t: i18n.getFixedT(language, 'common'),
    timeZone: ZONE,
    language,
  });

  it('outcome texts: "as requested" until approved, then the final one', () => {
    expect(outcomeText(PENDING_CANCELLATION, context())).toBe(
      'On time — makeup until Dec 9, 2026 (as requested)',
    );
    expect(outcomeText(PENDING_RESCHEDULE, context())).toBe(
      'Late — no makeup (as requested)',
    );
    expect(outcomeText(REJECTED, context())).toBe(
      'Late — no makeup (as requested)',
    );
    expect(outcomeText(WITHDRAWN, context())).toContain('(as requested)');
    expect(outcomeText(APPROVED_EXCEPTION, context())).toBe(
      'Approved as an exception — makeup until Dec 9, 2026',
    );
    expect(outcomeText(APPROVED, context())).toBe(
      'Approved — On time — makeup until Dec 9, 2026',
    );
    expect(outcomeText(APPROVED_EXCEPTION, context('es'))).toBe(
      'Aprobada como excepción — reposición hasta el 9 dic 2026',
    );
  });

  it('parses and writes the URL filters', () => {
    expect(
      parseListState(
        new URLSearchParams(
          `status=approved&student=${LUCIA.student_id}&page=2`,
        ),
      ),
    ).toEqual({ status: 'approved', studentId: LUCIA.student_id, page: 2 });
    expect(parseListState(new URLSearchParams('status=nope&page=-1'))).toEqual({
      status: 'all',
      studentId: null,
      page: 1,
    });
    expect(
      listSearchParams({ status: 'all', studentId: null, page: 1 }).toString(),
    ).toBe('');
  });

  function eager(list: RouteObject[]): RouteObject[] {
    return list.map((route) => {
      if (route.path === 'change-requests' && route.lazy) {
        return {
          path: route.path,
          handle: route.handle as unknown,
          element: createElement(ChangeRequestsPage),
        };
      }
      return route.children
        ? { ...route, children: eager(route.children) }
        : route;
    });
  }
  const ROUTES = eager(routes);

  async function openList(path = '/change-requests'): Promise<void> {
    app = await renderApp(path, ROUTES);
    await settleUntil(() => app!.container.querySelector('main h1'));
    await settle();
  }

  const main = () => app!.container.querySelector('main')!;

  it('is in the nav for guardians, students and tutors, and lazy', async () => {
    for (const roles of [['guardian'], ['student'], ['tutor']] as const) {
      stub({
        'GET /api/v1/auth/me': meAs({ ...GUARDIAN, roles: [...roles] }),
        [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(SELF)),
      });
      app = await renderApp('/', ROUTES);
      const link = [...app.container.querySelectorAll('nav a')].find(
        (a) => a.textContent === 'My change requests',
      );
      expect(link?.getAttribute('href')).toBe('/change-requests');
      app.unmount();
      app = undefined;
    }
    stub({
      'GET /api/v1/auth/me': meAs({
        ...TEST_USER,
        roles: ['owner'],
        permissions: ['schedule.view'],
      }),
    });
    await openList();
    expect(
      [...app!.container.querySelectorAll('nav a')].some(
        (a) => a.textContent === 'My change requests',
      ),
    ).toBe(false);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'change-requests');
    expect(route?.element).toBeUndefined();
    const lazy = route?.lazy as () => Promise<{ Component: unknown }>;
    expect((await lazy()).Component).toBe(ChangeRequestsPage);
  });

  it('lists every status as text, the outcome, the reason and the note; never a tutor', async () => {
    const leaky = {
      ...PENDING_CANCELLATION,
      tutor: { display_name: 'Carmen Ruiz' },
    } as ChangeRequestItem;
    stub({
      [PENDING_ROUTE]: () => page([leaky, ...ALL_REQUESTS.slice(1)]),
      [FAMILY_STUDENTS]: () =>
        jsonResponse(200, familyStudents(LUCIA, SECOND_CHILD)),
    });
    await openList();

    expect(main().querySelector('h1')?.textContent).toBe('My change requests');
    expect(document.title).toContain('My change requests');
    const cards = [...main().querySelectorAll('li')];
    expect(cards).toHaveLength(6);
    for (const status of ['Pending', 'Approved', 'Rejected', 'Withdrawn']) {
      expect(main().textContent).toContain(`Status: ${status}`);
    }
    expect(cards[0]!.textContent).toContain('Cancellation: Matemáticas');
    expect(cards[0]!.textContent).toContain('Tiene consulta médica.');
    expect(cards[0]!.textContent).toContain('(as requested)');
    expect(cards[1]!.textContent).toContain('Proposed time');
    expect(cards[2]!.textContent).toContain('Hubo una emergencia familiar.');
    expect(cards[4]!.textContent).toContain('No hay aula a esa hora.');
    expect(cards[4]!.textContent).toContain(SECOND_CHILD.display_name);
    expect(main().textContent).not.toContain('Carmen Ruiz');
    // Only pending requests the caller made can be withdrawn.
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'Withdraw',
      ),
    ).toHaveLength(2);
    // The guardian's student filter (two children).
    const student = [...main().querySelectorAll('label')].find(
      (l) => l.textContent === 'Student',
    );
    expect(student).toBeDefined();
  });

  it('sends the URL filters to the API', async () => {
    stub();
    await openList(
      `/change-requests?status=rejected&student=${LUCIA.student_id}&page=2`,
    );
    const url = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .find((u) => u.pathname === '/api/v1/change-requests');
    expect(Object.fromEntries(url!.searchParams)).toEqual({
      status: 'rejected',
      limit: '25',
      offset: '25',
      student_id: LUCIA.student_id,
    });
  });

  it('empty and error states', async () => {
    stub();
    await openList();
    expect(main().textContent).toContain('You have no change requests yet.');
    expect(main().querySelector('a[href="/my-schedule"]')).not.toBeNull();
    app!.unmount();

    stub({ [PENDING_ROUTE]: () => problem(403, 'permission_denied') });
    await openList();
    expect(alerts(main()).length).toBeGreaterThan(0);
  });

  it('withdraws after a confirmation, and a stale withdrawal says so and refetches', async () => {
    const WITHDRAW = `POST /api/v1/change-requests/${PENDING_CANCELLATION.id}/withdraw`;
    stub({
      [PENDING_ROUTE]: [
        () => page([PENDING_CANCELLATION]),
        () =>
          page([
            {
              ...PENDING_CANCELLATION,
              status: 'withdrawn',
              can_withdraw: false,
            },
          ]),
      ],
      [WITHDRAW]: () =>
        jsonResponse(200, { ...PENDING_CANCELLATION, status: 'withdrawn' }),
    });
    await openList();
    await click(buttonNamed(main(), 'Withdraw'));
    const confirm = dialog();
    expect(confirm.textContent).toContain('Withdraw this change request?');
    await click(buttonNamed(confirm, 'Withdraw'));
    await settle();
    expect(api.calls(WITHDRAW)[0]?.body).toEqual({ version: 1 });
    expect(main().textContent).toContain('Status: Withdrawn');
    app!.unmount();

    stub({
      [PENDING_ROUTE]: () => page([PENDING_CANCELLATION]),
      [WITHDRAW]: () => problem(409, 'change_request_not_pending'),
    });
    await openList();
    const before = api.calls(PENDING_ROUTE).length;
    await click(buttonNamed(main(), 'Withdraw'));
    await click(buttonNamed(dialog(), 'Withdraw'));
    await settle();
    expect(alerts(main())).toContain(
      'This request is no longer pending. The list is updated.',
    );
    expect(api.calls(PENDING_ROUTE).length).toBeGreaterThan(before);
  });

  it('a version conflict on withdraw says the request changed', async () => {
    const WITHDRAW = `POST /api/v1/change-requests/${PENDING_CANCELLATION.id}/withdraw`;
    stub({
      [PENDING_ROUTE]: () => page([changeRequest()]),
      [WITHDRAW]: () => problem(409, 'version_conflict'),
    });
    await openList();
    await click(buttonNamed(main(), 'Withdraw'));
    await click(buttonNamed(dialog(), 'Withdraw'));
    await settle();
    expect(alerts(main())).toContain(
      'This request changed since you loaded it. The list is updated.',
    );
  });
});

describe('notification links (C1)', () => {
  it.each([
    ['scheduling.change_request_approved', '/change-requests?status=approved'],
    ['scheduling.change_request_rejected', '/change-requests?status=rejected'],
  ])('%s opens My change requests', (type, path) => {
    expect(
      NOTIFICATION_LINKS[type]?.(
        { change_request_id: 'x', kind: 'cancellation' },
        { userId: GUARDIAN.id, timeZone: ZONE },
      ),
    ).toBe(path);
  });
});
