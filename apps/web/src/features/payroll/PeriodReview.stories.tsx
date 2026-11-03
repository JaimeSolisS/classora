import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FinalizePeriodDialog } from './FinalizePeriodDialog';
import {
  ADMIN_ID,
  EARLIER_PERIOD_ID,
  FINANCE_ID,
  LINE_DEDUCTION,
  PERIOD_FINALIZED,
  PERIOD_NOT_ENDED,
  PERIOD_OPEN,
  PERIOD_PREPARED,
  PERIOD_RETURNED,
  review,
  SESSION_ID,
} from './periodFixtures';
import { PeriodReviewView } from './PeriodReviewView';
import { ReturnPeriodDialog } from './ReturnPeriodDialog';
import { FINANCE_SESSION } from './storySession';

// A period's review (#110b H2): each status, every line kind, the warnings,
// a negative total, the out-of-range way out, a refusal with its items, the
// disabled Finalize and the two dialogs, at full width and 320 px. No
// request. axe runs on each story in both themes.

const today = '2026-10-20' as LocalDate;

const problem = (code: string, status: number, details: object = {}) =>
  new ApiError({
    code,
    status,
    problem: {
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-${code}`,
      details: details as Record<string, string>,
    },
  });

const narrowViewport = {
  viewport: {
    options: {
      narrow320: {
        name: 'Narrow (320 px)',
        styles: { width: '320px', height: '640px' },
        type: 'mobile',
      },
    },
  },
};
const narrow = {
  parameters: narrowViewport,
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

const finance = {
  prepare: true,
  finalize: false,
  openSession: true,
  openPayPages: true,
};
const reviewer = {
  prepare: false,
  finalize: true,
  openSession: true,
  openPayPages: false,
};

const meta = {
  title: 'Payroll/PeriodReviewView',
  component: PeriodReviewView,
  args: {
    review: review(PERIOD_OPEN),
    today,
    user: { id: FINANCE_ID, roles: ['finance_staff'] },
    can: finance,
    running: null,
    refreshError: null,
    onReload: fn(),
    announcement: '',
    failure: null,
    onRecalculate: fn(),
    onPrepare: fn(),
    onReturn: fn(),
    onFinalize: fn(),
  },
  render: (args) => (
    <Shell>
      <PeriodReviewView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof PeriodReviewView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Open and ended, every line kind (#111's adjustment as "Other line"). */
export const Open: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('payroll.period.actions.prepare'),
      }),
    ).toBeEnabled();
    const table = canvas.getByRole('table', {
      name: i18n.t('payroll.period.lines.caption'),
    });
    await expect(table.textContent).toContain(
      i18n.t('payroll.period.lines.kinds.unknown'),
    );
  },
};

export const OpenNotEnded: Story = {
  args: { review: review(PERIOD_NOT_ENDED) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('payroll.period.actions.prepare'),
      }),
    ).toBeDisabled();
  },
};

export const Returned: Story = {
  args: { review: review({ ...PERIOD_RETURNED, pending_closeouts: 1 }) },
};

export const NegativeTotal: Story = {
  args: { review: review(PERIOD_OPEN, [LINE_DEDUCTION], '-150.00') },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-negative-total]'),
    ).not.toBeNull();
  },
};

export const PendingCloseouts: Story = {
  args: { review: review({ ...PERIOD_OPEN, pending_closeouts: 3 }) },
};

export const Empty: Story = {
  args: { review: review(PERIOD_OPEN, [], '0.00') },
};

export const PreparedForFinance: Story = {
  args: { review: review(PERIOD_PREPARED) },
};

export const PreparedForReviewer: Story = {
  args: {
    review: review(PERIOD_PREPARED),
    user: { id: ADMIN_ID, roles: ['administrator'] },
    can: reviewer,
  },
};

export const FinalizeBlocked: Story = {
  args: {
    review: review({
      ...PERIOD_PREPARED,
      earlier_unfinalized_period_id: EARLIER_PERIOD_ID,
    }),
    user: { id: FINANCE_ID, roles: ['administrator', 'finance_staff'] },
    can: { ...finance, finalize: true },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('payroll.period.actions.finalize'),
      }),
    ).toBeDisabled();
  },
};

export const Finalized: Story = {
  args: {
    review: review(PERIOD_FINALIZED),
    user: { id: ADMIN_ID, roles: ['administrator'] },
    can: reviewer,
    announcement: i18n.t('payroll.period.done.finalized'),
  },
};

export const OutOfRangeOnReload: Story = {
  args: {
    refreshError: problem('payroll_amount_out_of_range', 409, {
      class_session_id: SESSION_ID,
    }),
  },
};

export const UnresolvedRefusal: Story = {
  args: {
    failure: problem('payroll_unresolved_work', 409, {
      count: 2,
      items: [
        {
          class_session_id: SESSION_ID,
          service_date: '2026-10-05',
          reason: 'no_rate',
        },
      ],
    }),
  },
};

export const StaleRefusal: Story = {
  args: {
    review: review(PERIOD_PREPARED),
    user: { id: ADMIN_ID, roles: ['administrator'] },
    can: reviewer,
    failure: problem('payroll_period_stale', 409, { added: 1 }),
  },
};

export const Working: Story = {
  args: { running: 'prepare' },
};

export const Narrow: Story = {
  ...narrow,
  args: {
    review: review({ ...PERIOD_RETURNED, pending_closeouts: 2 }),
  },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    const cards = main.querySelector('[data-period-lines-cards]')!;
    await expect(cards.getBoundingClientRect().height).toBeGreaterThan(0);
    for (const target of main.querySelectorAll<HTMLElement>(
      '[data-period-actions] button, [data-period-lines-cards] a',
    )) {
      await expect(
        target.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

const finalizeArgs = {
  open: true,
  review: review(PERIOD_PREPARED),
  onSubmit: fn(() => Promise.resolve()),
  onClose: fn(),
  refreshing: false,
};

export const FinalizeDialog: Story = {
  render: () => (
    <Shell>
      <FinalizePeriodDialog {...finalizeArgs} />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(dialog?.textContent).toContain('$4,166.67');
  },
};

export const FinalizeDialogNarrow: Story = {
  ...narrow,
  render: () => (
    <Shell>
      <FinalizePeriodDialog {...finalizeArgs} />
    </Shell>
  ),
};

export const ReturnDialog: Story = {
  render: () => (
    <Shell>
      <ReturnPeriodDialog
        open
        onSubmit={fn(() => Promise.resolve())}
        onClose={fn()}
      />
    </Shell>
  ),
};
