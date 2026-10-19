import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { RegistrationRequestItem } from './api';
import { DecisionDialog } from './DecisionDialog';

// The approve and reject confirmations (#169 G3, H3). No request:
// `onConfirm` is a stub. The dialog is portalled to <body>, outside the
// story canvas.

const ITEM: RegistrationRequestItem = {
  id: '00000000-0000-4000-8000-0000000000c1',
  username: 'rosa.diaz',
  display_name: 'Rosa Díaz',
  requested_role: 'guardian',
  status: 'pending',
  submitted_at: '2026-10-19T15:00:00Z',
  decided_at: null,
};

const names = () => ({ name: 'Rosa Díaz', role: i18n.t('roles.guardian') });

const meta = {
  title: 'Users/DecisionDialog',
  component: DecisionDialog,
  args: {
    pending: { item: ITEM, decision: 'approve' },
    onConfirm: fn(() => Promise.resolve()),
    onCancel: fn(),
  },
} satisfies Meta<typeof DecisionDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Approve: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('registrations.approveTitle', names()),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('registrations.approveConfirm', names()),
    );
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('registrations.approve'),
      }),
    );
    await expect(context.args.onConfirm).toHaveBeenCalledWith({
      item: ITEM,
      decision: 'approve',
    });
  },
};

export const Reject: Story = {
  args: { pending: { item: ITEM, decision: 'reject' } },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('registrations.rejectTitle', names()),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('registrations.rejectConfirm', { username: 'rosa.diaz' }),
    );
  },
};

export const Sending: Story = {
  args: { onConfirm: fn(() => new Promise<void>(() => {})) },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('registrations.approve'),
      }),
    );
    await waitFor(() =>
      expect(
        within(dialog).getByText(i18n.t('registrations.approving')),
      ).toBeVisible(),
    );
  },
};

export const Failed: Story = {
  args: {
    onConfirm: fn(() =>
      Promise.reject(
        new ApiError({ code: 'internal_error', status: 500, requestId: 'r' }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('registrations.approve'),
      }),
    );
    await expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      i18n.t('errors:internal_error'),
    );
  },
};
