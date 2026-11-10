import { act, createElement, type ReactNode } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import { formatHour } from '@/features/calendar/hours';
import { SETTINGS_QUERY_KEY } from '@/features/settings/api';
import { SETTINGS } from '@/features/settings/fixtures';
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
  TOMAS,
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
  slotMinTime: string;
  slotMaxTime: string;
  scrollTime: string;
  businessHours: unknown;
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
const SETTINGS_PATH = 'GET /api/v1/business-settings';
/** HF-08: the grid mounts once the business settings have settled. */
const SETTINGS_ROUTE = {
  [SETTINGS_PATH]: () => jsonResponse(200, SETTINGS),
};
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
      ...SETTINGS_ROUTE,
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
      ...SETTINGS_ROUTE,
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
      ...SETTINGS_ROUTE,
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
      ...SETTINGS_ROUTE,
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

  it("a shared note's link opens the second child's schedule (#325)", async () => {
    api = stubApi({
      ...SETTINGS_ROUTE,
      'GET /api/v1/auth/me': meAs(user('guardian')),
      [FAMILY_STUDENTS]: () => jsonResponse(200, familyStudents(LUCIA, TOMAS)),
      [scheduleOf(TOMAS.student_id)]: () =>
        jsonResponse(200, {
          student_id: TOMAS.student_id,
          items: [familyItem()],
          truncated: false,
        }),
    });
    const link = NOTIFICATION_LINKS['academics.learning_note_published']!(
      {
        student_id: TOMAS.student_id,
        class_session_id: familyItem().id,
        starts_at: familyItem().starts_at,
      },
      { userId: TUTOR_ID, timeZone: ZONE },
    );
    expect(link).toBe(
      `/my-schedule?for=${TOMAS.student_id}&view=list&date=2026-11-09`,
    );
    app = await renderApp(link, ROUTES);
    await loaded();

    const select = app.container.querySelector('select')!;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Lucía Pérez',
      'Tomás Pérez',
    ]);
    expect(select.value).toBe(TOMAS.student_id);
    expect(api.calls(scheduleOf(TOMAS.student_id)).length).toBeGreaterThan(0);
    expect(api.calls(scheduleOf(LUCIA.student_id))).toHaveLength(0);
    expect(text()).toContain('Tutor: Carmen Ruiz');
  });

  it('an unknown ?for= falls back to the default without a request for it', async () => {
    const foreign = '00000000-0000-4000-8000-0000000000ff';
    api = stubApi({
      ...SETTINGS_ROUTE,
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
      ...SETTINGS_ROUTE,
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

  // HF-08 AC5: Day and Week show the center's opening hours, as /schedule.
  describe('the opening hours (HF-08)', () => {
    const slots = () => {
      const props = calendarProps.last!;
      return [props.slotMinTime, props.slotMaxTime, props.scrollTime];
    };
    const caption = (from: string, to: string) =>
      `Center hours: ${formatHour(from, 'en')}–${formatHour(to, 'en')}`;

    function tutorStub(items = TUTOR_ITEMS) {
      api = stubApi({
        ...SETTINGS_ROUTE,
        'GET /api/v1/auth/me': meAs(user('tutor')),
        [TUTOR_SCHEDULE]: () => jsonResponse(200, { items, truncated: false }),
      });
    }

    it('Week shows the opening hours, shaded where closed', async () => {
      tutorStub();
      app = await renderApp('/my-schedule?view=week&date=2026-11-09', ROUTES);
      await loaded();

      expect(slots()).toEqual(['09:00:00', '20:00:00', '09:00:00']);
      expect(calendarProps.last?.businessHours).toHaveLength(6);
      expect(text()).toContain(caption('09:00', '20:00'));
    });

    it("Day shows that day's hours: Saturday 09:00–14:00", async () => {
      tutorStub([]);
      app = await renderApp('/my-schedule?view=day&date=2026-11-14', ROUTES);
      await loaded();

      expect(calendarProps.last?.initialView).toBe('timeGridDay');
      expect(slots()).toEqual(['09:00:00', '14:00:00', '09:00:00']);
      expect(text()).toContain(caption('09:00', '14:00'));
    });

    it('grows to show a class outside the hours', async () => {
      tutorStub([
        ...TUTOR_ITEMS,
        tutorItem({
          id: '00000000-0000-4000-8000-000000067e09',
          starts_at: '2026-11-11T13:00:00Z', // 07:00 in Mexico City
          ends_at: '2026-11-11T14:00:00Z',
        }),
      ]);
      app = await renderApp('/my-schedule?view=week&date=2026-11-09', ROUTES);
      await loaded();

      expect(slots()).toEqual(['07:00:00', '20:00:00', '07:00:00']);
      expect(text()).toContain('showing classes outside these hours');
      expect(api.calls(TUTOR_SCHEDULE)).toHaveLength(1);
    });

    it('falls back to the whole day, without an alert, when the settings fail or have no hours', async () => {
      tutorStub();
      api.set({ [SETTINGS_PATH]: () => problem(500, 'internal_error') });
      app = await renderApp('/my-schedule?view=week&date=2026-11-09', ROUTES);
      await loaded();

      expect(slots()).toEqual(['00:00:00', '24:00:00', '07:00:00']);
      expect(calendarProps.last?.businessHours).toBe(false);
      expect(text()).not.toContain('Center hours');
      expect(app.container.querySelector('[role="alert"]')).toBeNull();
      expect(text()).toContain('3 classes in this period.');
      app.unmount();

      tutorStub();
      api.set({
        [SETTINGS_PATH]: () =>
          jsonResponse(200, { ...SETTINGS, operating_hours: [] }),
      });
      app = await renderApp('/my-schedule?view=week&date=2026-11-09', ROUTES);
      await loaded();
      expect(slots()).toEqual(['00:00:00', '24:00:00', '07:00:00']);
      expect(text()).not.toContain('Center hours');
    });

    it('follows saved hours with no reload and no new schedule request', async () => {
      tutorStub();
      app = await renderApp('/my-schedule?view=week&date=2026-11-09', ROUTES);
      await loaded();

      act(() => {
        app!.queryClient.setQueryData(SETTINGS_QUERY_KEY, {
          ...SETTINGS,
          operating_hours: [
            { weekday: 1, opens_at: '08:00', closes_at: '18:00' },
          ],
        });
      });
      await settle();
      expect(slots()).toEqual(['08:00:00', '18:00:00', '08:00:00']);
      expect(api.calls(TUTOR_SCHEDULE)).toHaveLength(1);
    });
  });

  it('is forbidden to staff-only users and absent from their nav', async () => {
    api = stubApi({
      ...SETTINGS_ROUTE,
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
