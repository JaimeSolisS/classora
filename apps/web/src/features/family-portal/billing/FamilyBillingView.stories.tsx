import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import {
  EMPTY_LEDGER_PAGE,
  LEDGER_ENTRIES,
  PAYMENTS_PAGE,
  SUMMARY_NO_ACCOUNT,
} from '@/features/billing/accounts/fixtures';
import { i18n } from '@/i18n';
import { familyCharge, familyPayment } from './api';
import { FamilyBillingEmpty, FamilyBillingView } from './FamilyBillingView';
import {
  ALL_STATUSES_PAGE,
  EMPTY_PACKAGES_PAGE,
  LUIS,
  PACKAGES_PAGE,
  SUMMARY,
  SUMMARY_INACTIVE,
  SUMMARY_ONE_CHILD,
} from './fixtures';

// The family billing portal without its queries (#388 J2): two children
// with charges in every status, payments (one before receipts), packages
// and history; one child; a child selected; no account yet; an inactive
// family; no family; and the 320 px layout (cards, nothing scrolls
// sideways). No request; axe runs on each story in light and dark.

const CHARGES = {
  ...ALL_STATUSES_PAGE,
  items: ALL_STATUSES_PAGE.items.map(familyCharge),
};
const PAYMENTS = {
  ...PAYMENTS_PAGE,
  items: PAYMENTS_PAGE.items.map(familyPayment),
};
const EMPTY_PAGE = { items: [], total: 0, limit: 25, offset: 0 };

const meta = {
  title: 'Family portal/FamilyBillingView',
  component: FamilyBillingView,
  args: {
    summary: SUMMARY,
    studentId: null,
    onStudent: fn(),
    filter: 'all',
    onFilter: fn(),
    charges: { status: 'ready', page: CHARGES },
    onChargesPage: fn(),
    payments: { status: 'ready', page: PAYMENTS },
    onPaymentsPage: fn(),
    packages: { status: 'ready', page: PACKAGES_PAGE },
    onPackagesPage: fn(),
    ledger: {
      status: 'ready',
      items: LEDGER_ENTRIES,
      hasMore: true,
      loadingMore: false,
      onLoadMore: fn(),
    },
    onOpenCharge: fn(),
    onOpenPayment: fn(),
    onOpenPackage: fn(),
  },
  render: (args) => (
    <main className="p-4">
      <FamilyBillingView {...args} />
    </main>
  ),
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
} satisfies Meta<typeof FamilyBillingView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const TwoChildren: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const status of [
      'pending',
      'partially_paid',
      'overdue',
      'paid',
      'void',
    ] as const) {
      await expect(
        canvas.getAllByText(i18n.t(`billing.tuition.chargeStatus.${status}`))
          .length,
      ).toBeGreaterThan(0);
    }
    await expect(
      canvas.getByLabelText(i18n.t('familyBilling.filters.child')),
    ).toBeVisible();
    await expect(canvasElement.querySelector('table')).toBeNull();
  },
};

export const ChildSelected: Story = {
  args: { studentId: LUIS.id, filter: 'open' },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('familyBilling.payments.familyWide'),
      ),
    ).toBeVisible();
  },
};

export const OneChild: Story = {
  args: { summary: SUMMARY_ONE_CHILD },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByLabelText(
        i18n.t('familyBilling.filters.child'),
      ),
    ).toBeNull();
  },
};

export const NoAccount: Story = {
  args: {
    summary: SUMMARY_NO_ACCOUNT,
    charges: { status: 'ready', page: EMPTY_PAGE },
    payments: { status: 'ready', page: EMPTY_PAGE },
    packages: { status: 'ready', page: EMPTY_PACKAGES_PAGE },
    ledger: {
      status: 'ready',
      items: EMPTY_LEDGER_PAGE.items,
      hasMore: false,
      loadingMore: false,
      onLoadMore: fn(),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('familyBilling.summary.noAccount'),
      ),
    ).toBeVisible();
  },
};

export const InactiveFamily: Story = {
  args: { summary: SUMMARY_INACTIVE },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('familyBilling.inactive')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: {
    charges: { status: 'loading' },
    payments: { status: 'loading' },
    packages: { status: 'loading' },
    ledger: { status: 'loading' },
  },
};

export const NoFamily: Story = {
  render: () => (
    <main className="p-4">
      <FamilyBillingEmpty />
    </main>
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('familyBilling.noFamily')),
    ).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};
