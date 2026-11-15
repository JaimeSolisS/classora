import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { USER_STATES } from './api';
import { StateBadge } from './StateBadge';

// The user state badge (#31 J3): text, never colour alone.

const meta = {
  title: 'Users/StateBadge',
  component: StateBadge,
  args: { state: 'active' },
  render: () => (
    <div className="flex flex-wrap gap-2">
      {USER_STATES.map((state) => (
        <StateBadge key={state} state={state} />
      ))}
    </div>
  ),
} satisfies Meta<typeof StateBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllStates: Story = {
  play: async ({ canvas }) => {
    for (const state of USER_STATES) {
      await expect(
        canvas.getByText(i18n.t(`userStates.${state}`)),
      ).toBeVisible();
    }
  },
};
