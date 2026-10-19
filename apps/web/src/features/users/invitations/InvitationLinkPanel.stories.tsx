import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { InvitationLinkPanel } from './InvitationsPage';

// The one-time link panel after an invitation (#29 I3, K3). Fixture data
// only: the token below is not real.

const meta = {
  title: 'Users/InvitationLinkPanel',
  component: InvitationLinkPanel,
  args: {
    issued: {
      user_id: '00000000-0000-4000-8000-0000000000aa',
      username: 'ana.tutor',
      display_name: 'Ana Tutor',
      roles: ['tutor'],
      expires_at: '2026-10-20T18:00:00Z',
      invitation_url:
        'http://127.0.0.1:5173/invite#token=story-fixture-token-AAAAAAAAAAAAAAAAAAAAAAAA',
    },
    onDone: fn(),
  },
} satisfies Meta<typeof InvitationLinkPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Shown: Story = {
  play: async ({ canvas, args }) => {
    const link = canvas.getByLabelText(i18n.t('invitations.result.link'));
    await expect(link).toHaveAttribute('readonly');
    await expect(link).toHaveValue(args.issued.invitation_url);
    await expect(link).toHaveAccessibleDescription(
      i18n.t('invitations.result.onceWarning'),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('invitations.result.copy') }),
    ).toBeVisible();
    await expect(canvas.queryByRole('img')).toBeNull();
  },
};
