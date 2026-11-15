import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, fn, screen, userEvent, waitFor, within } from 'storybook/test';
import { SETTINGS } from '@/features/settings/fixtures';
import { i18n } from '@/i18n';
import { useLanguage } from '@/i18n/use-language';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { CalendarLoaders, CalendarPage } from '../api';
import { EventContent } from '../EventContent';
import {
  calendarState,
  page,
  ROOM_OPTIONS,
  STUDENT_OPTIONS,
  TUTOR_OPTIONS,
  WEEK,
  ZONE,
} from '../fixtures';
import type { LabelContext } from '../labels';
import { ScheduleCalendar } from '../ScheduleCalendar';
import {
  FIXED,
  HARD_CONFLICTS,
  MOVABLE,
  OCCURRENCE,
  OVERRIDDEN_ITEM,
  SOFT_CONFLICTS,
  staleError,
  updated,
} from './fixtures';
import { MoveNotSavedDialog } from './MoveNotSavedDialog';
import { MoveNoticeView } from './MoveNotice';
import { RescheduleDialog } from './RescheduleDialog';
import type { MoveNotice } from './useMoveSession';

// Rescheduling on the calendar (#58 G8): a week with editable (Owner) and
// read-only (Coordinator) events, an event being saved, the "Overridden"
// marker, the "Move not saved" dialog (hard only; soft only for a holder of
// schedule.override, with the button; soft only for anyone else), its
// override dialog (empty, a reason error, submitting), the version-conflict
// and workload-warning notices, and the Reschedule dialog (empty, a field
// error, a conflict; at 320 px). No request leaves a story. Axe runs in
// light and dark.

function useContext(): LabelContext {
  const { language } = useLanguage();
  return { t: i18n.getFixedT(language, 'common'), timeZone: ZONE, language };
}

const meta = {
  title: 'Scheduling/Reschedule',
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
        small640: {
          name: 'Small (640 px)',
          styles: { width: '640px', height: '800px' },
          type: 'tablet',
        },
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

async function dialog() {
  return within(await screen.findByRole('dialog'));
}

// --- The week ----------------------------------------------------------------------

function Week({ canSchedule }: { canSchedule: boolean }) {
  const [state, setState] = useState(calendarState());
  const loaders: CalendarLoaders = {
    fetchCalendar: () =>
      Promise.resolve<CalendarPage>(
        page([MOVABLE, OCCURRENCE, FIXED, OVERRIDDEN_ITEM]),
      ),
    searchTutors: () =>
      Promise.resolve({ items: TUTOR_OPTIONS, total: TUTOR_OPTIONS.length }),
    searchStudents: () =>
      Promise.resolve({
        items: STUDENT_OPTIONS,
        total: STUDENT_OPTIONS.length,
      }),
    fetchRooms: () => Promise.resolve(ROOM_OPTIONS),
    updateSession: (_id, body) => Promise.resolve(updated(MOVABLE, body)),
    fetchSettings: () => Promise.resolve(SETTINGS),
  };
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="p-4">
                <h1 className="mb-4 text-2xl font-semibold">
                  {i18n.t('scheduling.calendar.title')}
                </h1>
                <ScheduleCalendar
                  state={state}
                  onStateChange={setState}
                  timeZone={ZONE}
                  today={WEEK}
                  loaders={loaders}
                  canSchedule={canSchedule}
                  canOverride={canSchedule}
                />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

/** A fixed clock before the fixtures' week (Monday 2026-11-09), so its
 * sessions stay upcoming (D2) whatever the real date. */
function beforeTheWeek() {
  const realNow = Date.now;
  Date.now = () => Date.parse('2026-11-02T12:00:00Z');
  return () => {
    Date.now = realNow;
  };
}

async function weekLoaded(canvasElement: HTMLElement) {
  const region = within(canvasElement).getByRole('region', {
    name: i18n.t('scheduling.calendar.regionLabel'),
  });
  await waitFor(() => expect(region).toHaveAttribute('aria-busy', 'false'));
  await waitFor(() =>
    expect(region.querySelectorAll('[data-session-id]').length).toBe(4),
  );
  return region;
}

export const WeekOwner: Story = {
  beforeEach: beforeTheWeek,
  render: () => <Week canSchedule />,
  play: async ({ canvasElement }) => {
    const region = await weekLoaded(canvasElement);
    // Editable events can be dragged; a completed one can't.
    const movable = region.querySelector(`[data-session-id="${MOVABLE.id}"]`);
    const fixed = region.querySelector(`[data-session-id="${FIXED.id}"]`);
    await expect(movable).toHaveClass('cl-event--movable');
    await expect(fixed).not.toHaveClass('cl-event--movable');
    // The overridden session (a short event: the marker as an icon).
    await expect(
      region.querySelector('[data-marker="overridden"]'),
    ).not.toBeNull();
  },
};

export const WeekCoordinatorReadOnly: Story = {
  beforeEach: beforeTheWeek,
  render: () => <Week canSchedule={false} />,
  play: async ({ canvasElement }) => {
    const region = await weekLoaded(canvasElement);
    for (const element of region.querySelectorAll('[data-session-id]')) {
      await expect(element).not.toHaveClass('cl-event--movable');
    }
  },
};

// --- Event content -------------------------------------------------------------------

function Events() {
  const context = useContext();
  return (
    <main className="cl-calendar flex max-w-xs flex-col gap-3 p-4">
      <div className="cl-event rounded border p-1">
        <EventContent item={MOVABLE} context={context} pending />
      </div>
      <div className="cl-event rounded border p-1">
        <EventContent item={MOVABLE} context={context} compact pending />
      </div>
      <div className="cl-event rounded border p-1">
        <EventContent item={OVERRIDDEN_ITEM} context={context} />
      </div>
    </main>
  );
}

export const EventPendingAndOverridden: Story = {
  render: () => <Events />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getAllByText(i18n.t('scheduling.calendar.move.saving')),
    ).toHaveLength(2);
    await expect(
      canvas.getByText(i18n.t('scheduling.markers.overridden.label')),
    ).toBeVisible();
  },
};

// --- "Move not saved" ----------------------------------------------------------------

function NotSaved({
  conflicts,
  canOverride,
  initialReason,
  onOverride = () => Promise.resolve(),
}: {
  conflicts: typeof SOFT_CONFLICTS;
  canOverride: boolean;
  initialReason?: string;
  onOverride?: (reason: string) => Promise<void>;
}) {
  const context = useContext();
  return (
    <MoveNotSavedDialog
      refused={{
        item: MOVABLE,
        body: {
          version: MOVABLE.version,
          starts_at: '2026-11-15T16:00:00Z',
          ends_at: '2026-11-15T17:00:00Z',
        },
        conflicts,
      }}
      context={context}
      canOverride={canOverride}
      onOverride={onOverride}
      onConflicts={fn()}
      onClose={fn()}
      onCloseAutoFocus={fn()}
      initialReason={initialReason}
    />
  );
}

export const NotSavedHard: Story = {
  render: () => <NotSaved conflicts={HARD_CONFLICTS} canOverride />,
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
    await expect(
      view.queryByRole('button', {
        name: i18n.t('scheduling.calendar.move.overrideAction'),
      }),
    ).toBeNull();
  },
};

export const NotSavedSoftHolder: Story = {
  render: () => <NotSaved conflicts={SOFT_CONFLICTS} canOverride />,
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByRole('button', {
        name: i18n.t('scheduling.calendar.move.overrideAction'),
      }),
    ).toBeVisible();
    await expect(
      view.getByRole('heading', {
        name: i18n.t('scheduling.calendar.move.notSaved'),
      }),
    ).toHaveFocus();
  },
};

export const NotSavedSoftNonHolder: Story = {
  render: () => <NotSaved conflicts={SOFT_CONFLICTS} canOverride={false} />,
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.override.notAllowed')),
    ).toBeVisible();
  },
};

async function overrideDialog() {
  const found = await screen.findByRole('dialog', {
    name: i18n.t('scheduling.calendar.move.overrideAction'),
  });
  return within(found);
}

export const OverrideEmpty: Story = {
  render: () => (
    <NotSaved conflicts={SOFT_CONFLICTS} canOverride initialReason="" />
  ),
  play: async () => {
    const view = await overrideDialog();
    await expect(
      view.getByLabelText(i18n.t('scheduling.override.reason')),
    ).toHaveValue('');
  },
};

export const OverrideReasonError: Story = {
  render: () => (
    <NotSaved conflicts={SOFT_CONFLICTS} canOverride initialReason="" />
  ),
  play: async () => {
    const view = await overrideDialog();
    await userEvent.click(
      view.getByRole('button', {
        name: i18n.t('scheduling.calendar.move.overrideAction'),
      }),
    );
    await expect(
      await view.findByText(i18n.t('scheduling.override.reasonRequired')),
    ).toBeVisible();
  },
};

export const OverrideSubmitting: Story = {
  render: () => (
    <NotSaved
      conflicts={SOFT_CONFLICTS}
      canOverride
      initialReason="La familia lo pidió"
      onOverride={() => new Promise(() => {})}
    />
  ),
  play: async () => {
    const view = await overrideDialog();
    await userEvent.click(
      view.getByRole('button', {
        name: i18n.t('scheduling.calendar.move.overrideAction'),
      }),
    );
    await expect(
      await view.findByText(
        i18n.t('scheduling.calendar.move.overrideSubmitting'),
      ),
    ).toBeVisible();
  },
};

// --- Notices -------------------------------------------------------------------------

function Notice({ notice }: { notice: MoveNotice }) {
  const context = useContext();
  return (
    <main className="max-w-2xl p-4">
      <MoveNoticeView notice={notice} context={context} onReload={fn()} />
    </main>
  );
}

export const VersionConflict: Story = {
  render: () => (
    <Notice
      notice={{
        kind: 'failed',
        error: staleError(),
        refusal: 'stale',
        focus: false,
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('scheduling.calendar.move.stale'),
    );
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('scheduling.calendar.move.reload'),
      }),
    ).toBeVisible();
  },
};

export const MovedWithWarnings: Story = {
  render: () => (
    <Notice
      notice={{
        kind: 'moved',
        occurrence: true,
        overridden: true,
        session: updated(
          OCCURRENCE,
          {
            starts_at: '2026-11-15T16:00:00Z',
            ends_at: '2026-11-15T17:00:00Z',
          },
          {
            warnings: [
              {
                code: 'daily_limit_exceeded',
                details: {
                  date: '2026-11-15',
                  limit_minutes: 90,
                  scheduled_minutes: 120,
                },
              },
              {
                code: 'break_too_short',
                details: { gap_minutes: 10, required_break_minutes: 15 },
              },
            ],
          },
        ),
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('scheduling.calendar.move.movedWithOverride'),
    );
    await expect(
      canvas.getByText(i18n.t('scheduling.warnings.heading')),
    ).toBeVisible();
  },
};

// --- Reschedule ----------------------------------------------------------------------

function Reschedule(extra: Partial<Parameters<typeof RescheduleDialog>[0]>) {
  const context = useContext();
  return (
    <RescheduleDialog
      item={MOVABLE}
      context={context}
      canOverride
      send={fn(() => Promise.resolve())}
      onRefused={fn()}
      onCancel={fn()}
      onCloseAutoFocus={fn()}
      {...extra}
    />
  );
}

export const RescheduleEmpty: Story = {
  render: () => <Reschedule />,
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByLabelText(i18n.t('scheduling.fields.start')),
    ).toHaveValue('10:00');
    await expect(
      view.getByLabelText(i18n.t('scheduling.fields.date')),
    ).toHaveFocus();
  },
};

export const RescheduleError: Story = {
  render: () => (
    <Reschedule
      initialErrors={{ end: 'scheduling.validation.endAfterStart' }}
    />
  ),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.validation.endAfterStart')),
    ).toBeVisible();
  },
};

export const RescheduleConflict: Story = {
  render: () => <Reschedule initialConflicts={SOFT_CONFLICTS} />,
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByRole('button', {
        name: i18n.t('scheduling.calendar.move.overrideAction'),
      }),
    ).toBeVisible();
  },
};

export const RescheduleConflictNarrow: Story = {
  render: () => <Reschedule initialConflicts={HARD_CONFLICTS} />,
  globals: { viewport: { value: 'narrow320' } },
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
  },
};

// --- The time fields (#600) ------------------------------------------------------------

/** MOVABLE as a 90-minute session (10:00-11:30 in the center zone). */
const LONG_ITEM = { ...MOVABLE, ends_at: '2026-11-09T17:30:00Z' };
const cancelReschedule = fn();

/** Start and End of the open dialog. */
function timeFields() {
  const view = within(screen.getByRole('dialog'));
  return {
    start: view.getByRole('combobox', {
      name: i18n.t('scheduling.fields.start'),
    }),
    end: view.getByRole('combobox', { name: i18n.t('scheduling.fields.end') }),
  };
}

/** The open time list (portalled outside the dialog). */
function timeList(name: string) {
  return screen.findByRole('listbox', { name });
}

/**
 * #600 AC2, AC3, AC6 in a real browser: typed key by key, the end keeps
 * the 90 minutes; Escape with the list open closes only the list; a click
 * on an option keeps the dialog; Escape with the list closed cancels.
 */
export const RescheduleTypedStart: Story = {
  render: () => <Reschedule item={LONG_ITEM} onCancel={cancelReschedule} />,
  play: async () => {
    cancelReschedule.mockClear();
    await dialog();
    const { start, end } = timeFields();
    await userEvent.clear(start);
    await userEvent.type(start, '1400');
    await userEvent.tab();
    await expect(start).toHaveValue('14:00');
    await expect(end).toHaveValue('15:30');

    // One edit: cleared, then 9 (the list opens while typing).
    await userEvent.clear(start);
    await userEvent.type(start, '9');
    await timeList(i18n.t('scheduling.fields.start'));
    await expect(end).toHaveValue('10:30');
    await userEvent.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument(),
    );
    await expect(screen.getByRole('dialog')).toBeVisible();
    await expect(cancelReschedule).not.toHaveBeenCalled();
    await expect(start).toHaveValue('14:00');
    await expect(end).toHaveValue('15:30');
    await expect(start).toHaveFocus();

    await userEvent.click(end);
    const list = await timeList(i18n.t('scheduling.fields.end'));
    await userEvent.click(within(list).getByRole('option', { name: '16:00' }));
    await expect(end).toHaveValue('16:00');
    await expect(screen.getByRole('dialog')).toBeVisible();
    await expect(cancelReschedule).not.toHaveBeenCalled();

    await userEvent.keyboard('{Escape}');
    await expect(cancelReschedule).toHaveBeenCalled();
  },
};

/** #600 AC4, AC11: the End list open inside the dialog (axe scans it),
 * scrolled to the chosen time. */
export const RescheduleEndListOpen: Story = {
  render: () => <Reschedule item={LONG_ITEM} />,
  play: async () => {
    await dialog();
    const { end } = timeFields();
    await userEvent.click(end);
    const list = await timeList(i18n.t('scheduling.fields.end'));
    const chosen = within(list).getByRole('option', { selected: true });
    await expect(chosen).toHaveTextContent('11:30');
    await waitFor(() =>
      expect(list.scrollTop).toBeGreaterThanOrEqual(chosen.offsetTop - 1),
    );
    await expect(end).toHaveFocus();
  },
};

/** #600 AC11: "9:30 PM" and the clock button fit a 640 px column. */
export const RescheduleEnglish640: Story = {
  render: () => <Reschedule item={LONG_ITEM} />,
  globals: { locale: 'en', viewport: { value: 'small640' } },
  play: async () => {
    await dialog();
    const { start, end } = timeFields();
    await userEvent.clear(start);
    await userEvent.type(start, '9:30 pm');
    await userEvent.tab();
    await expect(start).toHaveValue('9:30 PM');
    await expect(end).toHaveValue('11:00 PM');
    for (const field of [start, end]) {
      await expect(field.scrollWidth).toBeLessThanOrEqual(field.clientWidth);
    }
  },
};

/** #600 AC11: no sideways scroll at 320 px with an error shown. */
export const RescheduleErrorNarrow: Story = {
  render: () => <Reschedule item={LONG_ITEM} />,
  globals: { viewport: { value: 'narrow320' } },
  play: async () => {
    const view = await dialog();
    const { start } = timeFields();
    await userEvent.clear(start);
    await userEvent.type(start, 'abc');
    await userEvent.click(
      view.getByRole('button', {
        name: i18n.t('scheduling.calendar.reschedule.submit'),
      }),
    );
    await expect(
      view.getByText(i18n.t('scheduling.validation.timeFormat')),
    ).toBeVisible();
    const content = screen.getByRole('dialog');
    await expect(content.scrollWidth).toBeLessThanOrEqual(content.clientWidth);
    await expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  },
};
