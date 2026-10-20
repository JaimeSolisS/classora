import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ApprovalsTable } from './ApprovalsTable';
import { MIXED_ITEMS, PENDING_REGISTRATION } from './fixtures';

// The approval list (#40 F3, F6): mixed statuses and types, empty, loading
// and error. No request.

const meta = {
  title: 'Approvals/ApprovalsTable',
  component: ApprovalsTable,
  args: {
    state: {
      status: 'ready',
      page: { items: MIXED_ITEMS, total: 5, limit: 10, offset: 0 },
    },
    onDecide: fn(),
    onPage: fn(),
  },
} satisfies Meta<typeof ApprovalsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const MixedStatuses: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(6);
    for (const status of [
      'pending',
      'approved',
      'rejected',
      'changes_requested',
      'cancelled',
    ] as const) {
      await expect(
        canvas.getByText(i18n.t(`approvals.status.${status}`)),
      ).toBeVisible();
    }
    // An unknown type shows its code (twice: type and summary).
    await expect(canvas.getAllByText('test.sample')).toHaveLength(2);
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('approvals.actions.rejectFor', { name: 'rosa.diaz' }),
      }),
    );
    await expect(args.onDecide).toHaveBeenCalledWith(MIXED_ITEMS[0], 'reject');
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
      canvas.getByText(i18n.t('approvals.list.empty')),
    ).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
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

export const OnePending: Story = {
  args: {
    state: {
      status: 'ready',
      page: { items: [PENDING_REGISTRATION], total: 1, limit: 10, offset: 0 },
    },
  },
};
