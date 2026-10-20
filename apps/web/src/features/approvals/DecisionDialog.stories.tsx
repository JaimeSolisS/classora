import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { DecisionDialog } from './DecisionDialog';
import { PENDING_REGISTRATION } from './fixtures';

// The decision confirmations (#40 F4, F6). No request: `onConfirm` is a
// stub. The dialog is portalled to <body>, outside the story canvas.

const SUMMARY = () =>
  i18n.t('approvals.summary.registration', {
    name: 'Rosa Díaz',
    username: 'rosa.diaz',
    role: i18n.t('roles.guardian'),
  });

const meta = {
  title: 'Approvals/DecisionDialog',
  component: DecisionDialog,
  args: {
    pending: { item: PENDING_REGISTRATION, action: 'approve' },
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
      name: i18n.t('approvals.dialog.approveTitle', { summary: SUMMARY() }),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('approvals.dialog.registrationApprove', {
        name: 'Rosa Díaz',
        role: i18n.t('roles.guardian'),
      }),
    );
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.approve'),
      }),
    );
    await expect(context.args.onConfirm).toHaveBeenCalledWith(
      { item: PENDING_REGISTRATION, action: 'approve' },
      '',
    );
  },
};

export const Sending: Story = {
  args: { onConfirm: fn(() => new Promise<void>(() => {})) },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.approve'),
      }),
    );
    await waitFor(() =>
      expect(
        within(dialog).getByText(i18n.t('approvals.dialog.approving')),
      ).toBeVisible(),
    );
  },
};

export const RejectWithError: Story = {
  args: {
    pending: { item: PENDING_REGISTRATION, action: 'reject' },
    onConfirm: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'approval_transition_invalid',
          status: 409,
          requestId: 'r',
        }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('approvals.dialog.rejectTitle', { summary: SUMMARY() }),
    });
    await context.userEvent.type(
      within(dialog).getByLabelText(
        i18n.t('approvals.dialog.reasonOptionalLabel'),
      ),
      'Unknown family.',
    );
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.reject'),
      }),
    );
    await expect(context.args.onConfirm).toHaveBeenCalledWith(
      { item: PENDING_REGISTRATION, action: 'reject' },
      'Unknown family.',
    );
    await expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      i18n.t('errors:approval_transition_invalid'),
    );
  },
};

export const RequestChangesNeedsAReason: Story = {
  args: {
    pending: {
      item: {
        ...PENDING_REGISTRATION,
        request_type: 'test.sample',
        subject: {},
        allowed_actions: ['approve', 'reject', 'request_changes'],
      },
      action: 'request_changes',
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.requestChanges'),
      }),
    );
    await expect(
      within(dialog).getByText(i18n.t('approvals.dialog.reasonRequired')),
    ).toBeVisible();
    await expect(context.args.onConfirm).not.toHaveBeenCalled();
  },
};
