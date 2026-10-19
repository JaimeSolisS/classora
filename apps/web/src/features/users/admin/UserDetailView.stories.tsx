import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { ACTIVE_TUTOR, OWN_RECORD, PENDING_INVITATION } from './fixtures';
import { UserDetailView } from './UserDetailPage';

// The user detail page (#31 K, L3): an active Tutor with every action, the
// viewer's own record (profile only), a pending invitation, the
// version_conflict alert and the suspend confirmation. No request: every
// call is a stub.

const meta = {
  title: 'Users/UserDetailView',
  component: UserDetailView,
  args: {
    user: ACTIVE_TUTOR,
    onSaveProfile: fn(() => Promise.resolve()),
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
  args: { user: OWN_RECORD },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: i18n.t('users.profile.save') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('button')).toHaveLength(1);
    await expect(
      canvas.queryByRole('heading', { name: i18n.t('users.status.title') }),
    ).toBeNull();
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
