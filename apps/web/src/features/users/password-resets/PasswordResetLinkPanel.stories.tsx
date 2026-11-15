import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { PasswordResetLinkPanel } from './PasswordResetsPage';

// The one-time link panel after a reset (#30 I4, L3). Fixture data only: the
// token below is a placeholder.

const meta = {
  title: 'Users/PasswordResetLinkPanel',
  component: PasswordResetLinkPanel,
  args: {
    issued: {
      user_id: '00000000-0000-4000-8000-0000000000aa',
      username: 'ana.tutor',
      display_name: 'Ana Tutor',
      expires_at: '2026-10-20T18:00:00Z',
      reset_url: `http://127.0.0.1:5173/reset#token=${'p'.repeat(43)}`,
    },
    onDone: fn(),
  },
} satisfies Meta<typeof PasswordResetLinkPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Shown: Story = {
  play: async ({ canvas, args }) => {
    const link = canvas.getByLabelText(i18n.t('passwordResets.result.link'));
    await expect(link).toHaveAttribute('readonly');
    await expect(link).toHaveValue(args.issued.reset_url);
    await expect(link).toHaveAccessibleDescription(
      i18n.t('oneTimeLink.onceWarning'),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('oneTimeLink.copy') }),
    ).toBeVisible();
    await expect(canvas.queryByRole('img')).toBeNull();
  },
};
