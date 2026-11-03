import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  payment,
  PAYMENT_METHODS,
  PAYMENT_REVERSED,
  PAYMENT_TRANSFER,
  PERIOD_FINALIZED,
  PERIOD_PAID,
  PERIOD_PARTIAL,
  PERIOD_ZERO,
} from './periodFixtures';
import { PeriodPaymentsView } from './PeriodPaymentsView';
import { RecordPayrollPaymentDialog } from './RecordPayrollPaymentDialog';
import { ReversePayrollPaymentDialog } from './ReversePayrollPaymentDialog';
import { FINANCE_SESSION } from './storySession';

// Payroll payments (M7-09b E2): a finalized period's payments section unpaid,
// part paid, paid, with a 0.00 total, with a reversed payment, read-only for
// a delegated Administrator and out of date after a failed refetch, plus the
// two dialogs, at full width and 320 px. No request. axe runs on each story
// in both themes.

const today = '2026-10-20' as LocalDate;

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
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/payroll/periods/1"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const ready = (items = [PAYMENT_TRANSFER, PAYMENT_REVERSED]) => ({
  status: 'ready' as const,
  items,
  refreshError: null,
  onRefresh: fn(),
});

const meta = {
  title: 'Payroll/Payments',
  component: PeriodPaymentsView,
  args: {
    period: PERIOD_PARTIAL,
    payments: ready(),
    can: { record: true, reverse: true },
    busy: false,
    announcement: '',
    onRecord: fn(),
    onReverse: fn(),
  },
  render: (args) => (
    <Shell>
      <PeriodPaymentsView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof PeriodPaymentsView>;

export default meta;
type Story = StoryObj<typeof meta>;

const badge = (root: ParentNode) =>
  root.querySelector('[data-paid-state]')?.textContent;

const targets = async (root: ParentNode, selector: string) => {
  for (const target of root.querySelectorAll<HTMLElement>(selector)) {
    await expect(target.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
  }
};

/** Nothing paid yet. */
export const Unpaid: Story = {
  args: { period: PERIOD_FINALIZED, payments: ready([]) },
  play: async ({ canvas, canvasElement }) => {
    await expect(badge(canvasElement)).toBe(
      i18n.t('payroll.periods.paidStates.unpaid'),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('payroll.payments.record') }),
    ).toBeEnabled();
  },
};

/** Part paid, with a reversed payment struck through (C3). */
export const PartlyPaidWithReversal: Story = {
  play: async ({ canvasElement }) => {
    await expect(badge(canvasElement)).toBe(
      i18n.t('payroll.periods.paidStates.partially_paid'),
    );
    const reversed = canvasElement.querySelector(
      `[data-payments-table] tr[data-payment-id="${PAYMENT_REVERSED.id}"]`,
    );
    await expect(
      reversed?.querySelector('[data-payment-amount]')?.className,
    ).toContain('line-through');
    await expect(reversed?.querySelector('[data-reverse-payment]')).toBeNull();
  },
};

export const PartlyPaidNarrow: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    await targets(main, '[data-payments-cards] button');
    await targets(main, '[data-payments-section] > div > button');
  },
};

/** All paid: no "Record payment". */
export const Paid: Story = {
  args: {
    period: PERIOD_PAID,
    payments: ready([
      payment(3, { amount: '4116.67', reference: 'SPEI 4116' }),
    ]),
    can: { record: false, reverse: true },
  },
  play: async ({ canvasElement }) => {
    await expect(badge(canvasElement)).toBe(
      i18n.t('payroll.periods.paidStates.paid'),
    );
  },
};

/** A 0.00 total is paid with no payment and offers nothing (D3). */
export const ZeroTotal: Story = {
  args: {
    period: PERIOD_ZERO,
    payments: ready([]),
    can: { record: false, reverse: false },
  },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-payments-empty]')?.textContent,
    ).toBe(i18n.t('payroll.payments.nothingToPay'));
  },
};

/** A delegated Administrator reads the section only (A1). */
export const DelegatedReadOnly: Story = {
  args: { can: { record: false, reverse: false } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelectorAll('[data-payments-section] button'),
    ).toHaveLength(0);
  },
};

/** A refetch failed: the rows stay, marked, and nothing starts (D4). */
export const OutOfDate: Story = {
  args: {
    payments: {
      ...ready(),
      refreshError: new ApiError({ code: 'network_error' }),
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: i18n.t('payroll.payments.record') }),
    ).toBeDisabled();
  },
};

const payArgs = {
  open: true,
  tutorName: 'Ana Tutora',
  startsOn: PERIOD_PARTIAL.starts_on,
  endsOn: PERIOD_PARTIAL.ends_on,
  today,
  remaining: '3116.67',
  methods: { status: 'ready' as const, methods: PAYMENT_METHODS },
  onReloadMethods: fn(),
  describePayment: () => null,
  onSubmit: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const RecordDialog: Story = {
  render: () => (
    <Shell>
      <RecordPayrollPaymentDialog {...payArgs} />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(
      dialog?.querySelector('[data-payment-remaining]')?.textContent,
    ).toContain('3,116.67');
  },
};

export const RecordDialogNarrow: Story = {
  ...narrow,
  render: RecordDialog.render,
  play: async ({ canvasElement }) => {
    const dialog =
      canvasElement.ownerDocument.querySelector<HTMLElement>(
        '[role="dialog"]',
      )!;
    await expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
    await targets(dialog, 'form button');
  },
};

export const ReverseDialog: Story = {
  render: () => (
    <Shell>
      <ReversePayrollPaymentDialog
        open
        summary="Oct 20, 2026, $1,000.00, Transfer SPEI 1"
        onSubmit={fn(() => Promise.resolve())}
        onClose={fn()}
      />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(dialog?.textContent).toContain(
      i18n.t('payroll.reversalDialog.help'),
    );
  },
};

export const ReverseDialogNarrow: Story = {
  ...narrow,
  render: ReverseDialog.render,
};
