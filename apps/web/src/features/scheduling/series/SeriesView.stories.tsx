import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ENDED_SERIES,
  LINEAGE_SERIES,
  OVERRIDE_SERIES,
  SERIES_DETAIL,
  SPLIT_SERIES,
} from './fixtures';
import { SeriesView, type SeriesViewProps } from './SeriesView';

// The series page (#54b E3): a normal series with its rows (edited,
// skipped, cancelled and conflict ones) and the actions; read-only (no
// schedule.manage); ended; split, with both lineage links; with overrides
// (#236: "Overridden", a conflict row's "Override and schedule" and one to
// fix instead). No request; axe runs in light and dark.

// Before every fixture row, so no row has started.
const NOW = Date.parse('2030-12-01T00:00:00Z');

function SeriesStory(props: SeriesViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('scheduling.series.page.title')}
              </h1>
              <SeriesView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const meta = {
  title: 'Scheduling/SeriesView',
  component: SeriesStory,
  args: {
    series: SERIES_DETAIL,
    timeZone: 'America/Mexico_City',
    canManage: true,
    now: NOW,
    onEditOccurrence: fn(),
    onEditSeries: fn(),
    onCancelSeries: fn(),
  },
} satisfies Meta<typeof SeriesStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Normal: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(i18n.t('scheduling.series.page.editOccurrence')),
      }),
    ).toHaveLength(4);
    await expect(
      canvas.getByText(i18n.t('scheduling.series.page.edited')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: { canManage: false },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('button')).toHaveLength(
      0,
    );
  },
};

export const Ended: Story = {
  args: { series: ENDED_SERIES },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('scheduling.series.page.ended')),
    ).toBeVisible();
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
  },
};

export const Split: Story = {
  args: { series: SPLIT_SERIES },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.series.page.continuesFrom'),
      }),
    ).toBeVisible();
  },
};

export const Lineage: Story = {
  args: { series: LINEAGE_SERIES },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByRole('link')).toHaveLength(2);
  },
};

export const Overrides: Story = {
  args: {
    series: OVERRIDE_SERIES,
    canOverride: true,
    onOverrideOccurrence: fn(),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('scheduling.series.page.overridden')),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(i18n.t('scheduling.override.action')),
      }),
    ).toHaveLength(1);
    await expect(
      canvas.getByText(i18n.t('scheduling.series.page.fixConflict')),
    ).toBeVisible();
  },
};
