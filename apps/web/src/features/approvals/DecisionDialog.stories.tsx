import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { DecisionDialog } from './DecisionDialog';
import { AVAILABILITY_CHANGE, PENDING_REGISTRATION } from './fixtures';

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

// #63 H4: an availability change: the affected classes ("and N more") and
// the link to the tutor's availability; the stale error. The link needs a
// router.

const withRouter: Story['render'] = (args) => (
  <StoryRouter
    routes={[{ path: '*', element: <DecisionDialog {...args} /> }]}
  />
);

export const AvailabilityChange: Story = {
  args: { pending: { item: AVAILABILITY_CHANGE, action: 'approve' } },
  render: withRouter,
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByText(
        i18n.t('approvals.availabilityChange.affectedTitle'),
      ),
    ).toBeVisible();
    await expect(within(dialog).getAllByRole('listitem')).toHaveLength(2);
    await expect(
      within(dialog).getByText(
        i18n.t('approvals.availabilityChange.more', { count: 1 }),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).getByRole('link', {
        name: i18n.t('approvals.availabilityChange.openAvailability'),
      }),
    ).toBeVisible();
  },
};

export const AvailabilityChangeStale: Story = {
  args: {
    pending: { item: AVAILABILITY_CHANGE, action: 'approve' },
    onConfirm: fn(() =>
      Promise.reject(
        new ApiError({ code: 'availability_change_stale', status: 409 }),
      ),
    ),
  },
  render: withRouter,
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.approve'),
      }),
    );
    await expect(
      await within(dialog).findByText(
        i18n.t('errors:availability_change_stale'),
      ),
    ).toBeVisible();
  },
};
