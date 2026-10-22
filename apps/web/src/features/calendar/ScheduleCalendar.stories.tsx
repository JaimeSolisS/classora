import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, userEvent, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { LocalDate } from '@/lib/time';
import type { CalendarLoaders, CalendarPage, SessionState } from './api';
import {
  ANA,
  calendarState,
  CARMEN,
  DST_ITEMS,
  DST_WEEK,
  page,
  ROOM,
  ROOM_OPTIONS,
  SHORT_ITEMS,
  STUDENT_OPTIONS,
  TIJUANA,
  TUTOR_OPTIONS,
  WEEK,
  WEEK_ITEMS,
  ZONE,
} from './fixtures';
import { ScheduleCalendar } from './ScheduleCalendar';
import type { CalendarState } from './urlState';

// The calendar (#57 E6): Week, Day, Month and List with fixture data; every
// state and both markers in one week; active filters (chips); loading,
// empty, error and truncated; the event dialog; a Tijuana DST week. The
// loaders are stubs: no request leaves the story.

interface StoryArgs {
  initial: CalendarState;
  timeZone: string;
  today: LocalDate;
  result: () => Promise<CalendarPage>;
  canSchedule: boolean;
}

function CalendarStory({
  initial,
  timeZone,
  today,
  result,
  canSchedule,
}: StoryArgs) {
  const [state, setState] = useState(initial);
  const loaders: CalendarLoaders = {
    fetchCalendar: () => result(),
    searchTutors: () => Promise.resolve(TUTOR_OPTIONS),
    searchStudents: () => Promise.resolve(STUDENT_OPTIONS),
    fetchRooms: () => Promise.resolve(ROOM_OPTIONS),
    // #54b D2: every event is an occurrence of one series here.
    fetchSeriesId: () =>
      Promise.resolve('00000000-0000-4000-8000-0000000007a1'),
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
    ...status!.querySelectorAll<HTMLElement>('.cl-badge'),
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
