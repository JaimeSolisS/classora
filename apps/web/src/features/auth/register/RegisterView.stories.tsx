import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { RegisterView } from './RegisterView';

// The /register page states (#169 F, H3). No request: each story passes a
// state. A memory router, because the page links to /login and home.

const meta = {
  title: 'Auth/RegisterView',
  component: RegisterView,
  parameters: { layout: 'fullscreen' },
  args: {
    state: { kind: 'form', onSubmit: fn(() => Promise.resolve()) },
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '/', element: <RegisterView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof RegisterView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: i18n.t('register.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('register.role.legend') }),
    ).toBeVisible();
    for (const role of ['guardian', 'student'] as const) {
      await expect(
        canvas.getByRole('radio', { name: i18n.t(`register.role.${role}`) }),
      ).not.toBeChecked();
    }
    await expect(
      canvas.getByLabelText(i18n.t('register.password')),
    ).toHaveAttribute('autocomplete', 'new-password');
    await expect(
      canvas.getByRole('link', { name: i18n.t('register.backToLogin') }),
    ).toHaveAttribute('href', '/login');
  },
};

export const FieldErrors: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('register.submit') }),
    );
    const guardian = canvas.getByRole('radio', {
      name: i18n.t('register.role.guardian'),
    });
    await waitFor(() =>
      expect(guardian).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(guardian).toHaveFocus();
    await expect(
      canvas.getByText(i18n.t('register.roleRequired')),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('register.username')),
    ).toHaveAttribute('aria-invalid', 'true');
    if (args.state.kind === 'form') {
      await expect(args.state.onSubmit).not.toHaveBeenCalled();
    }
  },
};

export const UsernameTaken: Story = {
  args: {
    state: {
      kind: 'form',
      onSubmit: fn(() =>
        Promise.reject(
          new ApiError({ code: 'username_taken', status: 409, requestId: 'r' }),
        ),
      ),
    },
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('radio', { name: i18n.t('register.role.student') }),
    );
    await userEvent.type(
      canvas.getByLabelText(i18n.t('register.displayName')),
      'Rosa Díaz',
    );
    const username = canvas.getByLabelText(i18n.t('register.username'));
    await userEvent.type(username, 'rosa.diaz');
    await userEvent.type(
      canvas.getByLabelText(i18n.t('register.password')),
      'a-long-enough-password',
    );
    await userEvent.type(
      canvas.getByLabelText(i18n.t('register.confirmPassword')),
      'a-long-enough-password',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('register.submit') }),
    );
    await waitFor(() =>
      expect(username).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(
      canvas.getByText(i18n.t('errors:username_taken')),
    ).toBeVisible();
  },
};

export const Sent: Story = {
  args: { state: { kind: 'sent', username: 'rosa.diaz' } },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('register.sent', { username: 'rosa.diaz' }),
    );
    await expect(canvas.queryByRole('textbox')).toBeNull();
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
      canvas.getByText(i18n.t('register.signedIn', { name: 'Demo Owner' })),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('register.goHome') }),
    ).toHaveAttribute('href', '/');
    await expect(canvas.queryByRole('textbox')).toBeNull();
  },
};
