import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { ResetView } from './RedeemLinkView';

// The /reset page states (#30 J, L3). No request: each story passes a state.

const meta = {
  title: 'Auth/ResetView',
  component: ResetView,
  parameters: { layout: 'fullscreen' },
  args: { state: { kind: 'incomplete' } },
} satisfies Meta<typeof ResetView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Form: Story = {
  args: {
    state: {
      kind: 'form',
      username: 'ana.tutor',
      displayName: 'Ana Tutor',
      onSubmit: fn(() => Promise.resolve()),
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: i18n.t('reset.title') }),
    ).toBeVisible();
    await expect(canvas.getByText(i18n.t('reset.username'))).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('invite.username')),
    ).toHaveAttribute('autocomplete', 'username');
    await expect(
      canvas.getByLabelText(i18n.t('invite.newPassword')),
    ).toHaveAttribute('autocomplete', 'new-password');
    await expect(
      canvas.getByLabelText(i18n.t('invite.confirmPassword')),
    ).toHaveAttribute('autocomplete', 'new-password');
  },
};

export const TokenInvalid: Story = {
  args: { state: { kind: 'invalid' } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('errors:token_invalid')),
    ).toBeVisible();
    await expect(canvas.queryByRole('textbox')).toBeNull();
  },
};

export const Incomplete: Story = {
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('invite.incomplete'))).toBeVisible();
  },
};

export const SignedInNotice: Story = {
  args: {
    state: {
      kind: 'signedIn',
      name: 'Demo Owner',
      onSignOut: fn(() => Promise.resolve()),
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('reset.signedIn', { name: 'Demo Owner' })),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('auth.userMenu.signOut') }),
    ).toBeVisible();
  },
};

export const RateLimited: Story = {
  args: { state: { kind: 'rateLimited' } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('errors:rate_limited'))).toBeVisible();
  },
};
