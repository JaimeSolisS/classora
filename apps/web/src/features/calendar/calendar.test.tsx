import classicThemePlugin from '@fullcalendar/react/themes/classic';
import dayGridPlugin from '@fullcalendar/react/daygrid';
import interactionPlugin from '@fullcalendar/react/interaction';
import listPlugin from '@fullcalendar/react/list';
import timeGridPlugin from '@fullcalendar/react/timegrid';
import { createElement, type ReactNode } from 'react';
import type { RouteObject } from 'react-router';
import { routes } from '@/app/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage, i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { meAs, stubApi, TEST_USER, type ApiStub } from '@/test/api-stub';
import { click } from '@/test/dom';
import {
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { CalendarItem } from './api';
import { ANA, CARMEN, item, ROOM, TIJUANA, WEEK_ITEMS, ZONE } from './fixtures';
import { CalendarPage } from './CalendarPage';
import {
  isCompact,
  sessionName,
  sessionTimeText,
  type LabelContext,
} from './labels';
import {
  calendarSearchParams,
  filterQuery,
  NO_FILTERS,
  parseCalendarState,
  rangeInstants,
  shiftDate,
} from './urlState';
import { CALENDAR_PLUGINS, CALENDAR_VIEWS } from './views';

// #57 E5: the URL state, the filter → query mapping, the range → instants
// conversion in a named zone (the DST weeks too, under a host zone that is
// not the center's), the accessible names, the configured views, and the
// page: its route and nav entry, the query it sends, the truncated notice,
// the empty message and the error state. FullCalendar itself is replaced by
// a stub that reports a visible range and renders each event's content (its
// rendering is covered by the stories and the E2E spec).

interface StubCalendarProps {
  timeZone: string;
  initialView: string;
  initialDate: string;
  plugins: unknown[];
  events: { id: string; extendedProps: { item: CalendarItem } }[];
  eventContent: (info: unknown) => ReactNode;
  datesSet: (info: { startStr: string; endStr: string }) => void;
}

const calendarProps = vi.hoisted(() => ({
  last: null as StubCalendarProps | null,
  range: { startStr: '2026-11-09', endStr: '2026-11-16' },
}));

vi.mock('@fullcalendar/react', async () => {
  const { useEffect } = await import('react');
  function FullCalendar(props: StubCalendarProps) {
    calendarProps.last = props;
    const { datesSet } = props;
    useEffect(() => {
      datesSet(calendarProps.range);
    }, [datesSet]);
    return (
      <div data-testid="calendar">
        {props.events.map((event) => (
          <div key={event.id} role="button" tabIndex={0}>
            {props.eventContent({ event, view: { type: props.initialView } })}
          </div>
        ))}
      </div>
    );
  }
  return {
    default: FullCalendar,
    useCalendarController: () => ({
      view: { type: 'timeGridWeek', title: 'Nov 9 – 15, 2026' },
      changeView: vi.fn(),
      gotoDate: vi.fn(),
    }),
  };
});

const SESSIONS = 'GET /api/v1/class-sessions';

/** The app's routes with the calendar loaded eagerly (the lazy import is
 * checked on its own below). */
function eager(list: RouteObject[]): RouteObject[] {
  return list.map((route) => {
    if (route.path === 'schedule' && route.lazy) {
      return {
        path: route.path,
        handle: route.handle as unknown,
        element: createElement(CalendarPage),
      };
    }
    return route.children
      ? { ...route, children: eager(route.children) }
      : route;
  });
}

const ROUTES = eager(routes);

const VIEWER: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['schedule.view', 'student.view'],
};
const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'schedule.manage', 'student.view'],
};

const DEFAULTS = { view: 'week', date: '2026-11-09' as LocalDate } as const;

function context(language: 'es' | 'en', timeZone = ZONE): LabelContext {
  return { t: i18n.getFixedT(language, 'common'), timeZone, language };
}

describe('the URL state', () => {
  it('reads every parameter', () => {
    const tutor = CARMEN.id;
    const params = new URLSearchParams(
      `view=month&date=2026-12-24&tutor=${tutor}&student=${ANA.student_id}&room=${ROOM.id}&kind=group&cancelled=1`,
    );

    expect(parseCalendarState(params, DEFAULTS)).toEqual({
      view: 'month',
      date: '2026-12-24',
      filters: {
        tutor,
        student: ANA.student_id,
        room: ROOM.id,
        kind: 'group',
        cancelled: true,
      },
    });
  });

  it('falls back to the defaults for missing or invalid values', () => {
    const params = new URLSearchParams(
      'view=year&date=2026-02-30&tutor=nope&student=1&room=&kind=workshop&cancelled=yes',
    );

    expect(parseCalendarState(params, DEFAULTS)).toEqual({
      ...DEFAULTS,
      filters: NO_FILTERS,
    });
    expect(parseCalendarState(new URLSearchParams(), DEFAULTS)).toEqual({
      ...DEFAULTS,
      filters: NO_FILTERS,
    });
  });

  it('round-trips through the search params, filters only when set', () => {
    const state = parseCalendarState(
      new URLSearchParams(`view=list&date=2026-11-10&tutor=${CARMEN.id}`),
      DEFAULTS,
    );

    expect(calendarSearchParams(state).toString()).toBe(
      `view=list&date=2026-11-10&tutor=${CARMEN.id}`,
    );
    expect(
      calendarSearchParams({ ...state, filters: NO_FILTERS }).toString(),
    ).toBe('view=list&date=2026-11-10');
  });

  it('maps each filter to its query parameter', () => {
    expect(filterQuery(NO_FILTERS)).toEqual({});
    expect(filterQuery({ ...NO_FILTERS, tutor: CARMEN.id })).toEqual({
      tutor_user_id: CARMEN.id,
    });
    expect(filterQuery({ ...NO_FILTERS, student: ANA.student_id })).toEqual({
      student_id: ANA.student_id,
    });
    expect(filterQuery({ ...NO_FILTERS, room: ROOM.id })).toEqual({
      classroom_id: ROOM.id,
    });
    expect(filterQuery({ ...NO_FILTERS, kind: 'course' })).toEqual({
      kind: 'course',
    });
    expect(filterQuery({ ...NO_FILTERS, cancelled: true })).toEqual({
      include_cancelled: true,
    });
  });

  it('moves by a day, a week or a month', () => {
    const date = '2026-01-31' as LocalDate;
    expect(shiftDate('day', date, 1)).toBe('2026-02-01');
    expect(shiftDate('week', date, -1)).toBe('2026-01-24');
    expect(shiftDate('list', date, 1)).toBe('2026-02-07');
    expect(shiftDate('month', date, 1)).toBe('2026-02-28');
    expect(shiftDate('month', date, -1)).toBe('2025-12-31');
  });
});

describe('the range in the center zone', () => {
  it('runs under a host zone that is not the center zone', () => {
    const host = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(host).not.toBe(TIJUANA);
    expect(host).not.toBe(ZONE);
  });

  it('turns local dates into instants of the zone', () => {
    expect(
      rangeInstants(
        { start: '2026-11-09' as LocalDate, end: '2026-11-16' as LocalDate },
        ZONE,
      ),
    ).toEqual({
      from: '2026-11-09T06:00:00.000Z',
      to: '2026-11-16T06:00:00.000Z',
    });
  });

  it('covers the Tijuana fall-back week (one 25-hour day)', () => {
    const { from, to } = rangeInstants(
      { start: '2026-10-26' as LocalDate, end: '2026-11-02' as LocalDate },
      TIJUANA,
    );

    expect(from).toBe('2026-10-26T07:00:00.000Z');
    expect(to).toBe('2026-11-02T08:00:00.000Z');
    expect((Date.parse(to) - Date.parse(from)) / 3_600_000).toBe(7 * 24 + 1);
  });

  it('covers the Tijuana spring-forward week (one 23-hour day)', () => {
    const { from, to } = rangeInstants(
      { start: '2026-03-02' as LocalDate, end: '2026-03-09' as LocalDate },
      TIJUANA,
    );

    expect(from).toBe('2026-03-02T08:00:00.000Z');
    expect(to).toBe('2026-03-09T07:00:00.000Z');
    expect((Date.parse(to) - Date.parse(from)) / 3_600_000).toBe(7 * 24 - 1);
  });
});

describe('the accessible name', () => {
  const unassigned = item({
    state: 'unassigned',
    tutor: null,
    markers: ['center_closed'],
  });

  it('names subject, students, time, tutor, state, room and markers', () => {
    expect(sessionName(unassigned, context('en'))).toBe(
      'Matemáticas, Ana López, 10:00 AM–11:00 AM, No tutor, Unassigned, Aula 2, Center closed',
    );
    expect(sessionName(unassigned, context('es'))).toBe(
      'Matemáticas, Ana López, 10:00–11:00, Sin tutor, Sin asignar, Aula 2, Centro cerrado',
    );
  });

  it('leaves out a missing room and names the tutor', () => {
    const offered = item({
      state: 'offered',
      classroom: null,
      participants: [],
      markers: ['center_closed', 'tutor_not_qualified'],
    });

    expect(sessionName(offered, context('en'))).toBe(
      'Matemáticas, No students, 10:00 AM–11:00 AM, Carmen Ruiz, Offered, Center closed, Tutor no longer qualified',
    );
  });

  it.each([
    ['unassigned', 'Unassigned', 'Sin asignar'],
    ['offered', 'Offered', 'Ofrecida'],
    ['confirmed', 'Confirmed', 'Confirmada'],
    ['completed', 'Completed', 'Impartida'],
    ['cancelled', 'Cancelled', 'Cancelada'],
    ['skipped_closure', 'Skipped (closure)', 'Omitida (cierre)'],
    ['conflict', 'Conflict', 'En conflicto'],
  ] as const)('names the state %s', (state, en, es) => {
    const session = item({ state, markers: [] });

    expect(sessionName(session, context('en'))).toContain(`, ${en}, `);
    expect(sessionName(session, context('es'))).toContain(`, ${es}, `);
  });

  it('gives the offset on a DST day, where a time can happen twice', () => {
    const first = {
      starts_at: '2026-11-01T08:30:00Z',
      ends_at: '2026-11-01T09:00:00Z',
    };
    const second = {
      starts_at: '2026-11-01T09:30:00Z',
      ends_at: '2026-11-01T10:00:00Z',
    };

    expect(sessionTimeText(first, context('es', TIJUANA))).toBe(
      '01:30 (UTC−07:00)–01:00 (UTC−08:00)',
    );
    expect(sessionTimeText(second, context('es', TIJUANA))).toBe(
      '01:30 (UTC−08:00)–02:00 (UTC−08:00)',
    );
    expect(
      sessionTimeText(
        { starts_at: '2026-11-09T16:00:00Z', ends_at: '2026-11-09T17:00:00Z' },
        context('es', TIJUANA),
      ),
    ).toBe('08:00–09:00');
  });
});

describe('the compact layout of short events', () => {
  const minutes = (n: number) =>
    item({
      starts_at: '2026-11-09T16:00:00Z',
      ends_at: new Date(
        Date.parse('2026-11-09T16:00:00Z') + n * 60_000,
      ).toISOString(),
    });

  it('applies to day and week events under 90 minutes only', () => {
    expect(isCompact('timeGridWeek', minutes(15))).toBe(true);
    expect(isCompact('timeGridDay', minutes(60))).toBe(true);
    expect(isCompact('timeGridWeek', minutes(90))).toBe(false);
    expect(isCompact('dayGridMonth', minutes(15))).toBe(false);
    expect(isCompact('listWeek', minutes(15))).toBe(false);
  });
});

describe('the views', () => {
  it('are exactly the four standard ones, plus the interaction plugin (#58 D1)', () => {
    expect(Object.values(CALENDAR_VIEWS)).toEqual([
      'timeGridDay',
      'timeGridWeek',
      'dayGridMonth',
      'listWeek',
    ]);
    expect(CALENDAR_PLUGINS).toHaveLength(5);
    expect(CALENDAR_PLUGINS).toContain(interactionPlugin);
    expect(CALENDAR_PLUGINS).toEqual(
      expect.arrayContaining([
        dayGridPlugin,
        timeGridPlugin,
        listPlugin,
        classicThemePlugin,
      ]),
    );
  });
});

describe('the Schedule page', () => {
  let app: RenderedApp | undefined;
  let api: ApiStub;

  function stub(user: SessionUserBody, sessions: () => Response) {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(user),
      [SESSIONS]: sessions,
      'GET /api/v1/classrooms': () =>
        jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    });
  }

  function sessionUrls(): URL[] {
    return api.fetch.mock.calls
      .map(([request]) => new URL(request.url))
      .filter((url) => url.pathname === '/api/v1/class-sessions');
  }

  /** Waits for the calendar and its first query. */
  async function calendarLoaded(): Promise<void> {
    await vi.waitFor(
      () => {
        expect(calendarProps.last).not.toBeNull();
        expect(
          app!.container
            .querySelector('[role="region"]')
            ?.getAttribute('aria-busy'),
        ).toBe('false');
      },
      { timeout: 3000 },
    );
    await settle();
  }

  function status(): string {
    return [...app!.container.querySelectorAll('[role="status"]')]
      .map((node) => node.textContent ?? '')
      .join(' | ');
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

  it('is in the nav for schedule.view and loads the visible range in the center zone', async () => {
    stub(VIEWER, () =>
      jsonResponse(200, { items: WEEK_ITEMS, truncated: false }),
    );
    app = await renderApp('/', ROUTES);
    const link = [...app.container.querySelectorAll('nav a')].find(
      (a) => a.textContent === 'Schedule',
    );
    expect(link?.getAttribute('href')).toBe('/schedule');

    await click(link as HTMLElement);
    await calendarLoaded();

    expect(app.location()).toBe('/schedule');
    expect(calendarProps.last?.timeZone).toBe('America/Mexico_City');
    const [url] = sessionUrls();
    expect(url!.searchParams.get('from')).toBe('2026-11-09T06:00:00.000Z');
    expect(url!.searchParams.get('to')).toBe('2026-11-16T06:00:00.000Z');
    expect(status()).toContain('7 sessions in this range.');
    expect(app.container.querySelector('a[href="/schedule/new"]')).toBeNull();
    expect(
      app.container.querySelector('[role="region"]')?.getAttribute('aria-busy'),
    ).toBe('false');
  });

  it('sends the URL filters and offers "Schedule a session" to schedule.manage', async () => {
    stub(MANAGER, () => jsonResponse(200, { items: [], truncated: false }));
    app = await renderApp(
      `/schedule?view=list&date=2026-11-10&tutor=${CARMEN.id}&room=${ROOM.id}&kind=individual&cancelled=1`,
      ROUTES,
    );
    await calendarLoaded();

    const [url] = sessionUrls();
    expect(Object.fromEntries(url!.searchParams)).toMatchObject({
      tutor_user_id: CARMEN.id,
      classroom_id: ROOM.id,
      kind: 'individual',
      include_cancelled: 'true',
    });
    expect(calendarProps.last?.initialView).toBe('listWeek');
    expect(calendarProps.last?.initialDate).toBe('2026-11-10');
    expect(status()).toContain('No sessions in this range.');
    expect(
      app.container.querySelector('a[href="/schedule/new"]')?.textContent,
    ).toBe('Schedule a session');

    await click(
      [...app.container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Clear filters',
      )!,
    );
    await settle();
    expect(app.location()).toBe('/schedule?view=list&date=2026-11-10');
  });

  it('shows the truncated notice', async () => {
    stub(VIEWER, () =>
      jsonResponse(200, { items: WEEK_ITEMS, truncated: true }),
    );
    app = await renderApp('/schedule', ROUTES);
    await calendarLoaded();

    expect(app.container.textContent).toContain(
      'Showing the first 1000 sessions. Narrow the dates or filters.',
    );
  });

  it('shows the error with a retry and keeps the toolbar', async () => {
    let calls = 0;
    stub(VIEWER, () => {
      calls += 1;
      return calls === 1
        ? jsonResponse(422, {
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              { path: ['query', 'to'], code: 'range_too_long', message: 'x' },
            ],
          })
        : jsonResponse(200, { items: [], truncated: false });
    });
    app = await renderApp('/schedule', ROUTES);
    await calendarLoaded();

    const retry = [...app.container.querySelectorAll('button')].find(
      (b) => b.textContent === 'Try again',
    );
    expect(retry).toBeDefined();
    const today = [...app.container.querySelectorAll('button')].find(
      (b) => b.textContent === 'Today',
    );
    expect(today?.disabled).toBe(false);

    await click(retry!);
    await settle();
    expect(status()).toContain('No sessions in this range.');
  });

  it('loads the page lazily', async () => {
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'schedule');
    const lazy = route?.lazy as
      (() => Promise<{ Component: unknown }>) | undefined;

    expect(route?.element).toBeUndefined();
    expect((await lazy!()).Component).toBe(CalendarPage);
  });

  it('denies the page without schedule.view', async () => {
    stub({ ...TEST_USER, permissions: [] }, () =>
      jsonResponse(200, { items: [], truncated: false }),
    );
    app = await renderApp('/schedule', ROUTES);

    expect(sessionUrls()).toHaveLength(0);
    expect(calendarProps.last).toBeNull();
  });
});
