import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { InvitationListItem } from './api';
import { InvitationsList } from './InvitationsList';

// The open invitations (#29 I5, K3): rows, empty and error. No request.

const ROWS: InvitationListItem[] = [
  {
    user_id: '00000000-0000-4000-8000-0000000000aa',
    username: 'ana.tutor',
    display_name: 'Ana Tutor',
    roles: ['tutor'],
    status: 'active',
    expires_at: '2026-10-20T18:00:00Z',
    issued_at: '2026-10-19T18:00:00Z',
  },
  {
    user_id: '00000000-0000-4000-8000-0000000000bb',
    username: 'carlos.admin',
    display_name: 'Carlos Admin',
    roles: ['administrator'],
    status: 'expired',
    expires_at: '2026-10-18T09:30:00Z',
    issued_at: '2026-10-17T09:30:00Z',
  },
];

const meta = {
  title: 'Users/InvitationsList',
  component: InvitationsList,
  args: {
    state: {
      status: 'ready',
      page: { items: ROWS, total: 2, limit: 10, offset: 0 },
    },
    canReissue: (item: InvitationListItem) => !item.roles.includes('owner'),
    onReissue: fn(),
    onPage: fn(),
  },
} satisfies Meta<typeof InvitationsList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(3);
    await expect(
      canvas.getByText(i18n.t('invitations.list.expired')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('invitations.list.reissueFor', { username: 'ana.tutor' }),
      }),
    );
    await expect(args.onReissue).toHaveBeenCalledWith(ROWS[0]);
  },
};

export const Empty: Story = {
  args: {
    state: {
      status: 'ready',
      page: { items: [], total: 0, limit: 10, offset: 0 },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('invitations.list.empty')),
    ).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('errors:internal_error'),
    );
  },
};
