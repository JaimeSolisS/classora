import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, userEvent, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { LocalDate } from '@/lib/time';
import type { BusinessSettings } from '@/features/settings/api';
import { SETTINGS } from '@/features/settings/fixtures';
import type { CalendarLoaders, CalendarPage, SessionState } from './api';
import { SESSION_KINDS, type SessionKind } from './kinds';
import {
  ANA,
  calendarState,
  CARMEN,
  DST_ITEMS,
  DST_WEEK,
  item,
  KIND_SIZE_ITEMS,
  page,
  ROOM,
  ROOM_OPTIONS,
  SHORT_ITEMS,
  STATE_KIND_ITEMS,
  STUDENT_OPTIONS,
  TIJUANA,
  TUTOR_OPTIONS,
  WEEK,
  WEEK_ITEMS,
  ZONE,
} from './fixtures';
import { formatHour } from './hours';
import { ScheduleCalendar } from './ScheduleCalendar';
import type { CalendarState } from './urlState';

// The calendar (#57 E6): Week, Day, Month and List with fixture data; every
// state and both markers in one week; active filters (chips); loading,
// empty, error and truncated; the event dialog; a Tijuana DST week. HF-07:
// every class type in the fixture week, the legend, event sizes per type
// and every state on every type. The loaders are stubs: no request leaves
// the story.

interface StoryArgs {
  initial: CalendarState;
  timeZone: string;
  today: LocalDate;
  result: () => Promise<CalendarPage>;
  canSchedule: boolean;
  /** HF-08: the business settings (the opening hours). */
  settings: () => Promise<BusinessSettings>;
}

function CalendarStory({
  initial,
  timeZone,
  today,
  result,
  canSchedule,
  settings,
}: StoryArgs) {
  const [state, setState] = useState(initial);
  const loaders: CalendarLoaders = {
    fetchCalendar: () => result(),
    searchTutors: () =>
      Promise.resolve({ items: TUTOR_OPTIONS, total: TUTOR_OPTIONS.length }),
    searchStudents: () =>
      Promise.resolve({
        items: STUDENT_OPTIONS,
        total: STUDENT_OPTIONS.length,
      }),
    fetchRooms: () => Promise.resolve(ROOM_OPTIONS),
    // #54b D2: every event is an occurrence of one series here.
    fetchSeriesId: () =>
      Promise.resolve('00000000-0000-4000-8000-0000000007a1'),
    fetchSettings: () => settings(),
  };
  return (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">
        {i18n.t('scheduling.calendar.title')}
      </h1>
      <ScheduleCalendar
        state={state}
        onStateChange={setState}
        timeZone={timeZone}
        today={today}
        loaders={loaders}
        canSchedule={canSchedule}
      />
    </main>
  );
}

const meta = {
  title: 'Scheduling/ScheduleCalendar',
  component: CalendarStory,
  args: {
    initial: calendarState(),
    timeZone: ZONE,
    today: WEEK,
    result: () => Promise.resolve(page(WEEK_ITEMS)),
    canSchedule: true,
    settings: () => Promise.resolve(SETTINGS),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[{ path: '/', element: <CalendarStory {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof CalendarStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const stateName = (state: SessionState) =>
  new RegExp(escape(i18n.t(`scheduling.states.${state}`)));

async function events(canvasElement: HTMLElement) {
  const region = within(canvasElement).getByRole('region', {
    name: i18n.t('scheduling.calendar.regionLabel'),
  });
  await waitFor(() => expect(region).toHaveAttribute('aria-busy', 'false'));
  return within(region);
}

export const Week: Story = {
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    const shown: SessionState[] = [
      'unassigned',
      'offered',
      'confirmed',
      'completed',
      'skipped_closure',
      'conflict',
    ];
    for (const state of shown) {
      await expect(
        region.getAllByRole('button', { name: stateName(state) }).length,
      ).toBeGreaterThan(0);
    }
    await expect(
      region.getAllByRole('button', {
        name: new RegExp(
          String(i18n.t('scheduling.markers.center_closed.label')),
        ),
      }),
    ).toHaveLength(2);
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('scheduling.calendar.views.week'),
      }),
    ).toHaveAttribute('aria-pressed', 'true');
  },
};

export const Day: Story = {
  args: {
    initial: calendarState({ view: 'day', date: '2026-11-10' as LocalDate }),
  },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    await expect(region.getAllByRole('button')).toHaveLength(2);
  },
};

export const Month: Story = {
  args: { initial: calendarState({ view: 'month' }) },
};

export const List: Story = {
  args: { initial: calendarState({ view: 'list' }) },
};

export const CancelledShown: Story = {
  args: {
    initial: calendarState({
      filters: {
        tutor: CARMEN.id,
        student: ANA.student_id,
        room: ROOM.id,
        kind: 'individual',
        cancelled: true,
      },
    }),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await events(canvasElement);
    const chips = canvas.getByRole('list', {
      name: i18n.t('scheduling.calendar.filters.active'),
    });
    await expect(within(chips).getAllByRole('button')).toHaveLength(5);
    await expect(
      within(chips).getByRole('button', {
        name: i18n.t('scheduling.calendar.filters.remove', {
          filter: i18n.t('scheduling.calendar.filters.tutor'),
          value: CARMEN.display_name,
        }),
      }),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { result: () => new Promise<CalendarPage>(() => {}) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('region', {
        name: i18n.t('scheduling.calendar.regionLabel'),
      }),
    ).toHaveAttribute('aria-busy', 'true');
  },
};

export const Empty: Story = {
  args: { result: () => Promise.resolve(page([])) },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    const statuses = within(canvasElement).getAllByRole('status');
    await expect(
      statuses.some(
        (status) => status.textContent === i18n.t('scheduling.calendar.empty'),
      ),
    ).toBe(true);
  },
};

export const Failed: Story = {
  args: {
    result: () =>
      Promise.reject(
        new ApiError({
          code: 'internal_error',
          status: 500,
          requestId: 'req-story-500',
        }),
      ),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await waitFor(() =>
      expect(
        canvas.getByRole('button', { name: i18n.t('feedback.retry') }),
      ).toBeVisible(),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('scheduling.calendar.today') }),
    ).toBeEnabled();
  },
};

export const Truncated: Story = {
  args: { result: () => Promise.resolve(page(WEEK_ITEMS, true)) },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    await expect(
      within(canvasElement).getByText(i18n.t('scheduling.calendar.truncated')),
    ).toBeVisible();
  },
};

export const EventDialog: Story = {
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    const [event] = region.getAllByRole('button', {
      name: stateName('unassigned'),
    });
    event!.focus();
    await userEvent.keyboard('{Enter}');
    const dialog = within(document.body).getByRole('dialog');
    await expect(dialog).toHaveAccessibleName(WEEK_ITEMS[0]!.subject.name);
    await expect(
      within(dialog).getByText(
        i18n.t('scheduling.markers.center_closed.explanation'),
      ),
    ).toBeVisible();
    await expect(
      await within(dialog).findByRole('link', {
        name: i18n.t('scheduling.calendar.dialog.openSeries'),
      }),
    ).toHaveAttribute(
      'href',
      '/schedule/series/00000000-0000-4000-8000-0000000007a1',
    );
  },
};

export const TijuanaFallBack: Story = {
  args: {
    initial: calendarState({ view: 'day', date: '2026-11-01' as LocalDate }),
    timeZone: TIJUANA,
    today: DST_WEEK,
    result: () => Promise.resolve(page(DST_ITEMS)),
  },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    await expect(region.getAllByRole('button')).toHaveLength(2);
    await expect(
      region.getByRole('button', { name: /UTC−07:00/ }),
    ).toBeInTheDocument();
    await expect(
      region.getByRole('button', { name: /UTC−08:00\)–/ }),
    ).toBeInTheDocument();
  },
};

/** Every event's state and markers fit inside the event's box. */
async function statusVisible(event: HTMLElement) {
  const box = event.getBoundingClientRect();
  const status = event.querySelector('.cl-event-status');
  await expect(status).not.toBeNull();
  for (const part of [
    status!,
    ...status!.querySelectorAll<HTMLElement>('.cl-badge, [data-kind]'),
  ]) {
    const rect = part.getBoundingClientRect();
    await expect(rect.width).toBeGreaterThan(0);
    await expect(rect.top).toBeGreaterThanOrEqual(box.top - 1);
    await expect(rect.bottom).toBeLessThanOrEqual(box.bottom + 1);
    await expect(rect.right).toBeLessThanOrEqual(box.right + 1);
  }
}

export const ShortEvents: Story = {
  args: { result: () => Promise.resolve(page(SHORT_ITEMS)) },
  parameters: { viewport: { defaultViewport: 'responsive' } },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    const buttons = region.getAllByRole('button');
    await expect(buttons).toHaveLength(3);
    for (const [index, state] of (
      ['unassigned', 'offered', 'confirmed'] as const
    ).entries()) {
      const event = buttons[index]!;
      await expect(event).toHaveAccessibleName(stateName(state));
      await expect(event).toHaveAccessibleName(
        new RegExp(escape(i18n.t('scheduling.markers.center_closed.label'))),
      );
      await expect(
        event.querySelectorAll('.cl-event-status [data-marker]'),
      ).toHaveLength(2);
      await expect(event.querySelector('.cl-event-status')).toHaveTextContent(
        i18n.t(`scheduling.states.${state}`),
      );
      await statusVisible(event);
    }
  },
};

// --- HF-07: the class type and the tutor --------------------------------------

const kindLabel = (kind: SessionKind) =>
  i18n.t(`scheduling.calendar.filters.kinds.${kind}`);
const kindName = (kind: SessionKind) =>
  new RegExp(`, ${escape(kindLabel(kind))}, `);

/** The event element (FullCalendar's, with the `cl-event` classes). */
function eventOf(canvasElement: HTMLElement, id: string): HTMLElement {
  const event = canvasElement.querySelector<HTMLElement>(
    `[data-session-id="${id}"]`,
  );
  if (!event) {
    throw new Error(`No event ${id}`);
  }
  return event;
}

/** The fixture week shows every type, with the legend above the grid. */
export const KindsAndLegend: Story = {
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    for (const kind of SESSION_KINDS) {
      const item = WEEK_ITEMS.find((entry) => entry.kind === kind)!;
      const event = eventOf(canvasElement, item.id);
      await expect(event).toHaveClass(`cl-event--kind-${kind}`);
      await expect(event).toHaveAccessibleName(kindName(kind));
      await expect(
        event.querySelector(`[data-kind="${kind}"] svg`),
      ).not.toBeNull();
    }
    const legend = within(canvasElement).getByRole('list', {
      name: i18n.t('scheduling.calendar.legend.title'),
    });
    await expect(within(legend).getAllByRole('listitem')).toHaveLength(3);
    for (const kind of SESSION_KINDS) {
      await expect(within(legend).getByText(kindLabel(kind))).toBeVisible();
    }
  },
};

/** AC3: 15/30/60/120-minute events of each type. A short event keeps its
 * status line; a 60-minute one shows its state, time, subject, first
 * student and tutor, each line whole (cut with an ellipsis, not in half). */
export const KindSizes: Story = {
  args: { result: () => Promise.resolve(page(KIND_SIZE_ITEMS)) },
  parameters: { viewport: { defaultViewport: 'responsive' } },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    for (const item of KIND_SIZE_ITEMS) {
      const event = eventOf(canvasElement, item.id);
      await expect(event).toHaveClass(`cl-event--kind-${item.kind}`);
      const minutes =
        (Date.parse(item.ends_at) - Date.parse(item.starts_at)) / 60_000;
      if (minutes < 90) {
        await statusVisible(event);
      }
      if (minutes === 60) {
        const box = event.getBoundingClientRect();
        const lines = [
          ...event.querySelectorAll<HTMLElement>('.cl-event-body > *'),
        ];
        await expect(lines).toHaveLength(5);
        for (const line of lines) {
          const rect = line.getBoundingClientRect();
          await expect(rect.height).toBeGreaterThan(0);
          await expect(rect.bottom).toBeLessThanOrEqual(box.bottom + 1);
        }
      }
    }
  },
};

/** AC4: each state keeps its border style and its icon with text on every
 * type's background; the background differs by type. */
export const StatesByKind: Story = {
  args: {
    initial: calendarState({
      filters: { ...calendarState().filters, cancelled: true },
    }),
    result: () => Promise.resolve(page(STATE_KIND_ITEMS)),
  },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    const states = [...new Set(STATE_KIND_ITEMS.map((entry) => entry.state))];
    await expect(states).toHaveLength(7);
    for (const state of states) {
      const boxes = STATE_KIND_ITEMS.filter(
        (entry) => entry.state === state,
      ).map((entry) => eventOf(canvasElement, entry.id));
      await expect(boxes).toHaveLength(3);
      const styles = boxes.map((box) => getComputedStyle(box));
      for (const style of styles.slice(1)) {
        await expect(style.borderTopStyle).toBe(styles[0]!.borderTopStyle);
        await expect(style.borderTopColor).toBe(styles[0]!.borderTopColor);
      }
      await expect(
        new Set(styles.map((style) => style.backgroundColor)).size,
      ).toBe(3);
      for (const box of boxes) {
        await expect(box).toHaveClass(`cl-event--${state}`);
        await expect(box).toHaveAccessibleName(stateName(state));
        await expect(box).toHaveTextContent(
          i18n.t(`scheduling.states.${state}`),
        );
      }
    }
  },
};

// --- HF-08: the center's opening hours -----------------------------------------

/** The `data-time` of the grid's first and last slot rows. */
function slotTimes(canvasElement: HTMLElement): string[] {
  return [
    ...new Set(
      [...canvasElement.querySelectorAll<HTMLElement>('[data-time]')].map(
        (row) => row.dataset.time ?? '',
      ),
    ),
  ].sort();
}

const hoursCaption = (
  from: string,
  to: string,
  key: 'center' | 'outside' = 'center',
) => {
  const language = i18n.language === 'en' ? 'en' : 'es';
  return i18n.t(`scheduling.calendar.hours.${key}`, {
    from: formatHour(from, language),
    to: formatHour(to, language),
  });
};

/** Saturday (09:00–14:00): Day shows that day's hours only. */
export const SaturdayDay: Story = {
  args: {
    initial: calendarState({ view: 'day', date: '2026-11-14' as LocalDate }),
    result: () => Promise.resolve(page([])),
  },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    const times = slotTimes(canvasElement);
    await expect(times[0]).toBe('09:00:00');
    await expect(times.at(-1)).toBe('13:30:00');
    await expect(
      within(canvasElement).getByText(hoursCaption('09:00', '14:00')),
    ).toBeInTheDocument();
  },
};

/** A class at 07:00 (an override): the window grows to show it. */
export const OutsideHours: Story = {
  args: {
    result: () =>
      Promise.resolve(
        page([
          ...WEEK_ITEMS,
          item({
            starts_at: '2026-11-11T13:00:00Z',
            ends_at: '2026-11-11T14:00:00Z',
          }),
        ]),
      ),
  },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    const times = slotTimes(canvasElement);
    await expect(times[0]).toBe('07:00:00');
    await expect(times.at(-1)).toBe('19:30:00');
    await expect(
      region.getAllByRole('button', { name: /7:00/ }).length,
    ).toBeGreaterThan(0);
    await expect(
      within(canvasElement).getByText(
        hoursCaption('09:00', '20:00', 'outside'),
      ),
    ).toBeInTheDocument();
  },
};
