import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CANCUN,
  CONFIRMED_REQUEST,
  PENDING_REQUEST,
  REJECTED_REQUEST,
  WITHDRAWN_REQUEST,
} from './fixtures';
import { MyRequestsView, type MyRequestsViewProps } from './MyRequestsView';

// "My session requests" (#59 F5): empty, mixed statuses, the withdraw
// confirmation and the 320 px layout. No request; axe runs in light and
// dark.

function MyRequestsStory(props: MyRequestsViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('sessionRequests.list.title')}
              </h1>
              <MyRequestsView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const ALL = [
  PENDING_REQUEST,
  WITHDRAWN_REQUEST,
  CONFIRMED_REQUEST,
  REJECTED_REQUEST,
];

const meta = {
  title: 'Family portal/MyRequestsView',
  component: MyRequestsStory,
  args: {
    state: { status: 'ready', items: ALL },
    timeZone: CANCUN,
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
} satisfies Meta<typeof MyRequestsStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  args: { state: { status: 'ready', items: [] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('sessionRequests.list.empty')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const MixedStatuses: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const status of [
      'pending',
      'withdrawn',
      'confirmed',
      'rejected',
    ] as const) {
      await expect(
        canvas.getByText(i18n.t(`sessionRequests.status.${status}`)),
      ).toBeVisible();
    }
    await expect(
      canvas.getAllByRole('button', {
        name: /Withdraw|Retirar/,
      }),
    ).toHaveLength(1);
  },
};

export const WithdrawConfirmation: Story = {
  args: { initialWithdraw: PENDING_REQUEST },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('sessionRequests.list.withdrawTitle')),
    ).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};
