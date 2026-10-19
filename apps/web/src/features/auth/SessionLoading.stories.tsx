import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { SessionLoading } from './SessionLoading';

const meta = {
  title: 'Auth/SessionLoading',
  component: SessionLoading,
} satisfies Meta<typeof SessionLoading>;

export default meta;
type Story = StoryObj<typeof meta>;

// What a page under the guard shows while /auth/me is pending.
export const Default: Story = {
  play: async ({ canvasElement }) => {
    const region = canvasElement.querySelector('[aria-busy="true"]');
    await expect(region).not.toBeNull();
    await expect(region).toHaveTextContent(i18n.t('auth.session.loading'));
  },
};
