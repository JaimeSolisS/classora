import { createElement, type ReactNode } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import { NOTIFICATION_LINKS } from '@/features/notifications/messages';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { ScheduleEntry } from './api';
import {
  familyItem,
  familyStudents,
  FAMILY_ITEMS,
  LUCIA,
  MATEO,
  SELF,
  TUTOR_ID,
  TUTOR_ITEMS,
  tutorItem,
  ZONE,
} from './fixtures';
import { entryName, type LabelContext } from './labels';
import { MySchedulePage } from './MySchedulePage';
import { defaultView, parseMyScheduleState } from './urlState';
import { resolveWhose, TEACHING, whoseEntries } from './whose';

// #67 G6: the `?for=` resolution, the consent-required rendering (before any
// request and after a 403), the family and tutor accessible names (es, en),
// the default view by width, the F3 notification links, and the page: its
// route and nav entry, the requests it sends and what it shows.
// FullCalendar is replaced by a stub that reports a visible range and
// renders each event's content (as in features/calendar).

interface StubCalendarProps {
  initialView: string;
  events: { id: string; extendedProps: { entry: ScheduleEntry } }[];
  eventContent: (info: unknown) => ReactNode;
  datesSet: (info: { startStr: string; endStr: string }) => void;
}

const calendarProps = vi.hoisted(() => ({
  last: null as StubCalendarProps | null,
}));

vi.mock('@fullcalendar/react', async () => {
  const { useEffect } = await import('react');
  function FullCalendar(props: StubCalendarProps) {
    calendarProps.last = props;
    const { datesSet } = props;
    useEffect(() => {
      datesSet({ startStr: '2026-11-09', endStr: '2026-11-16' });
    }, [datesSet]);
    return (
      <div data-testid="calendar">
        {props.events.map((event) => (
          <div key={event.id} role="button" tabIndex={0}>
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

function eager(list: RouteObject[]): RouteObject[] {
  return list.map((route) => {
    if (route.path === 'my-schedule' && route.lazy) {
      return {
        path: route.path,
        handle: route.handle as unknown,
        element: createElement(MySchedulePage),
      };
    }
    return route.children
      ? { ...route, children: eager(route.children) }
      : route;
  });
}

const ROUTES = eager(routes);

const FAMILY_STUDENTS = 'GET /api/v1/schedule/family-students';
const TUTOR_SCHEDULE = `GET /api/v1/tutors/${TUTOR_ID}/schedule`;
const scheduleOf = (studentId: string) =>
  `GET /api/v1/students/${studentId}/schedule`;

function user(...roles: SessionUserBody['roles']): SessionUserBody {
  return {
    ...TEST_USER,
    id: TUTOR_ID,
    roles,
    permissions: ['student.view'],
  };
}

function context(language: 'es' | 'en'): LabelContext {
  return { t: i18n.getFixedT(language, 'common'), timeZone: ZONE, language };
}

describe('whose schedule (E2)', () => {
  it('a tutor only: My teaching', () => {
    const entries = whoseEntries(true, []);
    expect(entries.map((e) => e.key)).toEqual([TEACHING]);
    expect(resolveWhose(entries, null)?.key).toBe(TEACHING);
  });

  it('a guardian only: their children, the first by default', () => {
    const entries = whoseEntries(false, [LUCIA, MATEO]);
    expect(resolveWhose(entries, null)?.key).toBe(LUCIA.student_id);
    expect(resolveWhose(entries, MATEO.student_id)?.key).toBe(MATEO.student_id);
  });

  it('a tutor and guardian: My teaching first, then the children', () => {
    const entries = whoseEntries(true, [LUCIA]);
    expect(entries.map((e) => e.key)).toEqual([TEACHING, LUCIA.student_id]);
    expect(resolveWhose(entries, null)?.key).toBe(TEACHING);
    expect(resolveWhose(entries, LUCIA.student_id)?.key).toBe(LUCIA.student_id);
  });

  it('a student: their own classes', () => {
    const entries = whoseEntries(false, [SELF]);
    expect(resolveWhose(entries, null)?.key).toBe(SELF.student_id);
  });

  it('an unknown or foreign id falls back to the default', () => {
    const entries = whoseEntries(false, [LUCIA]);
    expect(
      resolveWhose(entries, '00000000-0000-4000-8000-0000000000ff')?.key,
    ).toBe(LUCIA.student_id);
    expect(resolveWhose(whoseEntries(false, []), TEACHING)).toBeNull();
    // "teaching" without the tutor role is not an entry either.
    expect(resolveWhose(entries, TEACHING)?.key).toBe(LUCIA.student_id);
  });
});

describe('the default view (E4)', () => {
  it('is List below 768 px and Week from 768 px', () => {
    expect(defaultView(320)).toBe('list');
    expect(defaultView(767)).toBe('list');
    expect(defaultView(768)).toBe('week');
    expect(defaultView(1280)).toBe('week');
  });

  it('keeps a valid view and date from the URL', () => {
    const state = parseMyScheduleState(
      new URLSearchParams(`for=${LUCIA.student_id}&view=month&date=2026-12-01`),
      { view: 'list', date: '2026-11-09' as never },
    );
    expect(state).toEqual({
      whose: LUCIA.student_id,
      view: 'month',
      date: '2026-12-01',
    });
    expect(
      parseMyScheduleState(new URLSearchParams('view=year&date=nope'), {
        view: 'list',
        date: '2026-11-09' as never,
      }),
    ).toEqual({ whose: null, view: 'list', date: '2026-11-09' });
  });
});

describe('accessible names (E6)', () => {
  const family: ScheduleEntry = { audience: 'family', item: familyItem() };
  const tutor: ScheduleEntry = {
    audience: 'tutor',
    item: tutorItem({ state: 'offered' }),
  };
  const skipped: ScheduleEntry = {
    audience: 'family',
    item: familyItem({ state: 'skipped_closure', classroom: null }),
  };

  it('in English', () => {
    expect(entryName(family, context('en'))).toBe(
      'Matemáticas, November 9, 2026, 10:00 AM–11:00 AM, Confirmed, Tutor: Carmen Ruiz, Room: Aula 2',
    );
    expect(entryName(tutor, context('en'))).toBe(
      'Matemáticas, November 9, 2026, 10:00 AM–11:00 AM, Awaiting your answer, Lucía Pérez, Room: Aula 2',
    );
    expect(entryName(skipped, context('en'))).toContain(
      'Not held: center closed, Tutor: Carmen Ruiz',
    );
  });

  it('in Spanish', () => {
    expect(entryName(family, context('es'))).toBe(
      'Matemáticas, 9 de noviembre de 2026, 10:00–11:00, Confirmada, Tutor(a): Carmen Ruiz, Aula: Aula 2',
    );
    expect(entryName(tutor, context('es'))).toContain(
      'Esperando tu respuesta, Lucía Pérez',
    );
  });

  it('never name markers or other students on a family item', () => {
    const name = entryName(
      { audience: 'family', item: familyItem({ kind: 'group' }) },
      context('en'),
    );
    expect(name).not.toContain('Lucía');
  });
});

describe('notification links (F3)', () => {
  const recipient = { userId: TUTOR_ID, timeZone: ZONE };

  it.each([
    'scheduling.session_cancelled',
    'scheduling.session_tutor_changed',
    'scheduling.tutor_substituted',
  ])('%s opens My schedule on the local date', (type) => {
    // 05:00Z on the 10th is the 9th in Mexico City (UTC-6).
    expect(
      NOTIFICATION_LINKS[type]?.(
        { class_session_id: 'x', starts_at: '2026-11-10T05:00:00Z' },
        recipient,
      ),
    ).toBe('/my-schedule?view=list&date=2026-11-09');
    expect(NOTIFICATION_LINKS[type]?.({}, { userId: TUTOR_ID })).toBe(
      '/my-schedule?view=list',
    );
  });
});

describe('the My schedule page', () => {
  let app: RenderedApp | undefined;
  let api: ApiStub;

  function text(): string {
    return app!.container.textContent ?? '';
  }

  async function loaded(): Promise<void> {
    await vi.waitFor(
      () => {
        expect(
          app!.container
            .querySelector('[aria-label="My schedule calendar"]')
            ?.getAttribute('aria-busy'),
        ).toBe('false');
      },
      { timeout: 3000 },
    );
    await settle();
  }

  beforeEach(() => {
    applyLanguage('en');
    calendarProps.last = null;
  });

  afterEach(() => {
    app?.unmount();
    app = undefined;
    vi.unstubAllGlobals();
  });

  it('is in the nav for a tutor and shows their teaching', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('tutor')),
      [TUTOR_SCHEDULE]: () =>
        jsonResponse(200, { items: TUTOR_ITEMS, truncated: false }),
    });
    app = await renderApp('/', ROUTES);
    const link = [...app.container.querySelectorAll('nav a')].find(
      (a) => a.textContent === 'My schedule',
    );
    expect(link?.getAttribute('href')).toBe('/my-schedule');
    app.unmount();

    app = await renderApp('/my-schedule', ROUTES);
    await loaded();

    expect(api.calls(FAMILY_STUDENTS)).toHaveLength(0);
    const [request] = api.calls(TUTOR_SCHEDULE);
    expect(request).toBeDefined();
    expect(text()).toContain('Whose schedule My teaching');
    expect(text()).toContain('Awaiting your answer');
    expect(text()).toContain('3 classes in this period.');
  });

  it('a guardian chooses a child; the family item shows the tutor', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('guardian')),
      [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(LUCIA, MATEO)),
      [scheduleOf(LUCIA.student_id)]: () =>
        jsonResponse(200, {
          student_id: LUCIA.student_id,
          items: FAMILY_ITEMS,
          truncated: false,
        }),
    });
    app = await renderApp('/my-schedule', ROUTES);
    await loaded();

    const select = app.container.querySelector('select')!;
    expect(select.labels?.[0]?.textContent).toBe('Whose schedule');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Lucía Pérez',
      'Mateo Pérez (not shared)',
    ]);
    expect(text()).toContain('Tutor: Carmen Ruiz');
    expect(text()).toContain('Not held: center closed');
    expect(api.calls(scheduleOf(MATEO.student_id))).toHaveLength(0);
  });

  it('a consent-required child shows the message and sends no request', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('guardian')),
      [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(LUCIA, MATEO)),
    });
    app = await renderApp(`/my-schedule?for=${MATEO.student_id}`, ROUTES);
    await vi.waitFor(() =>
      expect(text()).toContain(
        "This student hasn't shared their schedule with you",
      ),
    );
    await settle();

    expect(text()).toContain(
      "Adult: guardians see academic records only with the student's consent.",
    );
    expect(calendarProps.last).toBeNull();
    expect(
      api.requests.filter((r) => r.path.endsWith('/schedule')),
    ).toHaveLength(0);
  });

  it('a 403 student_consent_required shows the same message', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('guardian')),
      [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(LUCIA)),
      [scheduleOf(LUCIA.student_id)]: () =>
        problem(403, 'student_consent_required'),
    });
    app = await renderApp('/my-schedule', ROUTES);
    await vi.waitFor(() =>
      expect(text()).toContain(
        "This student hasn't shared their schedule with you",
      ),
    );
    // A single entry is plain text, not a control.
    expect(app.container.querySelector('select')).toBeNull();
    expect(text()).toContain('Whose schedule Lucía Pérez');
  });

  it('an unknown ?for= falls back to the default without a request for it', async () => {
    const foreign = '00000000-0000-4000-8000-0000000000ff';
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('student')),
      [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(SELF)),
      [scheduleOf(SELF.student_id)]: () =>
        jsonResponse(200, {
          student_id: SELF.student_id,
          items: [],
          truncated: false,
        }),
    });
    app = await renderApp(`/my-schedule?for=${foreign}`, ROUTES);
    await loaded();

    expect(api.calls(scheduleOf(foreign))).toHaveLength(0);
    expect(api.calls(scheduleOf(SELF.student_id)).length).toBeGreaterThan(0);
    expect(text()).toContain('Whose schedule My classes');
    expect(text()).toContain('No classes in this period.');
  });

  it('shows the truncated notice', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user('tutor')),
      [TUTOR_SCHEDULE]: () =>
        jsonResponse(200, { items: TUTOR_ITEMS, truncated: true }),
    });
    app = await renderApp('/my-schedule', ROUTES);
    await loaded();

    expect(text()).toContain(
      'Showing the first 1000 classes. Choose a shorter period.',
    );
  });

  it('is forbidden to staff-only users and absent from their nav', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs({
        ...TEST_USER,
        roles: ['owner'],
        permissions: ['schedule.view', 'student.view'],
      }),
    });
    app = await renderApp('/my-schedule', ROUTES);
    await settle();

    expect(
      [...app.container.querySelectorAll('nav a')].some(
        (a) => a.textContent === 'My schedule',
      ),
    ).toBe(false);
    expect(app.container.querySelector('main h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.requests.some((r) => r.path.includes('schedule'))).toBe(false);
    expect(calendarProps.last).toBeNull();
  });

  it('loads the page lazily', async () => {
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'my-schedule');
    const lazy = route?.lazy as
      (() => Promise<{ Component: unknown }>) | undefined;

    expect(route?.element).toBeUndefined();
    expect((await lazy!()).Component).toBe(MySchedulePage);
  });
});
