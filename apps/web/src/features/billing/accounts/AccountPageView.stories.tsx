import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { AccountPageView } from './AccountPageView';
import {
  CHARGES_PAGE,
  EMPTY_CHARGES_PAGE,
  EMPTY_PAYMENTS_PAGE,
  FAMILY_ID,
  LEDGER_ENTRIES,
  PAYMENTS_PAGE,
  SUMMARY,
  SUMMARY_NO_ACCOUNT,
} from './fixtures';

// A family's billing account without its queries (#379 G2): with balances
// (credit, charges in every status, history with "Load more", payments)
// and with no account yet (zeros, empty lists, no credit button). No
// request. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/AccountPageView',
  component: AccountPageView,
  args: {
    summary: SUMMARY,
    view: { studentId: null, statuses: [], page: 1, paymentsPage: 1 },
    onView: fn(),
    charges: { status: 'ready', page: CHARGES_PAGE },
    ledger: {
      status: 'ready',
      items: LEDGER_ENTRIES,
      hasMore: true,
      loadingMore: false,
      onLoadMore: fn(),
    },
    payments: { status: 'ready', page: PAYMENTS_PAGE },
    canRecord: true,
    onRecord: fn(),
    onApplyCredit: fn(),
    onOpenPayment: fn(),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path={`/billing/accounts/${FAMILY_ID}`}
        routes={[{ path: '*', element: <AccountPageView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof AccountPageView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithBalances: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('billing.accounts.page.title', { name: 'Familia López' }),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.accounts.credit.action'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.accounts.charges.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.accounts.ledger.more'),
      }),
    ).toBeVisible();
  },
};

export const NoAccount: Story = {
  args: {
    summary: SUMMARY_NO_ACCOUNT,
    charges: { status: 'ready', page: EMPTY_CHARGES_PAGE },
    ledger: {
      status: 'ready',
      items: [],
      hasMore: false,
      loadingMore: false,
      onLoadMore: fn(),
    },
    payments: { status: 'ready', page: EMPTY_PAYMENTS_PAGE },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.accounts.summary.noAccount')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.accounts.credit.action'),
      }),
    ).toBeNull();
  },
};

export const ReadOnly: Story = {
  args: { canRecord: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.accounts.record.action'),
      }),
    ).toBeNull();
  },
};
