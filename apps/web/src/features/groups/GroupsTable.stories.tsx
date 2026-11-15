import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { LIST_ITEMS, STUDENT_LIST_ITEMS } from './fixtures';
import { GroupsTable, type GroupsTableProps } from './GroupsTable';

// The groups list (#234 G2): staff with rows (one ended), staff empty with
// "Clear filters", a student's own groups (no seats column), a student
// with no group, and an error. No request; axe runs in light and dark.

function TableStory(props: GroupsTableProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 id="groups-title" className="mb-4 text-2xl font-semibold">
                {i18n.t('groups.title')}
              </h1>
              <GroupsTable {...props} />
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
  title: 'Groups/GroupsTable',
  component: TableStory,
  args: {
    state: ready(LIST_ITEMS),
    view: 'staff',
    statusFilter: 'all',
    labelledBy: 'groups-title',
    filtered: true,
    onClearFilters: fn(),
  },
} satisfies Meta<typeof TableStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const StaffRows: Story = {
  args: { state: { ...ready(LIST_ITEMS), hasMore: true } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('columnheader', { name: i18n.t('groups.list.seats') }),
    ).toBeVisible();
    await expect(canvas.getByText(i18n.t('groups.ended'))).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('groups.list.loadMore') }),
    ).toBeVisible();
  },
};

export const StaffEmpty: Story = {
  args: { state: ready([]) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('groups.list.clearFilters'),
      }),
    ).toBeVisible();
  },
};

export const StudentRows: Story = {
  args: {
    state: ready(STUDENT_LIST_ITEMS),
    view: 'student',
    statusFilter: 'active',
    filtered: false,
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole('columnheader', {
        name: i18n.t('groups.list.seats'),
      }),
    ).toBeNull();
  },
};

export const StudentEmpty: Story = {
  args: {
    state: ready([]),
    view: 'student',
    statusFilter: 'active',
    filtered: false,
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('groups.list.studentEmpty')),
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
        requestId: 'req-story-groups',
      }),
      onRetry: fn(),
    },
  },
};
