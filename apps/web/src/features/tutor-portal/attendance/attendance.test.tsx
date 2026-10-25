import { createElement } from 'react';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import { click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { AttendancePage } from './AttendancePage';
import {
  ANA,
  BRUNO,
  CARLA,
  CORRECTED_ROW,
  LOCKED,
  NOT_RECORDABLE,
  NOT_STARTED,
  PARTIAL,
  SESSION_ID,
  UNMARKED,
  ZONE,
} from './fixtures';
import {
  changedMarks,
  initialChoices,
  showsFamilyAttendance,
  tutorAttendanceLink,
} from './rules';

// #76 H7: the tutor's attendance page (each block code, only new or changed
// marks sent, a corrected row read-only, the version-conflict reload, the
// 404, accessible names in es and en) and the My schedule dialog's
// attendance link and family row by state and time (a frozen clock).

const PATH = `/my-schedule/sessions/${SESSION_ID}/attendance`;
const GET = `GET /api/v1/class-sessions/${SESSION_ID}/attendance`;
const PUT = `PUT /api/v1/class-sessions/${SESSION_ID}/attendance`;

function eager(list: RouteObject[]): RouteObject[] {
  return list.map((route) => {
    if (route.path === 'my-schedule/sessions/:sessionId/attendance') {
      return {
        path: route.path,
        handle: route.handle as unknown,
        element: createElement(AttendancePage),
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

function fieldsets(): HTMLFieldSetElement[] {
  return [...main().querySelectorAll('fieldset')];
}

function radio(legend: string, label: string): HTMLInputElement {
  const set = fieldsets().find(
    (f) => f.querySelector('legend')?.textContent === legend,
  );
  const found = [...(set?.querySelectorAll('label') ?? [])].find(
    (l) => l.textContent === label,
  );
  const input = found?.querySelector('input');
  if (!(input instanceof HTMLInputElement)) {
    throw new Error(`No radio ${label} for ${legend}`);
  }
  return input;
}

async function open(...responses: unknown[]): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(TUTOR),
    [GET]: responses.map((body) => () => jsonResponse(200, body)),
  });
  app = await renderApp(PATH, ROUTES);
  await settleUntil(() => text().includes('Matemáticas'));
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
  it('sends only new or changed marks, never a corrected row', () => {
    const choices = {
      ...initialChoices(CORRECTED_ROW),
      [ANA]: 'present' as const,
      [BRUNO]: 'present' as const,
      [CARLA]: 'absent' as const,
    };
    expect(changedMarks(CORRECTED_ROW, choices)).toEqual([
      { student_id: CARLA, status: 'absent' },
    ]);
    expect(changedMarks(UNMARKED, initialChoices(UNMARKED))).toEqual([]);
  });

  const START = Date.parse('2026-11-09T16:00:00Z');
  const item = (state: string) => ({
    state,
    starts_at: '2026-11-09T16:00:00Z',
  });

  it.each([
    ['confirmed', START - 1, null],
    ['confirmed', START, 'take'],
    ['confirmed', START + 7 * 86_400_000, 'take'],
    ['completed', START - 1, 'view'],
    ['completed', START + 1, 'view'],
    ['offered', START + 1, null],
    ['cancelled', START + 1, null],
    ['skipped_closure', START + 1, null],
  ])('a tutor %s item at %s links to %s', (state, now, expected) => {
    expect(tutorAttendanceLink(item(state), now)).toBe(expected);
  });

  it.each([
    ['confirmed', START - 1, false],
    ['confirmed', START, true],
    ['completed', START + 1, true],
    ['cancelled', START + 1, false],
    ['skipped_closure', START + 1, false],
  ])('a family %s item at %s shows attendance: %s', (state, now, expected) => {
    expect(showsFamilyAttendance(item(state), now)).toBe(expected);
  });
});

describe('the attendance page (G1)', () => {
  it('names each radio group by the student, in English and Spanish', async () => {
    await open(UNMARKED);

    expect(main().querySelector('h1')?.textContent).toBe('Attendance');
    expect(text()).toContain('Matemáticas (Secundaria)');
    expect(text()).toContain('November 9, 2026, 10:00 AM–11:00 AM');
    expect(text()).toContain('State: Confirmed');
    expect(
      fieldsets().map((f) => f.querySelector('legend')?.textContent),
    ).toEqual(['Ana Díaz', 'Bruno Gómez', 'Carla Ruiz']);
    expect(radio('Ana Díaz', 'Late').name).toBe(
      radio('Ana Díaz', 'Absent').name,
    );
    expect(radio('Ana Díaz', 'Late').name).not.toBe(
      radio('Bruno Gómez', 'Late').name,
    );
    expect(text()).toContain('Not recorded');

    await i18n.changeLanguage('es');
    await settle();
    expect(radio('Carla Ruiz', 'Retardo').type).toBe('radio');
    expect(radio('Carla Ruiz', 'Presente').type).toBe('radio');
    expect(buttonNamed(main(), 'Guardar asistencia')).toBeTruthy();
  });

  it('sends only the new or changed marks with the version, then reloads', async () => {
    await open(PARTIAL, CORRECTED_ROW);
    api.set({ [PUT]: () => jsonResponse(200, PARTIAL) });

    await click(radio('Carla Ruiz', 'Absent'));
    await click(radio('Ana Díaz', 'Present'));
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(() => text().includes('Attendance saved.'));

    const [request] = api.calls(PUT);
    expect(request?.body).toEqual({
      version: 3,
      marks: [{ student_id: CARLA, status: 'absent' }],
    });
    expect(api.calls(GET)).toHaveLength(2);
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Attendance saved.',
    );
  });

  it('does not send anything when nothing changed', async () => {
    await open(PARTIAL);

    await click(buttonNamed(main(), 'Save attendance'));

    expect(api.calls(PUT)).toHaveLength(0);
    expect(text()).toContain('Nothing changed');
  });

  it('shows a corrected row read-only with the reason and history', async () => {
    await open(CORRECTED_ROW);

    const legends = fieldsets().map(
      (f) => f.querySelector('legend')?.textContent,
    );
    expect(legends).toEqual(['Ana Díaz', 'Carla Ruiz']);
    expect(text()).toContain('Bruno Gómez');
    expect(text()).toContain('Corrected by the center');
    expect(text()).toContain('Reason: Recepción confirmó que no llegó');
    expect(text()).toContain('Correction history (1)');
    expect(text()).toContain('Late → Absent');
  });

  it.each([
    [
      NOT_STARTED,
      'Attendance opens when the class starts at Nov 9, 2026, 10:00 AM.',
    ],
    [LOCKED, 'Attendance is closed. Ask the center to correct it.'],
    [
      NOT_RECORDABLE,
      "Attendance can't be taken for this class. State: Cancelled.",
    ],
  ])(
    'explains a blocked session instead of the form (%#)',
    async (data, message) => {
      await open(data);

      expect(text()).toContain(message);
      expect(fieldsets()).toHaveLength(0);
      expect(
        [...main().querySelectorAll('button')].some(
          (b) => b.textContent === 'Save attendance',
        ),
      ).toBe(false);
    },
  );

  it('offers Reload after a version conflict', async () => {
    await open(PARTIAL, PARTIAL);
    api.set({ [PUT]: () => problem(409, 'version_conflict') });

    await click(radio('Carla Ruiz', 'Late'));
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(() => text().includes('Attendance changed elsewhere'));

    expect(main().querySelector('[role="alert"]')?.textContent).toContain(
      'Attendance changed elsewhere',
    );
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => api.calls(GET).length === 2);
  });

  it('shows another refusal as an error', async () => {
    await open(PARTIAL);
    api.set({ [PUT]: () => problem(409, 'attendance_corrected') });

    await click(radio('Carla Ruiz', 'Late'));
    await click(buttonNamed(main(), 'Save attendance'));
    await settleUntil(() => text().includes('The center corrected'));
  });

  it('shows the not-found page for a 404', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(TUTOR),
      [GET]: () => problem(404, 'not_found'),
    });
    app = await renderApp(PATH, ROUTES);
    await settleUntil(
      () => main().querySelector('h1')?.textContent !== 'Attendance',
    );

    expect(main().querySelector('h1')?.textContent).toBe('Page not found');
  });

  it('loads the page lazily', async () => {
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'my-schedule/sessions/:sessionId/attendance');
    const lazy = route?.lazy as
      (() => Promise<{ Component: unknown }>) | undefined;

    expect(route?.handle).toEqual({ role: 'tutor' });
    expect((await lazy!()).Component).toBe(AttendancePage);
  });
});

describe('the My schedule dialog (G2)', () => {
  const START = Date.parse('2026-11-09T16:00:00Z');

  async function dialog(entry: ScheduleEntry, now: number): Promise<string> {
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
    return document.querySelector('[role="dialog"]')?.textContent ?? '';
  }

  function link(): HTMLAnchorElement | undefined {
    return [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ].find((a) => a.textContent?.includes('attendance'));
  }

  it('links a started confirmed tutor item to Take attendance', async () => {
    const item = tutorItem({ starts_at: '2026-11-09T16:00:00Z' });
    await dialog({ audience: 'tutor', item }, START + 60_000);

    expect(link()?.textContent).toBe('Take attendance');
    expect(link()?.getAttribute('href')).toBe(
      `/my-schedule/sessions/${item.id}/attendance`,
    );
  });

  it('has no link before the start, and View attendance once completed', async () => {
    const item = tutorItem({ starts_at: '2026-11-09T16:00:00Z' });
    await dialog({ audience: 'tutor', item }, START - 60_000);
    expect(link()).toBeUndefined();
    app?.unmount();

    await dialog(
      { audience: 'tutor', item: { ...item, state: 'completed' } },
      START - 60_000,
    );
    expect(link()?.textContent).toBe('View attendance');
  });

  it('shows a family the student status once started, never before', async () => {
    const item = familyItem({
      starts_at: '2026-11-09T16:00:00Z',
      attendance: 'late',
    });
    expect(await dialog({ audience: 'family', item }, START + 1)).toContain(
      'AttendanceLate',
    );
    expect(link()).toBeUndefined();
    app?.unmount();

    expect(await dialog({ audience: 'family', item }, START - 1)).not.toContain(
      'Attendance',
    );
    app?.unmount();

    expect(
      await dialog(
        { audience: 'family', item: { ...item, attendance: null } },
        START + 1,
      ),
    ).toContain('AttendanceNot recorded');
  });
});
