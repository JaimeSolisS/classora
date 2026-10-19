import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ReauthDialog } from './ReauthDialog';

// The password dialog in each state (#28 G3). No request: `onConfirm` is a
// stub. The dialog is portalled to <body>, outside the story canvas.

const rejectWith = (code: string, status: number) =>
  fn(() => Promise.reject(new ApiError({ code, status, requestId: 'req-1' })));

const meta = {
  title: 'Auth/ReauthDialog',
  component: ReauthDialog,
  args: {
    open: true,
    onConfirm: fn(() => Promise.resolve()),
    onCancel: fn(),
  },
} satisfies Meta<typeof ReauthDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

async function confirmWith(context: Context, password: string) {
  const page = body(context);
  await context.userEvent.type(
    await page.findByLabelText(i18n.t('auth.reauth.password')),
    password,
  );
  await context.userEvent.click(
    page.getByRole('button', { name: i18n.t('auth.reauth.confirm') }),
  );
}

export const Open: Story = {
  play: async (context) => {
    const page = body(context);
    const dialog = await page.findByRole('dialog', {
      name: i18n.t('auth.reauth.title'),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('auth.reauth.description'),
    );
    await waitFor(() =>
      expect(page.getByLabelText(i18n.t('auth.reauth.password'))).toHaveFocus(),
    );
  },
};

export const ReauthFailed: Story = {
  args: { onConfirm: rejectWith('reauth_failed', 403) },
  play: async (context) => {
    await confirmWith(context, 'wrong-password');
    const page = body(context);
    await expect(await page.findByRole('alert')).toHaveTextContent(
      i18n.t('errors:reauth_failed'),
    );
    const field = page.getByLabelText(i18n.t('auth.reauth.password'));
    await waitFor(() => expect(field).toHaveFocus());
    await expect(field).toHaveValue('');
  },
};

export const RateLimited: Story = {
  args: { onConfirm: rejectWith('rate_limited', 429) },
  play: async (context) => {
    await confirmWith(context, 'any-password');
    await expect(await body(context).findByRole('alert')).toHaveTextContent(
      i18n.t('errors:rate_limited'),
    );
  },
};

export const Submitting: Story = {
  args: { onConfirm: fn(() => new Promise<void>(() => {})) },
  play: async (context) => {
    await confirmWith(context, 'any-password');
    const page = body(context);
    await expect(
      await page.findByRole('button', {
        name: i18n.t('auth.reauth.confirming'),
      }),
    ).toBeDisabled();
    await expect(
      page.getByRole('button', { name: i18n.t('auth.reauth.cancel') }),
    ).toBeDisabled();
  },
};
