import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ACTIVE_TUTOR,
  ADMINISTRATOR_FOR_ADMINISTRATOR,
  DELEGATED_ADMINISTRATOR,
  OWN_RECORD,
  PENDING_INVITATION,
  TUTOR_FOR_ADMINISTRATOR,
} from './fixtures';
import { UserDetailView } from './UserDetailPage';

// The user detail page (#31 K, L3; #170 H3): an active Tutor with every
// action (the roles editor as an Owner), the viewer's own record (profile and
// roles with the sign-out warning), a pending invitation, the
// version_conflict alert, the suspend confirmation, the roles editor as an
// Administrator and with last_active_owner, and the delegated permissions
// (editable and read-only). No request: every call is a stub.

const meta = {
  title: 'Users/UserDetailView',
  component: UserDetailView,
  args: {
    user: ACTIVE_TUTOR,
    onSaveProfile: fn(() => Promise.resolve()),
    onSaveRoles: fn(() => Promise.resolve()),
    onSaveDelegations: fn(() => Promise.resolve()),
    onChangeState: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onIssueReset: fn(() => new Promise<never>(() => {})),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <UserDetailView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof UserDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const ActiveTutor: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: 'Ana Tutor' }),
    ).toBeVisible();
    for (const name of [
      i18n.t('users.status.suspend'),
      i18n.t('users.status.archive'),
      i18n.t('users.reset.create'),
      i18n.t('users.profile.save'),
    ]) {
      await expect(canvas.getByRole('button', { name })).toBeVisible();
    }
    await expect(
      canvas.queryByRole('button', { name: i18n.t('users.status.reactivate') }),
    ).toBeNull();
    await expect(
      canvas.getByLabelText(i18n.t('users.profile.email')),
    ).toHaveValue('ana@example.com');
  },
};

export const OwnRecord: Story = {
  args: { user: OWN_RECORD, ownRecord: true },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: i18n.t('users.profile.save') }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button').map((b) => b.textContent),
    ).toEqual([i18n.t('users.profile.save'), i18n.t('users.roles.save')]);
    await expect(
      canvas.queryByRole('heading', { name: i18n.t('users.status.title') }),
    ).toBeNull();
    await expect(
      canvas.getByText(i18n.t('users.roles.selfWarning')),
    ).toBeVisible();
  },
};

export const PendingInvitation: Story = {
  args: { user: PENDING_INVITATION },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('users.detail.pendingInvitation')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('users.status.archive') }),
    ).toBeVisible();
  },
};

export const VersionConflict: Story = {
  args: {
    onSaveProfile: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-story',
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('users.profile.save') }),
    );
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:version_conflict'));
    await userEvent.click(
      within(alert).getByRole('button', {
        name: i18n.t('users.conflict.reload'),
      }),
    );
    await expect(args.onReload).toHaveBeenCalled();
  },
};

export const SuspendConfirmation: Story = {
  play: async (context) => {
    await context.userEvent.click(
      context.canvas.getByRole('button', {
        name: i18n.t('users.status.suspend'),
      }),
    );
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('users.status.suspendTitle'),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('users.status.suspendConfirm', { name: 'Ana Tutor' }),
    );
    await waitFor(() =>
      expect(
        within(dialog).getByRole('button', {
          name: i18n.t('users.status.cancel'),
        }),
      ).toBeVisible(),
    );
  },
};

const roleBox = (canvas: Context['canvas'], role: string) =>
  canvas.getByRole('checkbox', {
    name: i18n.t(`roles.${role}` as 'roles.owner'),
  });

export const RolesAsOwner: Story = {
  play: async ({ canvas, userEvent, args }) => {
    for (const role of ['owner', 'administrator', 'finance_staff']) {
      await expect(roleBox(canvas, role)).toBeEnabled();
    }
    await expect(roleBox(canvas, 'tutor')).toBeChecked();
    const save = canvas.getByRole('button', {
      name: i18n.t('users.roles.save'),
    });
    await expect(save).toBeDisabled();
    await userEvent.click(roleBox(canvas, 'finance_staff'));
    await expect(save).toBeEnabled();
    await userEvent.click(save);
    await expect(args.onSaveRoles).toHaveBeenCalledWith([
      'finance_staff',
      'tutor',
      'guardian',
    ]);
    await expect(
      await canvas.findByText(i18n.t('users.roles.saved')),
    ).toBeVisible();
  },
};

export const RolesAsAdministrator: Story = {
  args: { user: TUTOR_FOR_ADMINISTRATOR },
  play: async ({ canvas }) => {
    for (const role of ['owner', 'administrator']) {
      await expect(roleBox(canvas, role)).toBeDisabled();
      await expect(roleBox(canvas, role)).toHaveAccessibleDescription(
        i18n.t('users.roles.locked'),
      );
    }
    await expect(roleBox(canvas, 'finance_staff')).toBeEnabled();
  },
};

export const RolesLastActiveOwner: Story = {
  args: {
    user: { ...OWN_RECORD, roles: ['owner', 'tutor'] },
    ownRecord: true,
    onSaveRoles: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'last_active_owner',
          status: 409,
          requestId: 'req-story',
        }),
      ),
    ),
  },
  play: async (context) => {
    const { canvas, userEvent } = context;
    await userEvent.click(roleBox(canvas, 'owner'));
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('users.roles.save') }),
    );
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('users.roles.selfConfirmTitle'),
    });
    await expect(dialog).toHaveAccessibleDescription(
      i18n.t('users.roles.selfConfirm'),
    );
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('users.roles.selfConfirmSave'),
      }),
    );
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:last_active_owner'));
    await waitFor(() => expect(body(context).queryByRole('dialog')).toBeNull());
  },
};

export const DelegationsEditable: Story = {
  args: { user: DELEGATED_ADMINISTRATOR },
  play: async ({ canvas, userEvent, args }) => {
    const refunds = canvas.getByRole('checkbox', {
      name: i18n.t('delegations.finance_refund_approve.label'),
    });
    await expect(refunds).toBeChecked();
    await expect(refunds).toHaveAccessibleDescription(
      i18n.t('delegations.finance_refund_approve.description'),
    );
    await userEvent.click(
      canvas.getByRole('checkbox', {
        name: i18n.t('delegations.payroll_finalize.label'),
      }),
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('users.delegations.save') }),
    );
    await expect(args.onSaveDelegations).toHaveBeenCalledWith([
      'finance.refund.approve',
      'payroll.finalize',
    ]);
  },
};

export const DelegationsReadOnly: Story = {
  args: { user: ADMINISTRATOR_FOR_ADMINISTRATOR },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { name: i18n.t('users.delegations.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('delegations.finance_refund_approve.label')),
    ).toBeVisible();
    await expect(canvas.queryAllByRole('checkbox')).toHaveLength(0);
    await expect(canvas.queryByRole('form')).toBeNull();
  },
};
