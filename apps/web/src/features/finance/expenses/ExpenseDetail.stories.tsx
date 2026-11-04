import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  EXPENSE_OVERDUE,
  EXPENSE_PAID,
  EXPENSE_PARTIAL,
  EXPENSE_UNPAID,
  EXPENSE_VOID,
  EXPENSES_OWNER_SESSION,
} from '../fixtures';
import { ExpenseDetailView } from './ExpenseDetailView';

// One expense (M7-12b F2): unpaid, partly paid with a reversed payment,
// overdue (inactive category and vendor), paid, void, the Finance view with
// no "Revertir", and the out-of-date state. No request. axe runs on each
// story in both themes.

const OWNER = { manage: true, reverse: true };
const FINANCE = { manage: true, reverse: false };

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/records/x"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/ExpenseDetail',
  component: ExpenseDetailView,
  args: {
    expense: EXPENSE_UNPAID,
    can: OWNER,
    onRecord: fn(),
    onAdjust: fn(),
    onVoid: fn(),
    onReverse: fn(),
  },
  render: (args) => (
    <Shell>
      <ExpenseDetailView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof ExpenseDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Unpaid: Story = {};

/** A reversed payment is struck through and doesn't count; void waits. */
export const PartlyPaidWithReversal: Story = {
  args: { expense: EXPENSE_PARTIAL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('finance.expenses.detail.actions.voidBlocked')),
    ).toBeVisible();
  },
};

export const Overdue: Story = { args: { expense: EXPENSE_OVERDUE } };

export const Paid: Story = { args: { expense: EXPENSE_PAID } };

export const Void: Story = { args: { expense: EXPENSE_VOID } };

/** Finance Staff: everything but "Revertir". */
export const FinanceView: Story = {
  args: { expense: EXPENSE_PARTIAL, can: FINANCE },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryAllByText(i18n.t('finance.expenses.detail.payments.reverse')),
    ).toHaveLength(0);
  },
};

/** C3: a refetch failed; the actions are held until a reload lands. */
export const OutOfDate: Story = {
  args: {
    expense: EXPENSE_PARTIAL,
    blocked: true,
    staleError: new ApiError({ status: 400, code: 'database_unavailable' }),
    onReload: fn(),
    announcement: i18n.t('finance.expenses.detail.closedLost.payment'),
  },
};

export const Mobile: Story = {
  args: { expense: EXPENSE_PARTIAL },
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
  globals: { viewport: { value: 'narrow320' } },
};
