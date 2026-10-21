import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { SOFT_CONFLICTS, TUTOR_SUGGESTIONS, TUTORS } from '../create/fixtures';
import {
  DECLINED,
  NEW_OFFER,
  OFFERED_SESSION,
  UNASSIGNED_SESSION,
} from './fixtures';
import { OfferPanel } from './OfferPanel';
import { SessionDetailView } from './SessionDetailView';

// The staff session page (#52 H4): with its offer history, with the "Offer
// to a tutor" panel, and with a conflict (the #48 panel and the #49
// override action); an offered session with its pending offer. No request:
// the panel's calls are stubs. Axe runs in light and dark.

const meta = {
  title: 'Scheduling/SessionDetailView',
  component: SessionDetailView,
  args: {
    state: {
      status: 'ready',
      session: UNASSIGNED_SESSION,
      offers: [DECLINED],
    },
    timeZone: 'America/Mexico_City',
  },
  render: (args) => (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">
        {i18n.t('scheduling.session.title')}
      </h1>
      <SessionDetailView {...args} />
    </main>
  ),
} satisfies Meta<typeof SessionDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

const panel = (conflicts?: typeof SOFT_CONFLICTS) => (
  <OfferPanel
    tutors={TUTORS}
    timeZone="America/Mexico_City"
    canOverride
    onSuggest={fn(() => Promise.resolve(TUTOR_SUGGESTIONS))}
    onSubmit={fn(() => Promise.resolve(NEW_OFFER))}
    onOffered={fn()}
    initialConflicts={conflicts}
  />
);

export const WithHistory: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('offers.card.note', { note: DECLINED.decline_note }),
      ),
    ).toBeInTheDocument();
  },
};

export const WithOfferPanel: Story = {
  args: { offerPanel: panel() },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('scheduling.offer.heading')),
    ).toBeInTheDocument();
  },
};

export const WithConflict: Story = {
  args: { offerPanel: panel(SOFT_CONFLICTS) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('scheduling.offer.overrideAction'),
      }),
    ).toBeInTheDocument();
  },
};

export const Offered: Story = {
  args: {
    state: { status: 'ready', session: OFFERED_SESSION, offers: [] },
    offered: { tutorName: 'Carmen Ruiz', warnings: NEW_OFFER.warnings },
  },
};
