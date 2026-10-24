import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CohortsTable, type CohortsTableProps } from './CohortsTable';
import { LIST_ITEMS } from './fixtures';

// The cohorts list (#243 E3): rows in every status (upcoming, in progress,
// ended; a tutor, a pending offer, no tutor), the same at 320 px (the table
// scrolls as a focusable region), empty with "Clear filters", and an error.
// No request; axe runs in light and dark.

function TableStory(props: CohortsTableProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 id="cohorts-title" className="mb-4 text-2xl font-semibold">
                {i18n.t('courses.cohorts.title')}
              </h1>
              <CohortsTable {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const ready = (items: typeof LIST_ITEMS) =>
  ({
    status: 'ready',
    items,
    hasMore: false,
    loadingMore: false,
    onMore: fn(),
  }) as const;

const meta = {
  title: 'Courses/CohortsTable',
  component: TableStory,
  args: {
    state: ready(LIST_ITEMS),
    labelledBy: 'cohorts-title',
    filtered: true,
    onClearFilters: fn(),
  },
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
} satisfies Meta<typeof TableStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const EveryStatus: Story = {
  args: { state: { ...ready(LIST_ITEMS), hasMore: true } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const status of ['upcoming', 'in_progress', 'ended'] as const) {
      await expect(
        canvas.getAllByText(i18n.t(`courses.cohorts.status.${status}`))[0],
      ).toBeVisible();
    }
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.assignment.noTutor')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('courses.cohorts.list.loadMore'),
      }),
    ).toBeVisible();
  },
};

// WCAG reflow (#234 QA D2): at 320 px the table scrolls sideways inside a
// focusable region named by the heading; the page itself doesn't.
export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const region = within(canvasElement).getByRole('region', {
      name: i18n.t('courses.cohorts.title'),
    });
    await expect(region).toHaveAttribute('tabindex', '0');
    await expect(
      canvasElement.ownerDocument.documentElement.scrollWidth,
    ).toBeLessThanOrEqual(320);
  },
};

export const Empty: Story = {
  args: { state: ready([]) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('courses.cohorts.list.clearFilters'),
      }),
    ).toBeVisible();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-story-cohorts',
      }),
      onRetry: fn(),
    },
  },
};
