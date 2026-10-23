import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import { localDateTimeToInstant } from '@/lib/time';
import { alerts, click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import { settle } from '@/test/render-app';
import type {
  CalendarItem,
  CalendarLoaders,
  CalendarPage,
  ClassSessionUpdate,
  ClassSessionUpdated,
  SessionState,
} from '../api';
import {
  calendarState,
  page,
  ROOM_OPTIONS,
  STUDENT_OPTIONS,
  TIJUANA,
  TUTOR_OPTIONS,
  WEEK,
  ZONE,
} from '../fixtures';
import { ScheduleCalendar } from '../ScheduleCalendar';
import {
  conflictError,
  FIXED,
  forbiddenError,
  HARD_CONFLICTS,
  invalidError,
  MOVABLE,
  notEditableError,
  notFoundError,
  OCCURRENCE,
  OVERRIDDEN_ITEM,
  serverError,
  SOFT_CONFLICTS,
  staleError,
  updated,
} from './fixtures';
import {
  droppedSlot,
  EDITABLE_STATES,
  isEditable,
  moveBody,
  overrideBody,
  refusalKind,
  rescheduleSlot,
  rescheduleValues,
} from './rules';

// #58 G7: the editability rule, the drop → body mapping (named zones, the
// Tijuana DST days, under a host zone that is neither: Asia/Tokyo), the
// outcomes of a move (optimistic pending, revert on every refusal, the cache
// update and the refetch on 200, the pending guard), the "Move not saved"
// dialog and its override resend, the Reschedule dialog, and the strings.
// FullCalendar is a stub that renders each event's content and hands its
// props to the test, which calls `eventDrop` / `eventResize` itself.

interface StubEvent {
  id: string;
  start: string;
  end: string;
  editable?: boolean;
  extendedProps: { item: CalendarItem; pending: boolean; movable: boolean };
}

interface StubProps {
  events: StubEvent[];
  eventStartEditable: boolean;
  eventDurationEditable: boolean;
  eventResizableFromStart: boolean;
  snapDuration: string;
  views: Record<string, Record<string, unknown>>;
  eventContent: (info: unknown) => ReactNode;
  eventClick: (info: unknown) => void;
  eventDrop: (info: unknown) => void;
  eventResize: (info: unknown) => void;
  datesSet: (info: { startStr: string; endStr: string }) => void;
}

const stub = vi.hoisted(() => ({ last: null as StubProps | null }));

vi.mock('@fullcalendar/react', async () => {
  const { useEffect } = await import('react');
  function FullCalendar(props: StubProps) {
    stub.last = props;
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
            data-session-id={event.id}
            onClick={(jsEvent) =>
              props.eventClick({
                event,
                el: jsEvent.currentTarget,
                jsEvent,
              })
            }
          >
            {props.eventContent({ event, view: { type: 'timeGridWeek' } })}
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

const HOST_ZONE = 'Asia/Tokyo';
let previousZone: string | undefined;

beforeAll(() => {
  previousZone = process.env.TZ;
  process.env.TZ = HOST_ZONE;
});

afterAll(() => {
  process.env.TZ = previousZone;
});

const at = (date: string, time: string, zone = ZONE) =>
  localDateTimeToInstant(date, time, zone);

// --- D3: editability -----------------------------------------------------------

describe('which events can move (D3)', () => {
  const now = Date.parse('2026-11-09T12:00:00Z');
  const future = '2026-11-09T16:00:00Z';
  const past = '2026-11-09T11:00:00Z';
  const states: SessionState[] = [
    'unassigned',
    'offered',
    'confirmed',
    'completed',
    'cancelled',
    'skipped_closure',
    'conflict',
  ];

  it.each(states)('%s', (state) => {
    const movable = ['unassigned', 'offered', 'confirmed', 'conflict'];
    expect(isEditable({ state, starts_at: future }, now, true)).toBe(
      movable.includes(state),
    );
    // Started, or without schedule.manage: never.
    expect(isEditable({ state, starts_at: past }, now, true)).toBe(false);
    expect(
      isEditable({ state, starts_at: '2026-11-09T12:00:00Z' }, now, true),
    ).toBe(false);
    expect(isEditable({ state, starts_at: future }, now, false)).toBe(false);
  });

  it('is the #54 set', () => {
    expect([...EDITABLE_STATES].sort()).toEqual([
      'confirmed',
      'conflict',
      'offered',
      'unassigned',
    ]);
  });
});

// --- D5: the body ----------------------------------------------------------------

describe('the PATCH body of a drop (D5)', () => {
  it('runs under a host zone that is not the center zone', () => {
    // Tokyo is UTC+9 all year.
    expect(new Date('2026-11-09T00:00:00Z').getTimezoneOffset()).toBe(-540);
  });

  it('a week-view move keeps the duration and sends the version, no room', () => {
    const slot = droppedSlot(
      new Date(at('2026-11-10', '11:00')),
      new Date(at('2026-11-10', '12:00')),
    )!;

    expect(moveBody(MOVABLE, slot)).toEqual({
      version: 3,
      starts_at: '2026-11-10T17:00:00.000Z',
      ends_at: '2026-11-10T18:00:00.000Z',
    });
  });

  it('a resize changes the end only', () => {
    const body = moveBody(
      MOVABLE,
      droppedSlot(
        new Date(MOVABLE.starts_at),
        new Date(at('2026-11-09', '11:30')),
      )!,
    );

    expect(body).toEqual({
      version: 3,
      starts_at: '2026-11-09T16:00:00.000Z',
      ends_at: '2026-11-09T17:30:00.000Z',
    });
    expect(body).not.toHaveProperty('classroom_id');
  });

  it('a month-view move keeps the local times on another date', () => {
    // FullCalendar moves the date in the center zone; the times stay.
    const body = moveBody(
      MOVABLE,
      droppedSlot(
        new Date(at('2026-11-20', '10:00')),
        new Date(at('2026-11-20', '11:00')),
      )!,
    )!;

    expect(rescheduleValues(body, ZONE)).toEqual({
      date: '2026-11-20',
      start: '10:00',
      end: '11:00',
    });
  });

  it('a drop onto the same place sends nothing', () => {
    expect(
      moveBody(
        MOVABLE,
        droppedSlot(new Date(MOVABLE.starts_at), new Date(MOVABLE.ends_at))!,
      ),
    ).toBeNull();
    expect(droppedSlot(null, new Date())).toBeNull();
  });

  it('each 01:30 of the Tijuana fall-back day sends its own instant', () => {
    // 2026-11-01 01:30 happens at UTC−07:00 (08:30Z) and at UTC−08:00 (09:30Z).
    const first = moveBody(
      MOVABLE,
      droppedSlot(
        new Date('2026-11-01T01:30:00-07:00'),
        new Date('2026-11-01T02:00:00-08:00'),
      )!,
    )!;
    const second = moveBody(
      MOVABLE,
      droppedSlot(
        new Date('2026-11-01T01:30:00-08:00'),
        new Date('2026-11-01T02:00:00-08:00'),
      )!,
    )!;

    expect(first.starts_at).toBe('2026-11-01T08:30:00.000Z');
    expect(second.starts_at).toBe('2026-11-01T09:30:00.000Z');
    expect(rescheduleValues(first, TIJUANA).start).toBe('01:30');
    expect(rescheduleValues(second, TIJUANA).start).toBe('01:30');
  });

  it('the spring-forward day and the Reschedule dialog use the center zone', () => {
    // 2026-03-08: 02:00-03:00 doesn't exist in Tijuana.
    expect(
      rescheduleSlot(
        { date: '2026-03-08', start: '01:30', end: '03:30' },
        TIJUANA,
      ),
    ).toEqual({
      starts_at: '2026-03-08T09:30:00.000Z',
      ends_at: '2026-03-08T10:30:00.000Z',
    });
    expect(
      rescheduleSlot(
        { date: '2026-11-10', start: '11:00', end: '12:00' },
        ZONE,
      ),
    ).toEqual({
      starts_at: '2026-11-10T17:00:00.000Z',
      ends_at: '2026-11-10T18:00:00.000Z',
    });
    expect(rescheduleValues(MOVABLE, ZONE)).toEqual({
      date: '2026-11-09',
      start: '10:00',
      end: '11:00',
    });
  });

  it('the override resend keeps the body and version (F3)', () => {
    const body: ClassSessionUpdate = {
      version: 3,
      starts_at: '2026-11-15T16:00:00Z',
      ends_at: '2026-11-15T17:00:00Z',
    };
    const conflicts = [
      ...SOFT_CONFLICTS,
      { code: 'notice_window', severity: 'soft' as const, details: {} },
    ];

    expect(overrideBody(body, conflicts, '  Lo pidió la familia ')).toEqual({
      ...body,
      override: {
        codes: ['outside_operating_hours', 'notice_window'],
        reason: 'Lo pidió la familia',
      },
    });
  });

  it('classifies every refusal', () => {
    expect(refusalKind(conflictError())).toBe('conflict');
    expect(refusalKind(staleError())).toBe('stale');
    expect(refusalKind(notEditableError())).toBe('gone');
    expect(refusalKind(notFoundError())).toBe('gone');
    expect(refusalKind(invalidError())).toBe('invalid');
    expect(refusalKind(forbiddenError())).toBe('other');
    expect(refusalKind(serverError())).toBe('other');
    expect(refusalKind(new TypeError('network'))).toBe('other');
  });
});

// --- The calendar ------------------------------------------------------------------

interface Rendered {
  root: Root;
  container: HTMLDivElement;
  client: QueryClient;
}

let rendered: Rendered | null = null;

function render(
  options: {
    items?: CalendarItem[];
    canSchedule?: boolean;
    canOverride?: boolean;
    update?: CalendarLoaders['updateSession'];
    fetchCalendar?: CalendarLoaders['fetchCalendar'];
  } = {},
): Rendered {
  const items = options.items ?? [MOVABLE, OCCURRENCE, FIXED];
  const loaders: CalendarLoaders = {
    fetchCalendar:
      options.fetchCalendar ??
      vi.fn(() => Promise.resolve<CalendarPage>(page(items))),
    searchTutors: () => Promise.resolve(TUTOR_OPTIONS),
    searchStudents: () => Promise.resolve(STUDENT_OPTIONS),
    fetchRooms: () => Promise.resolve(ROOM_OPTIONS),
    updateSession: options.update,
  };
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: createElement(ScheduleCalendar, {
          state: calendarState(),
          onStateChange: () => {},
          timeZone: ZONE,
          today: WEEK,
          loaders,
          canSchedule: options.canSchedule ?? true,
          canOverride: options.canOverride ?? true,
        }),
      },
    ],
    { initialEntries: ['/'] },
  );
  act(() =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(RouterProvider, { router }),
      ),
    ),
  );
  rendered = { root, container, client };
  return rendered;
}

async function loaded(): Promise<void> {
  await vi.waitFor(() => {
    expect(stub.last?.events.length ?? 0).toBeGreaterThan(0);
  });
  await settle();
}

function eventOf(id: string): StubEvent {
  const found = stub.last!.events.find((event) => event.id === id);
  if (!found) {
    throw new Error(`No event ${id}`);
  }
  return found;
}

/** Drops the event on a new slot (local times on a date, center zone). */
function dropInfo(
  item: CalendarItem,
  date: string,
  start: string,
  end: string,
) {
  const revert = vi.fn();
  return {
    revert,
    info: {
      event: {
        start: new Date(at(date, start)),
        end: new Date(at(date, end)),
        extendedProps: eventOf(item.id).extendedProps,
      },
      revert,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function statusText(): string {
  return [...rendered!.container.querySelectorAll('[role="status"]')]
    .map((node) => node.textContent ?? '')
    .join(' | ');
}

function button(root: ParentNode, name: string): HTMLButtonElement | undefined {
  return [...root.querySelectorAll('button')].find(
    (b) => b.textContent === name,
  );
}

beforeEach(() => {
  applyLanguage('en');
  stub.last = null;
});

afterEach(() => {
  if (rendered) {
    const { root, container, client } = rendered;
    act(() => root.unmount());
    container.remove();
    client.clear();
    rendered = null;
  }
  document.body.innerHTML = '';
});

describe('who can drag (D2)', () => {
  it('an Owner drags editable events only; the others are fixed', async () => {
    render({ update: vi.fn() });
    await loaded();

    expect(stub.last!.eventStartEditable).toBe(true);
    expect(stub.last!.eventDurationEditable).toBe(true);
    expect(stub.last!.eventResizableFromStart).toBe(false);
    expect(stub.last!.snapDuration).toBe('00:15:00');
    expect(stub.last!.views.dayGridMonth).toEqual({
      eventDurationEditable: false,
    });
    expect(eventOf(MOVABLE.id).editable).toBeUndefined();
    expect(eventOf(OCCURRENCE.id).editable).toBeUndefined();
    expect(eventOf(FIXED.id).editable).toBe(false);
    expect(eventOf(MOVABLE.id).extendedProps.movable).toBe(true);
    expect(eventOf(FIXED.id).extendedProps.movable).toBe(false);
  });

  it('a Coordinator (schedule.view only) gets no drag, resize or Reschedule', async () => {
    render({ canSchedule: false, update: vi.fn() });
    await loaded();

    expect(stub.last!.eventStartEditable).toBe(false);
    expect(stub.last!.eventDurationEditable).toBe(false);
    expect(stub.last!.events.every((event) => event.editable === false)).toBe(
      true,
    );
    await click(
      rendered!.container.querySelector(`[data-session-id="${MOVABLE.id}"]`)!,
    );
    expect(openDialog()).not.toBeNull();
    expect(button(openDialog()!, 'Reschedule')).toBeUndefined();
  });
});

describe('a drop (D6)', () => {
  it('saves optimistically, refuses a second drop while pending, then updates and refetches', async () => {
    const answer = deferred<ClassSessionUpdated>();
    const update = vi.fn(() => answer.promise);
    // The refetch after the move never answers here: what shows is the
    // cache update from the 200.
    const fetchCalendar = vi
      .fn<CalendarLoaders['fetchCalendar']>()
      .mockResolvedValueOnce(page([MOVABLE, OCCURRENCE, FIXED]))
      .mockReturnValue(new Promise<CalendarPage>(() => {}));
    render({ update, fetchCalendar });
    await loaded();
    const first = dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00');

    act(() => stub.last!.eventDrop(first.info));
    await settle();

    expect(update).toHaveBeenCalledWith(MOVABLE.id, {
      version: 3,
      starts_at: '2026-11-10T17:00:00.000Z',
      ends_at: '2026-11-10T18:00:00.000Z',
    });
    // Pending: at the new slot, "Saving…", not draggable.
    expect(eventOf(MOVABLE.id).start).toBe('2026-11-10T17:00:00.000Z');
    expect(eventOf(MOVABLE.id).extendedProps.pending).toBe(true);
    expect(eventOf(MOVABLE.id).editable).toBe(false);
    expect(
      rendered!.container.querySelector(`[data-session-id="${MOVABLE.id}"]`)
        ?.textContent,
    ).toContain('Saving…');

    const second = dropInfo(MOVABLE, '2026-11-11', '11:00', '12:00');
    act(() => stub.last!.eventDrop(second.info));
    await settle();
    expect(second.revert).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);

    const calls = fetchCalendar.mock.calls.length;
    answer.resolve(
      updated(MOVABLE, {
        starts_at: '2026-11-10T17:00:00Z',
        ends_at: '2026-11-10T18:00:00Z',
      }),
    );
    await settle();

    expect(first.revert).not.toHaveBeenCalled();
    expect(eventOf(MOVABLE.id).extendedProps.pending).toBe(false);
    expect(eventOf(MOVABLE.id).extendedProps.item.version).toBe(4);
    expect(statusText()).toContain(
      'Moved to November 10, 2026, 11:00 AM–12:00 PM.',
    );
    expect(fetchCalendar.mock.calls.length).toBeGreaterThan(calls);
  });

  it('a resize sends the new end; an occurrence says it is now an exception', async () => {
    const update = vi.fn((_id: string, body: ClassSessionUpdate) =>
      Promise.resolve(updated(OCCURRENCE, body)),
    );
    render({ update });
    await loaded();
    const resize = dropInfo(OCCURRENCE, '2026-11-10', '10:00', '11:30');

    act(() => stub.last!.eventResize(resize.info));
    await settle();

    expect(update).toHaveBeenCalledWith(OCCURRENCE.id, {
      version: 2,
      starts_at: '2026-11-10T16:00:00.000Z',
      ends_at: '2026-11-10T17:30:00.000Z',
    });
    expect(statusText()).toContain(
      'Only this occurrence was moved. It is now an exception to its series.',
    );
  });

  it('shows the workload warnings as a notice', async () => {
    const update = vi.fn((_id: string, body: ClassSessionUpdate) =>
      Promise.resolve(
        updated(MOVABLE, body, {
          warnings: [
            {
              code: 'daily_limit_exceeded',
              details: {
                date: '2026-11-10',
                limit_minutes: 90,
                scheduled_minutes: 120,
              },
            },
          ],
        }),
      ),
    );
    render({ update });
    await loaded();

    act(() =>
      stub.last!.eventDrop(
        dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00').info,
      ),
    );
    await settle();

    expect(rendered!.container.textContent).toContain('Workload warnings');
    expect(rendered!.container.textContent).toContain(
      'Daily teaching limit exceeded',
    );
  });

  it.each([
    ['scheduling_conflict', conflictError(HARD_CONFLICTS)],
    ['version_conflict', staleError()],
    ['session_not_editable', notEditableError()],
    ['not_found', notFoundError()],
    ['crosses_local_date', invalidError()],
    ['permission_denied', forbiddenError()],
    ['internal_error', serverError()],
    ['network', new TypeError('Failed to fetch')],
  ])('reverts on %s', async (_code, error) => {
    const update = vi.fn(() => Promise.reject(error));
    render({ update });
    await loaded();
    const drop = dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00');

    act(() => stub.last!.eventDrop(drop.info));
    await settle();

    expect(drop.revert).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
    // Back where the server has it, not pending.
    expect(eventOf(MOVABLE.id).start).toBe(MOVABLE.starts_at);
    expect(eventOf(MOVABLE.id).extendedProps.pending).toBe(false);
  });

  it('a version conflict asks to reload; Reload reads the range again', async () => {
    const fetchCalendar = vi.fn(() =>
      Promise.resolve(page([MOVABLE, OCCURRENCE, FIXED])),
    );
    render({
      update: vi.fn(() => Promise.reject(staleError())),
      fetchCalendar,
    });
    await loaded();

    act(() =>
      stub.last!.eventDrop(
        dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00').info,
      ),
    );
    await settle();
    expect(alerts(rendered!.container).join(' ')).toContain(
      'Someone else changed this session. Reload the calendar to see the latest version.',
    );
    const calls = fetchCalendar.mock.calls.length;

    await click(button(rendered!.container, 'Reload')!);
    await settle();

    expect(fetchCalendar.mock.calls.length).toBeGreaterThan(calls);
    expect(statusText()).toContain('Calendar reloaded');
    expect(button(rendered!.container, 'Reload')).toBeUndefined();
  });

  it('a session that can no longer move: the message and a refetch', async () => {
    const fetchCalendar = vi.fn(() =>
      Promise.resolve(page([MOVABLE, OCCURRENCE, FIXED])),
    );
    render({
      update: vi.fn(() => Promise.reject(notEditableError())),
      fetchCalendar,
    });
    await loaded();
    const calls = fetchCalendar.mock.calls.length;

    act(() =>
      stub.last!.eventDrop(
        dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00').info,
      ),
    );
    await settle();

    expect(alerts(rendered!.container).join(' ')).toContain(
      "This session can't be changed any more",
    );
    expect(fetchCalendar.mock.calls.length).toBeGreaterThan(calls);
  });

  it('a 422 shows the field message', async () => {
    render({ update: vi.fn(() => Promise.reject(invalidError())) });
    await loaded();

    act(() =>
      stub.last!.eventDrop(
        dropInfo(MOVABLE, '2026-11-10', '11:00', '12:00').info,
      ),
    );
    await settle();

    expect(alerts(rendered!.container).join(' ')).toContain(
      'A session must start and end on the same day.',
    );
  });
});

describe('"Move not saved" (F1-F3)', () => {
  async function refusedWith(
    conflicts: typeof SOFT_CONFLICTS,
    options: {
      canOverride?: boolean;
      then?: () => Promise<ClassSessionUpdated>;
    } = {},
  ) {
    const update = vi.fn((_id: string, body: ClassSessionUpdate) =>
      body.override && options.then
        ? options.then()
        : Promise.reject(conflictError(conflicts)),
    );
    render({ update, canOverride: options.canOverride ?? true });
    await loaded();
    act(() =>
      stub.last!.eventDrop(
        dropInfo(MOVABLE, '2026-11-15', '10:00', '11:00').info,
      ),
    );
    await settle();
    return update;
  }

  it('names the attempted slot and lists the conflicts; soft only + holder: "Override and move"', async () => {
    await refusedWith(SOFT_CONFLICTS);
    const dialog = openDialog()!;

    expect(dialog.textContent).toContain('Move not saved');
    expect(dialog.textContent).toContain('November 15, 2026');
    expect(dialog.textContent).toContain('10:00 AM–11:00 AM');
    expect(dialog.textContent).toContain('Can be overridden');
    expect(button(dialog, 'Override and move')).toBeDefined();
    expect(document.activeElement?.textContent).toBe('Move not saved');
  });

  it('a hard conflict: no button, fix the others first', async () => {
    await refusedWith(HARD_CONFLICTS);
    const dialog = openDialog()!;

    expect(dialog.textContent).toContain('Not allowed');
    expect(button(dialog, 'Override and move')).toBeUndefined();
    expect(dialog.textContent).toContain(
      'Fix the conflicts that aren’t allowed first.',
    );
  });

  it('without schedule.override: no button, who can override', async () => {
    await refusedWith(SOFT_CONFLICTS, { canOverride: false });
    const dialog = openDialog()!;

    expect(button(dialog, 'Override and move')).toBeUndefined();
    expect(dialog.textContent).toContain('Only an Owner');
  });

  it('the override resends the same body and version with the soft codes', async () => {
    const update = await refusedWith(SOFT_CONFLICTS, {
      then: () =>
        Promise.resolve(
          updated(MOVABLE, {
            starts_at: '2026-11-15T16:00:00Z',
            ends_at: '2026-11-15T17:00:00Z',
          }),
        ),
    });
    await click(button(openDialog()!, 'Override and move')!);
    const dialogs = document.querySelectorAll('[role="dialog"]');
    const overrideDialog = dialogs[dialogs.length - 1] as HTMLElement;
    const reason = overrideDialog.querySelector('textarea')!;
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value',
      )!.set!.call(reason, ' La familia lo pidió ');
      reason.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await click(button(overrideDialog, 'Override and move')!);
    await settle();

    expect(update).toHaveBeenLastCalledWith(MOVABLE.id, {
      version: 3,
      starts_at: '2026-11-15T16:00:00.000Z',
      ends_at: '2026-11-15T17:00:00.000Z',
      override: {
        codes: ['outside_operating_hours'],
        reason: 'La familia lo pidió',
      },
    });
    expect(openDialog()).toBeNull();
    expect(statusText()).toContain('Moved with an override.');
  });

  it('a new conflict list after the override replaces the panel', async () => {
    const update = await refusedWith(SOFT_CONFLICTS, {
      then: () => Promise.reject(conflictError(HARD_CONFLICTS)),
    });
    await click(button(openDialog()!, 'Override and move')!);
    const dialogs = document.querySelectorAll('[role="dialog"]');
    const overrideDialog = dialogs[dialogs.length - 1] as HTMLElement;
    const reason = overrideDialog.querySelector('textarea')!;
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value',
      )!.set!.call(reason, 'Motivo');
      reason.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await click(button(overrideDialog, 'Override and move')!);
    await settle();

    expect(update).toHaveBeenCalledTimes(2);
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(openDialog()!.textContent).toContain('Not allowed');
    expect(button(openDialog()!, 'Override and move')).toBeUndefined();
  });

  it('closing it puts the focus back on the event', async () => {
    await refusedWith(HARD_CONFLICTS);

    await click(button(openDialog()!, 'Close')!);
    await settle();

    expect(openDialog()).toBeNull();
    expect(document.activeElement?.getAttribute('data-session-id')).toBe(
      MOVABLE.id,
    );
  });
});

describe('the Reschedule dialog (E)', () => {
  async function openReschedule(update: CalendarLoaders['updateSession']) {
    render({ update });
    await loaded();
    const event = rendered!.container.querySelector<HTMLElement>(
      `[data-session-id="${MOVABLE.id}"]`,
    )!;
    event.focus();
    await click(event);
    await click(button(openDialog()!, 'Reschedule')!);
    return openDialog()!;
  }

  it('is prefilled in the center zone with the room unchanged; focus on the date', async () => {
    const dialog = await openReschedule(vi.fn());

    expect(dialog.textContent).toContain('Reschedule session');
    expect(fieldByLabel(dialog, 'Date').value).toBe('2026-11-09');
    expect(fieldByLabel(dialog, 'Start').value).toBe('10:00');
    expect(fieldByLabel(dialog, 'End').value).toBe('11:00');
    expect(dialog.textContent).toContain('Aula 2, unchanged');
    expect(dialog.textContent).toContain('America/Mexico_City');
    expect(document.activeElement).toBe(fieldByLabel(dialog, 'Date'));
  });

  it('checks only "end after start" and the required fields', async () => {
    const update = vi.fn();
    const dialog = await openReschedule(update);

    typeInto(fieldByLabel(dialog, 'End'), '09:00');
    await click(button(dialog, 'Save')!);

    expect(update).not.toHaveBeenCalled();
    expect(dialog.textContent).toContain(
      'The end must be after the start, on the same day.',
    );
  });

  it('sends the same PATCH as a drop, then closes and returns focus to the event', async () => {
    const update = vi.fn((_id: string, body: ClassSessionUpdate) =>
      Promise.resolve(updated(MOVABLE, body)),
    );
    const dialog = await openReschedule(update);

    typeInto(fieldByLabel(dialog, 'Date'), '2026-11-12');
    typeInto(fieldByLabel(dialog, 'Start'), '16:00');
    typeInto(fieldByLabel(dialog, 'End'), '17:00');
    await click(button(dialog, 'Save')!);
    await settle();

    expect(update).toHaveBeenCalledWith(MOVABLE.id, {
      version: 3,
      starts_at: '2026-11-12T22:00:00.000Z',
      ends_at: '2026-11-12T23:00:00.000Z',
    });
    expect(openDialog()).toBeNull();
    expect(statusText()).toContain('Moved to November 12, 2026');
    expect(document.activeElement?.getAttribute('data-session-id')).toBe(
      MOVABLE.id,
    );
  });

  it('shows a conflict inside the dialog, keeping the values, with the override', async () => {
    const update = vi.fn(() => Promise.reject(conflictError(SOFT_CONFLICTS)));
    const dialog = await openReschedule(update);
    typeInto(fieldByLabel(dialog, 'Date'), '2026-11-15');

    await click(button(dialog, 'Save')!);
    await settle();

    expect(openDialog()).toBe(dialog);
    expect(dialog.textContent).toContain('The session can’t move there');
    expect(fieldByLabel(dialog, 'Date').value).toBe('2026-11-15');
    expect(button(dialog, 'Override and move')).toBeDefined();
  });

  it('a version conflict closes it and the reload notice takes the focus', async () => {
    const dialog = await openReschedule(
      vi.fn(() => Promise.reject(staleError())),
    );
    typeInto(fieldByLabel(dialog, 'Start'), '12:00');
    typeInto(fieldByLabel(dialog, 'End'), '13:00');

    await click(button(dialog, 'Save')!);
    await settle();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(openDialog()).toBeNull();
    expect(alerts(rendered!.container).join(' ')).toContain(
      'Someone else changed this session.',
    );
    expect(document.activeElement?.textContent).toContain(
      'Someone else changed this session.',
    );
  });

  it('a session_unchanged 422 stays above the fields', async () => {
    const dialog = await openReschedule(
      vi.fn(() => Promise.reject(invalidError('session_unchanged', ['body']))),
    );

    await click(button(dialog, 'Save')!);
    await settle();

    expect(alerts(dialog).join(' ')).toContain(
      'The new date, times and classroom are the same as now.',
    );
  });
});

describe('the "Overridden" marker (F4)', () => {
  it('is in the event name and explained in the dialog', async () => {
    render({ items: [OVERRIDDEN_ITEM], update: vi.fn() });
    await loaded();
    const event = rendered!.container.querySelector<HTMLElement>(
      `[data-session-id="${OVERRIDDEN_ITEM.id}"]`,
    )!;

    expect(event.textContent).toContain('Overridden');
    await click(event);
    expect(openDialog()!.textContent).toContain(
      'Booked or moved with an override of a soft rule.',
    );
  });
});

describe('the strings (F5)', () => {
  const keys = [
    'scheduling.markers.overridden.label',
    'scheduling.markers.overridden.explanation',
    'scheduling.calendar.move.saving',
    'scheduling.calendar.move.moved',
    'scheduling.calendar.move.movedWithOverride',
    'scheduling.calendar.move.occurrence',
    'scheduling.calendar.move.notSaved',
    'scheduling.calendar.move.attempted',
    'scheduling.calendar.move.backInPlace',
    'scheduling.calendar.move.overrideAction',
    'scheduling.calendar.move.overrideDescription',
    'scheduling.calendar.move.stale',
    'scheduling.calendar.move.reload',
    'scheduling.calendar.move.reloaded',
    'scheduling.calendar.reschedule.action',
    'scheduling.calendar.reschedule.title',
    'scheduling.calendar.reschedule.roomUnchanged',
    'scheduling.calendar.reschedule.occurrenceNote',
  ] as const;

  it.each(['es', 'en'] as const)('exist in %s', (language) => {
    const t = i18n.getFixedT(language, 'common');
    for (const key of keys) {
      expect(i18n.exists(key, { lng: language, ns: 'common' })).toBe(true);
      expect(t(key)).not.toBe(key);
    }
    expect(i18n.getFixedT(language, 'errors')('session_not_editable')).not.toBe(
      'session_not_editable',
    );
  });

  it('are translated, not copied', () => {
    const es = i18n.getFixedT('es', 'common');
    const en = i18n.getFixedT('en', 'common');
    expect(es('scheduling.calendar.move.notSaved')).toBe(
      'No se guardó el cambio',
    );
    expect(en('scheduling.calendar.move.notSaved')).toBe('Move not saved');
    expect(es('scheduling.markers.overridden.label')).not.toBe(
      en('scheduling.markers.overridden.label'),
    );
  });
});
