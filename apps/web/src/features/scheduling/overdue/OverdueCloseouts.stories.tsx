import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CARMEN_ID,
  FETCHED_AT,
  FULL_PAGE,
  OVERDUE_ITEMS,
  ZONE,
} from './fixtures';
import {
  OverdueCloseoutsView,
  type OverdueCloseoutsViewProps,
} from './OverdueCloseoutsView';

// "Overdue closeouts" (#301b H2): the list with a "Can't submit" row and a
// long subject, filtered (tutor chip and dates), empty, empty with filters,
// loading, error, a reversed range and paginated. At 320 and 390 px the
// table scrolls inside its focusable region and the page never scrolls
// sideways; at 1280 px it fits. No request: the tutor search is a stub.
// Axe runs in light and dark (the storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

function OverdueStory(props: OverdueCloseoutsViewProps) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="p-4">
                <OverdueCloseoutsView {...props} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const ready = (
  items = OVERDUE_ITEMS,
  total = items.length,
  offset = 0,
): OverdueCloseoutsViewProps['state'] => ({
  status: 'ready',
  items,
  total,
  offset,
  fetchedAt: FETCHED_AT,
});

const meta = {
  title: 'Scheduling/OverdueCloseouts',
  component: OverdueStory,
  args: {
    state: ready(),
    filters: { tutor: null, from: null, to: null, page: 1 },
    onFilters: fn(),
    timeZone: ZONE,
    searchTutors: fn(() =>
      Promise.resolve([
        { id: CARMEN_ID, name: 'Carmen Ruiz', inactive: false },
      ]),
    ),
    tutorName: null,
    onTutorName: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof OverdueStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const FILTERS = {
  tutor: CARMEN_ID,
  from: '2026-11-01' as LocalDate,
  to: '2026-11-30' as LocalDate,
  page: 1,
};

async function tableShows(canvasElement: HTMLElement) {
  const canvas = within(canvasElement);
  await expect(
    canvas.getByRole('heading', {
      level: 1,
      name: i18n.t('scheduling.overdue.title'),
    }),
  ).toBeVisible();
  await expect(
    canvas.getByText(i18n.t('scheduling.overdue.cantSubmit')),
  ).toBeInTheDocument();
}

// #301b A8: the region (named by the heading, tabindex 0) is the only
// horizontal scroller.
async function scrollsInRegionOnly(
  canvasElement: HTMLElement,
  { scrolls }: { scrolls: boolean },
) {
  const region = within(canvasElement).getByRole('region', {
    name: i18n.t('scheduling.overdue.title'),
  });
  await expect(region).toHaveAttribute('tabindex', '0');
  if (scrolls) {
    await expect(region.scrollWidth).toBeGreaterThan(region.clientWidth);
  }
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

export const Default: Story = {
  play: async ({ canvasElement }) => tableShows(canvasElement),
};

export const DefaultNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await tableShows(canvasElement);
    await scrollsInRegionOnly(canvasElement, { scrolls: true });
  },
};

export const DefaultPhone390: Story = {
  globals: { viewport: { value: 'phone390' } },
  play: async ({ canvasElement }) => {
    await tableShows(canvasElement);
    await scrollsInRegionOnly(canvasElement, { scrolls: true });
  },
};

export const DefaultDesktop1280: Story = {
  globals: { viewport: { value: 'desktop1280' } },
  play: async ({ canvasElement }) => {
    await tableShows(canvasElement);
    const region = within(canvasElement).getByRole('region', {
      name: i18n.t('scheduling.overdue.title'),
    });
    await expect(region.scrollWidth).toBeLessThanOrEqual(region.clientWidth);
  },
};

export const Filtered: Story = {
  args: {
    state: ready([OVERDUE_ITEMS[0]!, OVERDUE_ITEMS[2]!]),
    filters: FILTERS,
    tutorName: 'Carmen Ruiz',
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('scheduling.overdue.filters.remove', {
          name: 'Carmen Ruiz',
        }),
      }),
    ).toBeVisible();
  },
};

export const FilteredNarrow320: Story = {
  ...Filtered,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) =>
    scrollsInRegionOnly(canvasElement, { scrolls: true }),
};

export const Empty: Story = {
  args: { state: ready([]) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('scheduling.overdue.empty')),
    ).toBeVisible();
  },
};

export const EmptyFiltered: Story = {
  args: { state: ready([]), filters: FILTERS, tutorName: null },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('scheduling.overdue.emptyFiltered'),
      ),
    ).toBeVisible();
  },
};

export const EmptyFilteredNarrow320: Story = {
  ...EmptyFiltered,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const page = canvasElement.ownerDocument.documentElement;
    await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
  },
};

export const ReversedRange: Story = {
  args: {
    state: { status: 'invalid' },
    filters: {
      tutor: null,
      from: '2026-11-10' as LocalDate,
      to: '2026-11-01' as LocalDate,
      page: 1,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('scheduling.overdue.filters.rangeInvalid'),
      ),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const ErrorState: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({ status: 403, code: 'permission_denied' }),
      onRetry: fn(),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toBeVisible();
  },
};

export const Paginated: Story = {
  args: { state: ready(FULL_PAGE, 30) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('navigation', {
        name: i18n.t('users.list.pagination'),
      }),
    ).toBeVisible();
  },
};

export const PaginatedPhone390: Story = {
  ...Paginated,
  globals: { viewport: { value: 'phone390' } },
  play: async ({ canvasElement }) =>
    scrollsInRegionOnly(canvasElement, { scrolls: true }),
};
