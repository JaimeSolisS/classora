import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { RegistrationRequestItem } from './api';
import { RegistrationRequestsTable } from './RegistrationRequestsTable';

// The registration requests (#169 G2, H3): pending, decided, empty and
// error. No request.

const PENDING: RegistrationRequestItem[] = [
  {
    id: '00000000-0000-4000-8000-0000000000c1',
    username: 'rosa.diaz',
    display_name: 'Rosa Díaz',
    requested_role: 'guardian',
    status: 'pending',
    submitted_at: '2026-10-19T15:00:00Z',
    decided_at: null,
  },
  {
    id: '00000000-0000-4000-8000-0000000000c2',
    username: 'luis.s',
    display_name: 'Luis S',
    requested_role: 'student',
    status: 'pending',
    submitted_at: '2026-10-19T15:30:00Z',
    decided_at: null,
  },
];

const DECIDED: RegistrationRequestItem[] = PENDING.map((item) => ({
  ...item,
  status: 'approved',
  decided_at: '2026-10-19T17:00:00Z',
}));

const meta = {
  title: 'Users/RegistrationRequestsTable',
  component: RegistrationRequestsTable,
  args: {
    state: {
      status: 'ready',
      page: { items: PENDING, total: 2, limit: 10, offset: 0 },
    },
    onDecide: fn(),
    onPage: fn(),
  },
} satisfies Meta<typeof RegistrationRequestsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Pending: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(3);
    await expect(canvas.getByText(i18n.t('roles.student'))).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('registrations.rejectFor', { username: 'luis.s' }),
      }),
    );
    await expect(args.onDecide).toHaveBeenCalledWith(PENDING[1], 'reject');
  },
};

export const Decided: Story = {
  args: {
    state: {
      status: 'ready',
      page: { items: DECIDED, total: 2, limit: 10, offset: 0 },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('columnheader', {
        name: i18n.t('registrations.list.decidedAt'),
      }),
    ).toBeVisible();
    await expect(canvas.queryByRole('button', { name: /rosa/ })).toBeNull();
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
      canvas.getByText(i18n.t('registrations.list.empty')),
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
