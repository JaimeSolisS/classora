import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ChangePasswordForm } from './ChangePasswordForm';

// The change-password form (#30 L3). No request: `onSubmit` is a stub.

const meta = {
  title: 'Account/ChangePasswordForm',
  component: ChangePasswordForm,
  args: { username: 'demo.owner', onSubmit: fn(() => Promise.resolve()) },
} satisfies Meta<typeof ChangePasswordForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('account.password.username')),
    ).toHaveAttribute('autocomplete', 'username');
    await expect(
      canvas.getByLabelText(i18n.t('account.password.current')),
    ).toHaveAttribute('autocomplete', 'current-password');
    await expect(
      canvas.getByLabelText(i18n.t('account.password.new')),
    ).toHaveAttribute('autocomplete', 'new-password');
    await expect(
      canvas.getByLabelText(i18n.t('account.password.confirm')),
    ).toHaveAttribute('autocomplete', 'new-password');
  },
};

export const FieldErrors: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.type(
      canvas.getByLabelText(i18n.t('account.password.new')),
      'too-short',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('account.password.submit') }),
    );
    const current = canvas.getByLabelText(i18n.t('account.password.current'));
    await waitFor(() =>
      expect(current).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(current).toHaveAccessibleDescription(
      i18n.t('account.password.currentRequired'),
    );
    await expect(
      canvas.getByLabelText(i18n.t('account.password.new')),
    ).toHaveAttribute('aria-invalid', 'true');
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};

export const WrongCurrentPassword: Story = {
  args: {
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({ code: 'current_password_invalid', status: 403 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(
      canvas.getByLabelText(i18n.t('account.password.current')),
      'not-my-password',
    );
    await userEvent.type(
      canvas.getByLabelText(i18n.t('account.password.new')),
      'a-new-long-password',
    );
    await userEvent.type(
      canvas.getByLabelText(i18n.t('account.password.confirm')),
      'a-new-long-password',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('account.password.submit') }),
    );
    const current = canvas.getByLabelText(i18n.t('account.password.current'));
    await waitFor(() =>
      expect(current).toHaveAccessibleDescription(
        i18n.t('errors:current_password_invalid'),
      ),
    );
  },
};

export const Success: Story = {
  args: { initiallyDone: true },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('account.password.done'),
    );
  },
};
