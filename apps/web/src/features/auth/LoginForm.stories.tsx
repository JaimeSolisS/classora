import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { LoginForm } from './LoginForm';

// The login form in each state (#28 G3). No request: `onSubmit` is a stub.

const rejectWith = (code: string, status: number) =>
  fn(() => Promise.reject(new ApiError({ code, status, requestId: 'req-1' })));

const meta = {
  title: 'Auth/LoginForm',
  component: LoginForm,
  args: { onSubmit: fn(() => Promise.resolve()) },
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof LoginForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('auth.login.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('auth.login.username')),
    ).toHaveAttribute('autocomplete', 'username');
    await expect(
      canvas.getByLabelText(i18n.t('auth.login.password')),
    ).toHaveAttribute('autocomplete', 'current-password');
    await expect(canvas.queryByRole('alert')).toBeNull();
  },
};

export const FieldErrors: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('auth.login.submit') }),
    );
    const username = canvas.getByLabelText(i18n.t('auth.login.username'));
    await waitFor(() =>
      expect(username).toHaveAttribute('aria-invalid', 'true'),
    );
    await expect(username).toHaveAccessibleDescription(
      i18n.t('auth.login.usernameRequired'),
    );
    await expect(
      canvas.getByLabelText(i18n.t('auth.login.password')),
    ).toHaveAccessibleDescription(i18n.t('auth.login.passwordRequired'));
    await expect(username).toHaveFocus();
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};

async function submitFilled({
  canvas,
  userEvent,
}: Parameters<NonNullable<Story['play']>>[0]) {
  await userEvent.type(
    canvas.getByLabelText(i18n.t('auth.login.username')),
    'demo.owner',
  );
  await userEvent.type(
    canvas.getByLabelText(i18n.t('auth.login.password')),
    'wrong-password',
  );
  await userEvent.click(
    canvas.getByRole('button', { name: i18n.t('auth.login.submit') }),
  );
}

export const InvalidCredentials: Story = {
  args: { onSubmit: rejectWith('invalid_credentials', 401) },
  play: async (context) => {
    await submitFilled(context);
    const { canvas } = context;
    await expect(await canvas.findByRole('alert')).toHaveTextContent(
      i18n.t('errors:invalid_credentials'),
    );
    const password = canvas.getByLabelText(i18n.t('auth.login.password'));
    await waitFor(() => expect(password).toHaveFocus());
    await expect(password).toHaveValue('');
    await expect(
      canvas.getByLabelText(i18n.t('auth.login.username')),
    ).toHaveValue('demo.owner');
  },
};

export const RateLimited: Story = {
  args: { onSubmit: rejectWith('rate_limited', 429) },
  play: async (context) => {
    await submitFilled(context);
    await expect(await context.canvas.findByRole('alert')).toHaveTextContent(
      i18n.t('errors:rate_limited'),
    );
  },
};

export const SessionExpiredNotice: Story = {
  args: { sessionExpired: true },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('errors:session_expired'),
    );
  },
};

export const Submitting: Story = {
  args: { onSubmit: fn(() => new Promise<void>(() => {})) },
  play: async (context) => {
    await submitFilled(context);
    const button = await context.canvas.findByRole('button', {
      name: i18n.t('auth.login.submitting'),
    });
    await expect(button).toBeDisabled();
  },
};
