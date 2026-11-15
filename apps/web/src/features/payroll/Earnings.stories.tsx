import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import type { EarningsPeriodDetail } from './api';
import { EarningsList } from './EarningsList';
import {
  DETAIL_ADJUSTMENTS,
  DETAIL_COURSE,
  DETAIL_FULL,
  DETAIL_HOURLY,
  DETAIL_SALARIED,
  EARNINGS_MIXED,
  EARNINGS_NONE,
  EARNINGS_PARTIAL,
  PAYMENT_UNDONE,
} from './earningsFixtures';
import { EarningsPeriodView } from './EarningsPeriodView';
import { TUTOR_SESSION } from './storySession';

// The tutor's earnings (M7-10b E2): the list on /my-pay empty, mixed and at
// 320 px, and a finalized period salaried, hourly with events, per course,
// with adjustments and a carry-forward, and with a reversed payment, at full
// width and 320 px. No request. axe runs on each story in both themes.

const TUTOR_ID =
  TUTOR_SESSION.status === 'signedIn' ? TUTOR_SESSION.user.id : '';

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

function Shell({ path, children }: { path: string; children: ReactNode }) {
  return (
    <StoryAuth session={TUTOR_SESSION}>
      <StoryRouter
        path={path}
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const fits = async (root: HTMLElement) => {
  const main = root.querySelector('main')!;
  await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
};

const targets = async (root: ParentNode, selector: string) => {
  const found = root.querySelectorAll<HTMLElement>(selector);
  await expect(found.length).toBeGreaterThan(0);
  for (const target of found) {
    await expect(target.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
  }
};

const meta = {
  title: 'Payroll/Earnings',
  component: EarningsList,
  args: {
    state: {
      status: 'ready',
      page: EARNINGS_MIXED,
      refreshError: null,
      onRefresh: fn(),
    },
    tutorUserId: TUTOR_ID,
    onPage: fn(),
  },
  render: (args) => (
    <Shell path="/my-pay">
      <EarningsList {...args} />
    </Shell>
  ),
} satisfies Meta<typeof EarningsList>;

export default meta;
type Story = StoryObj<typeof meta>;

/** No finalized period yet (B1). */
export const ListEmpty: Story = {
  args: {
    state: {
      status: 'ready',
      page: EARNINGS_NONE,
      refreshError: null,
      onRefresh: fn(),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-earnings-empty]')?.textContent,
    ).toBe(i18n.t('payroll.earnings.empty'));
  },
};

/** Unpaid, part paid, paid and an empty period with no payslip (B1, B2). */
export const ListMixed: Story = {
  play: async ({ canvasElement }) => {
    const link = canvasElement.querySelector<HTMLAnchorElement>(
      `[data-earnings-table] [data-earnings-id="${EARNINGS_PARTIAL.id}"] a[download]`,
    );
    await expect(link?.getAttribute('download')).toBe('P-2026-000012.pdf');
    await expect(
      canvasElement.querySelector('[data-earnings-table] [data-no-activity]')
        ?.textContent,
    ).toBe(i18n.t('payroll.earnings.noActivity'));
  },
};

/** Cards at 320 px: no sideways scroll, 44 px links (B2, B3). */
export const ListMobile: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    await fits(canvasElement);
    await targets(canvasElement, '[data-earnings-cards] a');
  },
};

/** A refetch failed: the rows stay, marked (D1). */
export const ListOutOfDate: Story = {
  args: {
    state: {
      status: 'ready',
      page: EARNINGS_MIXED,
      refreshError: new ApiError({ code: 'network_error' }),
      onRefresh: fn(),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-earnings-stale]'),
    ).not.toBeNull();
  },
};

const detail = (value: EarningsPeriodDetail) => ({
  render: () => (
    <Shell path={`/my-pay/periods/${value.period.id}`}>
      <EarningsPeriodView
        state={{
          status: 'ready',
          detail: value,
          refreshError: null,
          onRefresh: fn(),
        }}
        tutorUserId={TUTOR_ID}
      />
    </Shell>
  ),
});

const groups = (root: ParentNode) =>
  [...root.querySelectorAll('[data-earnings-group]')].map((element) =>
    element.getAttribute('data-earnings-group'),
  );

/** A salaried tutor, paid in full: the salary piece `n / d` (C3). */
export const DetailSalaried: Story = {
  ...detail(DETAIL_SALARIED),
  play: async ({ canvasElement }) => {
    await expect(groups(canvasElement)).toEqual(['salary']);
    await expect(
      canvasElement.querySelector('[data-head="remaining"]'),
    ).toBeNull();
  },
};

/** An hourly tutor with a late cancellation, a no-show and a makeup. */
export const DetailHourlyWithEvents: Story = {
  ...detail(DETAIL_HOURLY),
  play: async ({ canvasElement }) => {
    await expect(groups(canvasElement)).toEqual(['classes']);
  },
};

/** A per-course tutor: each line is its share `k / N`. */
export const DetailPerCourse: Story = {
  ...detail(DETAIL_COURSE),
};

/** Every adjustment kind, a carry-forward's periods and a manual reason. */
export const DetailAdjustments: Story = {
  ...detail(DETAIL_ADJUSTMENTS),
  play: async ({ canvasElement }) => {
    await expect(groups(canvasElement)).toEqual(['adjustments']);
    await expect(
      canvasElement.querySelector('[data-adjustment-reason]'),
    ).not.toBeNull();
  },
};

/** Everything, part paid, with a reversed payment struck through (C4). */
export const DetailReversedPayment: Story = {
  ...detail(DETAIL_FULL),
  play: async ({ canvasElement }) => {
    const reversed = canvasElement.querySelector(
      `[data-earnings-payments] [data-earnings-group-table] [data-payment-id="${PAYMENT_UNDONE.id}"]`,
    );
    await expect(
      reversed?.querySelector('[data-payment-amount]')?.className,
    ).toContain('line-through');
  },
};

/** The full period at 320 px: cards, no sideways scroll, 44 px links. */
export const DetailMobile: Story = {
  ...detail(DETAIL_FULL),
  ...narrow,
  play: async ({ canvasElement }) => {
    await fits(canvasElement);
    await targets(canvasElement, '[data-earnings-head] a');
  },
};

/** "Not found", whatever the cause (A2). */
export const DetailNotFound: Story = {
  render: () => (
    <Shell path="/my-pay/periods/x">
      <EarningsPeriodView
        state={{ status: 'notFound' }}
        tutorUserId={TUTOR_ID}
      />
    </Shell>
  ),
};
