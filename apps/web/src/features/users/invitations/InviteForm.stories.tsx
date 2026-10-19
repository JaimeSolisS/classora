import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { i18n } from '@/i18n';
import type { RoleCode } from '@/features/auth/session';
import { InviteForm } from './InviteForm';

// The invite form (#29 K3). No request: `onSubmit` is a stub.

const ALL_ROLES: RoleCode[] = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
  'tutor',
  'student',
  'guardian',
];

const meta = {
  title: 'Users/InviteForm',
  component: InviteForm,
  args: { roles: ALL_ROLES, onSubmit: fn(() => Promise.resolve()) },
} satisfies Meta<typeof InviteForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('invitations.form.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('invitations.form.username')),
    ).toHaveAccessibleDescription(i18n.t('common.usernameHint'));
    await expect(
      canvas.getAllByRole('option').map((option) => option.textContent),
    ).toEqual([
      i18n.t('invitations.form.chooseRole'),
      ...ALL_ROLES.map((role) => i18n.t(`roles.${role}`)),
    ]);
  },
};

export const FieldErrors: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('invitations.form.submit') }),
    );
    const username = canvas.getByLabelText(i18n.t('invitations.form.username'));
    await waitFor(() =>
      expect(username).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(username).toHaveFocus();
    await expect(
      canvas.getByLabelText(i18n.t('invitations.form.displayName')),
    ).toHaveAccessibleDescription(
      i18n.t('invitations.form.displayNameRequired'),
    );
    await expect(
      canvas.getByLabelText(i18n.t('invitations.form.role')),
    ).toHaveAccessibleDescription(i18n.t('invitations.form.roleRequired'));
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};

export const StaffNote: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.selectOptions(
      canvas.getByLabelText(i18n.t('invitations.form.role')),
      'academic_coordinator',
    );
    await expect(
      canvas.getByText(i18n.t('invitations.form.staffNote')),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('invitations.form.role')),
    ).toHaveAccessibleDescription(i18n.t('invitations.form.staffNote'));
  },
};
