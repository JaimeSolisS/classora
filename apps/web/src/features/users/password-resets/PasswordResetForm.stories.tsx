import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { PasswordResetForm } from './PasswordResetForm';

// The reset form (#30 L3). No request: `onSubmit` is a stub. A memory router,
// because the self hint links to /account/password.

const meta = {
  title: 'Users/PasswordResetForm',
  component: PasswordResetForm,
  args: { ownUsername: 'demo.owner', onSubmit: fn(() => Promise.resolve()) },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '/', element: <PasswordResetForm {...args} /> }]}
    />
  ),
} satisfies Meta<typeof PasswordResetForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('passwordResets.form.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('passwordResets.form.username')),
    ).toHaveAccessibleDescription(i18n.t('passwordResets.form.help'));
  },
};

export const FieldErrors: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('passwordResets.form.submit'),
      }),
    );
    const username = canvas.getByLabelText(
      i18n.t('passwordResets.form.username'),
    );
    await waitFor(() =>
      expect(username).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(username).toHaveFocus();
    await expect(
      canvas.getByText(i18n.t('passwordResets.form.usernameRequired')),
    ).toBeVisible();
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};

export const SelfHint: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.type(
      canvas.getByLabelText(i18n.t('passwordResets.form.username')),
      'Demo.Owner',
    );
    await expect(
      canvas.getByText(i18n.t('passwordResets.form.self'), { exact: false }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('account.password.link') }),
    ).toHaveAttribute('href', '/account/password');
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('passwordResets.form.submit'),
      }),
    );
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};
