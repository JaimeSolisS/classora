import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import {
  ACCEPTED_OFFER,
  DECLINED_OFFER,
  DUE_SOON_OFFER,
  NOW,
  PENDING_OFFER,
} from './fixtures';
import { OffersView } from './OffersView';

// "My offers" (#52 H4): empty, pending, due soon, answered, and the decline
// dialog with an error. No request: the answers are stubs. Axe runs in
// light and dark (the storybook projects).

const meta = {
  title: 'Tutor portal/OffersView',
  component: OffersView,
  args: {
    tab: 'pending',
    onTab: fn(),
    state: { status: 'ready', items: [PENDING_OFFER] },
    timeZone: 'America/Mexico_City',
    now: NOW,
    onAccept: fn(() => Promise.resolve()),
    onDecline: fn(() => Promise.resolve()),
    onStale: fn(),
  },
  render: (args) => (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">{i18n.t('offers.title')}</h1>
      <OffersView {...args} />
    </main>
  ),
} satisfies Meta<typeof OffersView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  args: { state: { status: 'ready', items: [] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('offers.emptyPending')),
    ).toBeInTheDocument();
  },
};

export const Pending: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('offers.accept.action')),
    ).toBeInTheDocument();
    await expect(
      canvas.queryByText(i18n.t('offers.card.dueSoon')),
    ).not.toBeInTheDocument();
  },
};

export const DueSoon: Story = {
  args: { state: { status: 'ready', items: [DUE_SOON_OFFER, PENDING_OFFER] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getAllByText(i18n.t('offers.card.dueSoon')),
    ).toHaveLength(1);
  },
};

export const Answered: Story = {
  args: {
    tab: 'answered',
    state: { status: 'ready', items: [DECLINED_OFFER, ACCEPTED_OFFER] },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('offers.status.declined')),
    ).toBeInTheDocument();
    await expect(
      canvas.queryByText(i18n.t('offers.accept.action')),
    ).not.toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const DeclineDialogWithError: Story = {
  args: {
    initialDecline: {
      offerId: PENDING_OFFER.id,
      error: new ApiError({
        code: 'network_error',
        requestId: undefined,
      }),
    },
  },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('offers.decline.title')),
    ).toBeInTheDocument();
    await expect(within(dialog).getByRole('alert')).toBeInTheDocument();
  },
};
