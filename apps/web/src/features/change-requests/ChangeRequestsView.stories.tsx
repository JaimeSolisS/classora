import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { LUCIA } from '@/features/my-schedule/fixtures';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ChangeRequestsView,
  type ChangeRequestsViewProps,
} from './ChangeRequestsView';
import {
  ALL_REQUESTS,
  PENDING_CANCELLATION,
  SECOND_CHILD,
  ZONE,
} from './fixtures';

// "My change requests" (#62b D3): every status, a guardian with two
// children (the student filter), empty, error, the withdraw confirmation
// and the 320 px layout. No request; axe runs in light and dark.

function ListStory(props: ChangeRequestsViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('changeRequests.title')}
              </h1>
              <ChangeRequestsView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const meta = {
  title: 'Change requests/ChangeRequestsView',
  component: ListStory,
  args: {
    state: {
      status: 'ready',
      items: ALL_REQUESTS,
      total: ALL_REQUESTS.length,
      offset: 0,
    },
    filters: { status: 'all', studentId: null, page: 1 },
    onFilters: fn(),
    students: [],
    timeZone: ZONE,
    onWithdraw: fn(() => Promise.resolve()),
    onStale: fn(),
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
} satisfies Meta<typeof ListStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const EveryStatus: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const status of [
      'pending',
      'approved',
      'rejected',
      'withdrawn',
    ] as const) {
      await expect(
        canvas.getAllByText(
          i18n.t('changeRequests.list.statusText', {
            status: i18n.t(`scheduling.changeRequest.statuses.${status}`),
          }),
        ).length,
      ).toBeGreaterThan(0);
    }
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(i18n.t('changeRequests.list.withdraw')),
      }),
    ).toHaveLength(2);
  },
};

export const GuardianWithTwoChildren: Story = {
  args: { students: [LUCIA, SECOND_CHILD] },
  play: async ({ canvasElement }) => {
    const select = within(canvasElement).getByLabelText(
      i18n.t('changeRequests.list.studentFilter'),
    );
    await expect(within(select).getAllByRole('option')).toHaveLength(3);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', items: [], total: 0, offset: 0 } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('changeRequests.list.empty')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const Failed: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-1',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByRole('alert')).toBeVisible();
  },
};

export const WithdrawConfirmation: Story = {
  args: { initialWithdraw: PENDING_CANCELLATION },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('changeRequests.list.withdrawTitle')),
    ).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { students: [LUCIA, SECOND_CHILD] },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};
