import type { Meta, StoryObj } from '@storybook/react-vite';
import { MemoryRouter } from 'react-router';
import { expect, within } from 'storybook/test';
import { i18n } from '@/i18n';
import {
  MAKEUP_AVAILABLE,
  MAKEUP_ENDED,
  MAKEUP_NOT_ALLOWED,
  MAKEUP_SCHEDULED,
  MAKEUP_SESSION,
} from './fixtures';
import { MakeupSection } from './MakeupSection';

// The makeup block of the session page (#436 D2): available, scheduled,
// window ended, not allowed, and a session that makes up another one. Axe
// runs in light and dark.

const meta = {
  title: 'Scheduling/MakeupSection',
  component: MakeupSection,
  args: {
    session: MAKEUP_AVAILABLE,
    timeZone: 'America/Mexico_City',
    now: new Date('2030-01-01T00:00:00Z'),
    canSchedule: true,
  },
  render: (args) => (
    <MemoryRouter>
      <main className="max-w-3xl p-4">
        <h1 className="sr-only">{i18n.t('scheduling.session.title')}</h1>
        <MakeupSection {...args} />
      </main>
    </MemoryRouter>
  ),
} satisfies Meta<typeof MakeupSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Available: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.makeup.action'),
      }),
    ).toBeInTheDocument();
  },
};

export const AvailableReadOnly: Story = {
  args: { canSchedule: false },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole('link', {
        name: i18n.t('scheduling.makeup.action'),
      }),
    ).not.toBeInTheDocument();
  },
};

export const Scheduled: Story = {
  args: { session: MAKEUP_SCHEDULED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.makeup.openMakeup'),
      }),
    ).toBeInTheDocument();
  },
};

export const WindowEnded: Story = {
  args: { session: MAKEUP_ENDED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('scheduling.makeup.ended')),
    ).toBeInTheDocument();
  },
};

export const NotAllowed: Story = {
  args: { session: MAKEUP_NOT_ALLOWED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('scheduling.makeup.notAllowed')),
    ).toBeInTheDocument();
  },
};

export const MakeupOf: Story = {
  args: { session: MAKEUP_SESSION },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.makeup.openCancelled'),
      }),
    ).toBeInTheDocument();
  },
};
