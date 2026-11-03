import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { CarryForwardDialog } from './CarryForwardDialog';
import { ManualAdjustmentDialog } from './ManualAdjustmentDialog';
import {
  FINANCE_ID,
  LINE_ADJ_MANUAL,
  LINE_ADJUSTMENT,
  LINE_CARRY_OUT,
  LINE_HOURLY,
  PERIOD_FINALIZED,
  PERIOD_OPEN,
  review,
  SESSION_ID,
} from './periodFixtures';
import { PeriodReviewView } from './PeriodReviewView';
import { FINANCE_SESSION } from './storySession';

// Corrections after lock (M7-08b F2): the manual adjustment dialog in its
// three shapes, the carry-forward dialog, a negative period's banner and a
// finalized period's "Correct line", at full width and 320 px. No request.
// axe runs on each story in both themes.

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

const can = {
  prepare: true,
  finalize: false,
  openSession: true,
  openPayPages: true,
  correct: true,
};

const NEGATIVE = review(PERIOD_OPEN, [LINE_HOURLY, LINE_ADJ_MANUAL], '-300.00');

const meta = {
  title: 'Payroll/Adjustments',
  component: PeriodReviewView,
  args: {
    review: NEGATIVE,
    today,
    user: { id: FINANCE_ID, roles: ['finance_staff'] },
    can,
    running: null,
    refreshError: null,
    onReload: fn(),
    announcement: '',
    failure: null,
    onRecalculate: fn(),
    onPrepare: fn(),
    onReturn: fn(),
    onFinalize: fn(),
    onCarry: fn(),
    onCorrect: fn(),
  },
  render: (args) => (
    <Shell>
      <PeriodReviewView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof PeriodReviewView>;

export default meta;
type Story = StoryObj<typeof meta>;

const targets = async (root: ParentNode, selector: string) => {
  for (const target of root.querySelectorAll<HTMLElement>(selector)) {
    await expect(target.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
  }
};

/** An ended open period below zero: the banner and its action. */
export const NegativeBanner: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('payroll.period.carry.action'),
      }),
    ).toBeEnabled();
  },
};

export const NegativeBannerNarrow: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    await targets(main, '[data-carry-banner] button');
  },
};

/** A finalized period: every line can be corrected; adjustments labelled. */
export const FinalizedCorrect: Story = {
  args: {
    review: review(
      PERIOD_FINALIZED,
      [LINE_HOURLY, LINE_ADJUSTMENT, LINE_CARRY_OUT],
      '650.00',
    ),
  },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(
      main.querySelectorAll('[data-period-lines-table] [data-correct-line]'),
    ).toHaveLength(3);
    await expect(main.querySelector('[data-adjustments-note]')).not.toBeNull();
  },
};

export const FinalizedCorrectNarrow: Story = {
  ...narrow,
  args: FinalizedCorrect.args,
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    await targets(main, '[data-period-lines-cards] button');
  },
};

const dialogArgs = {
  open: true,
  tutorName: 'Ana Tutora',
  onSubmit: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const ManualOnLine: Story = {
  render: () => (
    <Shell>
      <ManualAdjustmentDialog
        {...dialogArgs}
        reference={{
          kind: 'line',
          lineId: LINE_HOURLY.id,
          summary: 'Session, Oct 2, 2026, $500.00',
        }}
      />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(dialog?.textContent).toContain(
      i18n.t('payroll.adjustmentDialog.reasonHelp'),
    );
  },
};

export const ManualPayByHand: Story = {
  render: () => (
    <Shell>
      <ManualAdjustmentDialog
        {...dialogArgs}
        reference={{
          kind: 'session',
          sessionId: SESSION_ID,
          serviceDate: '2026-10-15',
          reason: 'no_rate',
        }}
        initialValues={{
          direction: 'pay_more',
          amount: '250',
          reason: 'Pago acordado',
        }}
      />
    </Shell>
  ),
};

/** The free shape warns about the stranded one-time item (M7-08c E5). */
export const ManualFree: Story = {
  render: () => (
    <Shell>
      <ManualAdjustmentDialog {...dialogArgs} reference={{ kind: 'free' }} />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(
      dialog?.querySelector('[data-stranded-warning]')?.textContent,
    ).toContain(i18n.t('payroll.adjustmentDialog.strandedTitle'));
  },
};

export const ManualFreeNarrow: Story = {
  ...narrow,
  render: ManualFree.render,
  play: async ({ canvasElement }) => {
    const dialog =
      canvasElement.ownerDocument.querySelector<HTMLElement>(
        '[role="dialog"]',
      )!;
    await expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
    await targets(dialog, 'form button');
    // The radio's whole label is its target.
    for (const radio of dialog.querySelectorAll('input[type="radio"]')) {
      await expect(
        radio.closest('label')!.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

export const CarryDialog: Story = {
  render: () => (
    <Shell>
      <CarryForwardDialog
        open
        review={NEGATIVE}
        onSubmit={fn(() => Promise.resolve())}
        onClose={fn()}
        refreshing={false}
      />
    </Shell>
  ),
  play: async ({ canvasElement }) => {
    const dialog = canvasElement.ownerDocument.querySelector('[role="dialog"]');
    await expect(dialog?.textContent).toContain('$300.00');
  },
};

export const CarryDialogNarrow: Story = {
  ...narrow,
  render: CarryDialog.render,
};
