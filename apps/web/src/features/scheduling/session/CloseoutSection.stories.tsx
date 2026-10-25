import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { i18n } from '@/i18n';
import {
  FINANCE_SUBMITTED,
  NO_CLOSEOUT,
  STAFF_SUBMITTED,
  WAITING,
  ZONE,
} from '@/features/tutor-portal/closeout/fixtures';
import { CloseoutSection } from './CloseoutSection';

// The staff session page's Closeout section (#77 H7): submitted (a
// reviewer sees the summary), Finance (summary hidden), no closeout
// recorded, waiting for the tutor and loading. Read-only, no request.

const meta = {
  title: 'Scheduling/CloseoutSection',
  component: CloseoutSection,
  args: {
    state: { status: 'ready', closeout: STAFF_SUBMITTED },
    timeZone: ZONE,
  },
} satisfies Meta<typeof CloseoutSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Submitted: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('closeout.submitted')),
    ).toBeInTheDocument();
  },
};

export const SummaryHidden: Story = {
  args: { state: { status: 'ready', closeout: FINANCE_SUBMITTED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('closeout.summaryHidden')),
    ).toBeInTheDocument();
  },
};

export const NoCloseout: Story = {
  args: { state: { status: 'ready', closeout: NO_CLOSEOUT } },
};

export const Waiting: Story = {
  args: { state: { status: 'ready', closeout: WAITING } },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};
