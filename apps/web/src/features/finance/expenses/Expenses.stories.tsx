import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import type { ExpenseListParams } from '../api';
import {
  EXPENSE_ITEMS,
  EXPENSE_OVERDUE,
  EXPENSES_OWNER_SESSION,
  expenseItem,
  expensePage,
  MIXED_CATEGORIES,
  VENDORS,
} from '../fixtures';
import { ExpensesView, type ExpensesState } from './ExpensesView';
import { DEFAULT_SORT } from './rules';

// The expense list (M7-12b F2): empty, every status, an inactive category
// and vendor, no match, the stale alert, and 320 px. No request. axe runs on
// each story in both themes.

const narrow = {
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

const PARAMS: ExpenseListParams = {
  statuses: [],
  categoryId: '',
  vendorId: '',
  from: '',
  to: '',
  q: '',
  sort: DEFAULT_SORT,
  page: 1,
};

const ready = (items = EXPENSE_ITEMS, total = items.length): ExpensesState => ({
  status: 'ready',
  page: expensePage(items, { total }),
});

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/records"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/Expenses',
  component: ExpensesView,
  args: {
    state: ready(),
    params: PARAMS,
    search: '',
    onSearch: fn(),
    onFilter: fn(),
    categories: MIXED_CATEGORIES,
    vendors: VENDORS,
    rangeInvalid: false,
    filtered: false,
    onPage: fn(),
    canAdd: true,
    onAdd: fn(),
  },
  render: (args) => (
    <Shell>
      <ExpensesView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof ExpensesView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The five statuses, an inactive category and vendor, two pages. */
export const MixedStatuses: Story = {
  args: { state: ready(EXPENSE_ITEMS, 30) },
  play: async ({ canvas }) => {
    for (const status of [
      'unpaid',
      'partially_paid',
      'paid',
      'overdue',
      'void',
    ] as const) {
      await expect(
        canvas.getAllByText(i18n.t(`finance.expenses.statuses.${status}`))
          .length,
      ).toBeGreaterThan(0);
    }
  },
};

/** An expense whose category and vendor were deactivated since. */
export const InactiveCategory: Story = {
  args: { state: ready([expenseItem(EXPENSE_OVERDUE)]) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByText(i18n.t('finance.expenses.inactiveCategory')).length,
    ).toBeGreaterThan(0);
  },
};

export const Empty: Story = {
  args: { state: ready([]) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('finance.expenses.empty')),
    ).toBeVisible();
  },
};

export const NoMatch: Story = {
  args: {
    state: ready([]),
    params: { ...PARAMS, statuses: ['void'] },
    filtered: true,
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

/** B3: a refetch failed; the rows stay, marked out of date. */
export const RefreshFailed: Story = {
  args: {
    state: {
      ...ready(),
      refreshError: new ApiError({ status: 400, code: 'database_unavailable' }),
      onRefresh: fn(),
    } as ExpensesState,
    addBlocked: true,
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('finance.expenses.stale'),
    );
  },
};

/** A Finance-style view without "Nuevo gasto" (view only). */
export const ViewOnly: Story = { args: { canAdd: false } };

/** Cards below 768 px; nothing scrolls sideways at 320 px. */
export const Mobile: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(view.innerWidth);
  },
};
