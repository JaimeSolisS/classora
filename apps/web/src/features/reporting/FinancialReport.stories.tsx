import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ACCRUAL_NOVEMBER,
  BALANCES,
  CASH_NOVEMBER,
  EMPTY_DAY,
  NEGATIVE_MONTH,
  ORACLE_TODAY,
  REPORTS_OWNER_SESSION,
} from './fixtures';
import { FinancialReportView } from './FinancialReportView';

// The financial report page (M7-14b E2), from #117's oracle: accrual, cash,
// an empty range, a negative gross profit, the balances, an error, the
// out-of-date alert and 320 px. No request. axe runs on each story in both
// themes.

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

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={REPORTS_OWNER_SESSION}>
      <StoryRouter
        path="/reports/finance"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const controls = {
  basis: 'accrual' as const,
  onBasis: fn(),
  draft: { from: '2026-11-01', to: '2026-11-30' },
  onDate: fn(),
  preset: 'lastMonth' as const,
  onPreset: fn(),
  today: ORACLE_TODAY,
  error: null,
};

const meta = {
  title: 'Reports/FinancialReport',
  component: FinancialReportView,
  args: {
    controls,
    profitability: { status: 'ready', data: ACCRUAL_NOVEMBER },
    balances: { status: 'ready', data: BALANCES },
    links: { overdueAccounts: true, overdueExpenses: true },
  },
  render: (args) => (
    <Shell>
      <FinancialReportView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof FinancialReportView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** #117 D1: November on the accrual basis, with the balances. */
export const Accrual: Story = {
  play: async ({ canvas }) => {
    for (const type of [
      'individual',
      'group',
      'course',
      'unallocated',
    ] as const) {
      await expect(
        canvas.getAllByText(
          i18n.t(`reports.profitability.classTypes.${type}`),
        )[0],
      ).toBeVisible();
    }
    await expect(canvas.getAllByText('$7,070.34')[0]).toBeVisible();
    await expect(canvas.getByText('-$709.66')).toBeVisible();
  },
};

/** #117 D2: November on the cash basis: center totals only. */
export const Cash: Story = {
  args: {
    controls: { ...controls, basis: 'cash' },
    profitability: { status: 'ready', data: CASH_NOVEMBER },
  },
  play: async ({ canvas }) => {
    await expect(canvas.queryByRole('table')).toBeNull();
    await expect(canvas.getByText('-$3,100.00')).toBeVisible();
  },
};

/** A day with nothing: every row at $0.00 and "Sin gastos". */
export const EmptyRange: Story = {
  args: {
    controls: {
      ...controls,
      draft: { from: '2026-12-09', to: '2026-12-09' },
      preset: null,
    },
    profitability: { status: 'ready', data: EMPTY_DAY },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('reports.profitability.expenses.empty')),
    ).toBeVisible();
  },
};

/** Tutor cost above revenue: negative values with a minus and an icon. */
export const NegativeGrossProfit: Story = {
  args: { profitability: { status: 'ready', data: NEGATIVE_MONTH } },
};

/** The balances with only the expense link (no billing.view). */
export const BalancesOnlyExpenseLink: Story = {
  args: { links: { overdueAccounts: false, overdueExpenses: true } },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByText(i18n.t('reports.balances.receivables.link')),
    ).toBeNull();
    await expect(
      canvas.getByText(i18n.t('reports.balances.payables.link')),
    ).toBeVisible();
  },
};

/** A range over 366 days: the bound next to "Desde", nothing asked. */
export const RangeTooLong: Story = {
  args: {
    controls: {
      ...controls,
      draft: { from: '2025-12-09', to: '2026-12-10' },
      preset: null,
      error: {
        field: 'from',
        message: i18n.t('reports.profitability.range.errors.tooLong'),
      },
    },
    profitability: { status: 'idle' },
  },
};

/** Both reads failed. */
export const Failed: Story = {
  args: {
    profitability: {
      status: 'error',
      error: new ApiError({ status: 400, code: 'database_unavailable' }),
      onRetry: fn(),
    },
    balances: {
      status: 'error',
      error: new ApiError({ status: 400, code: 'database_unavailable' }),
      onRetry: fn(),
    },
  },
};

/** A refetch failed: the figures stay with the out-of-date alert. */
export const Stale: Story = {
  args: {
    balances: {
      status: 'ready',
      data: BALANCES,
      refreshError: new ApiError({ status: 400, code: 'database_unavailable' }),
      onRefresh: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('reports.stale'))).toBeVisible();
  },
};

export const Loading: Story = {
  args: {
    profitability: { status: 'loading' },
    balances: { status: 'loading' },
  },
};

/** 320 px: the class rows as cards, no horizontal scroll. */
export const Mobile: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(view.innerWidth);
  },
};
