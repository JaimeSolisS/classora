import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { noSideScroll, VIEWPORTS } from '@/features/assignments/storyKit';
import { i18n } from '@/i18n';
import { formatMoney } from '@/lib/money';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { CHARGE_MOVED } from '../due-dates/fixtures';
import { AccountPageView } from './AccountPageView';
import {
  CHARGE_PAID,
  CHARGE_PENDING,
  CHARGES_PAGE,
  EMPTY_CHARGES_PAGE,
  EMPTY_PAYMENTS_PAGE,
  FAMILY_ID,
  LEDGER_ENTRIES,
  PAYMENTS_PAGE,
  RECORDED_RECEIPT,
  SUMMARY,
  SUMMARY_NO_ACCOUNT,
} from './fixtures';

// A family's billing account without its queries (#379 G2): with balances
// (credit, charges in every status, history with "Load more", payments)
// and with no account yet (zeros, empty lists, no credit button). No
// request. axe runs on each story in both themes. #563: the void (0.00)
// charges hidden with a count (also at 320 px), shown with the toggle, and
// an account whose charges are all void.

const meta = {
  title: 'Billing/Accounts/AccountPageView',
  component: AccountPageView,
  args: {
    summary: SUMMARY,
    view: {
      studentId: null,
      statuses: [],
      showVoid: false,
      page: 1,
      paymentsPage: 1,
    },
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
  parameters: { viewport: { options: VIEWPORTS } },
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

// #418: a reversed payment shows "—" under "Not applied", with hidden
// text for screen readers, never its unapplied amount as credit.
export const ReversedPayment: Story = {
  args: {
    payments: {
      status: 'ready',
      page: {
        ...PAYMENTS_PAGE,
        items: [
          {
            ...PAYMENTS_PAGE.items[0]!,
            is_reversed: true,
            reversed_at: '2026-10-15T17:00:00Z',
          },
          ...PAYMENTS_PAGE.items.slice(1),
        ],
      },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(
        i18n.t('billing.accounts.payments.notApplicableReversed'),
      ),
    ).toBeInTheDocument();
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

// #382: billing.manage holders get the offset's "Edit" and "Change due
// date" on open charges; a moved due date carries the "changed" marker.
export const ManageDueDates: Story = {
  args: {
    canManage: true,
    onEditOffset: fn(),
    onChangeDueDate: fn(),
    charges: {
      status: 'ready',
      page: {
        ...CHARGES_PAGE,
        items: [CHARGE_PENDING, CHARGE_MOVED, CHARGE_PAID],
        total: 3,
      },
    },
  },
  play: async ({ canvas, canvasElement }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.dueDates.offset.editLabel'),
      }),
    ).toBeVisible();
    const markers = canvasElement.querySelectorAll('[data-due-date-changed]');
    await expect(markers).toHaveLength(1);
    await expect(markers[0]).toBeVisible();
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(`^${i18n.t('billing.dueDates.change.action')}: `),
      }),
    ).toHaveLength(2);
  },
};

// #98 H1: each payment row shows its receipt (number and "Download
// receipt"), or "—" for a payment recorded before receipts; after recording,
// the new receipt follows the polite status.
export const Receipts: Story = {
  args: {
    announcement: 'Payment recorded.',
    recordedReceipt: RECORDED_RECEIPT,
  },
  play: async ({ canvas }) => {
    const links = canvas.getAllByRole('link', {
      name: new RegExp(
        `^${i18n.t('billing.accounts.receipt.downloadFor', { number: 'R-' })}`,
      ),
    });
    await expect(links).toHaveLength(2);
    await expect(links[0]).toHaveAttribute(
      'href',
      `/api/v1/families/${FAMILY_ID}/billing-account/receipts/${RECORDED_RECEIPT.id}/pdf`,
    );
    await expect(canvas.getByText(RECORDED_RECEIPT.number)).toBeVisible();
  },
};

// #563: the void charges hidden (the default), with the count and "Show".
const ZERO = formatMoney('0.00');
const CHARGE_VOID = {
  ...CHARGE_PENDING,
  id: '53000000-0000-4000-8000-000000000009',
  list_amount: '0.00',
  amount: '0.00',
  net: '0.00',
  open: '0.00',
  status: 'void' as const,
};
const toggleName = () =>
  i18n.t('billing.accounts.charges.showVoid', { amount: ZERO });

export const VoidHidden: Story = {
  args: { hiddenCount: 52 },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('checkbox', { name: toggleName() }),
    ).not.toBeChecked();
    await expect(
      canvas.getByText(
        i18n.t('billing.accounts.charges.hidden', { count: 52, amount: ZERO }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.accounts.charges.showHidden'),
      }),
    ).toBeVisible();
  },
};

// At 320 px: no sideways scroll, and the toggle and "Show" are 44 px tall.
// The lists are empty: below `md` an overflowing table names its scroll
// region like its section, a landmark-unique finding on this page that
// predates #563 (Charges, History and Payments alike; a follow-up).
export const VoidHiddenPhone: Story = {
  ...VoidHidden,
  args: {
    hiddenCount: 52,
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
  globals: { viewport: { value: 'narrow320' } },
  play: async (context) => {
    await VoidHidden.play?.(context);
    const { canvas, canvasElement } = context;
    await noSideScroll(canvasElement);
    const label = canvas
      .getByRole('checkbox', { name: toggleName() })
      .closest('label')!;
    await expect(label.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
    const show = canvas.getByRole('button', {
      name: i18n.t('billing.accounts.charges.showHidden'),
    });
    await expect(show.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
  },
};

export const VoidShown: Story = {
  args: {
    view: {
      studentId: null,
      statuses: [],
      showVoid: true,
      page: 1,
      paymentsPage: 1,
    },
    charges: {
      status: 'ready',
      page: {
        ...CHARGES_PAGE,
        items: [CHARGE_VOID, ...CHARGES_PAGE.items],
        total: CHARGES_PAGE.total + 1,
      },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('checkbox', { name: toggleName() }),
    ).toBeChecked();
    await expect(
      canvas.getByText(i18n.t('billing.tuition.chargeStatus.void')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.accounts.charges.showHidden'),
      }),
    ).toBeNull();
  },
};

export const AllVoid: Story = {
  args: {
    hiddenCount: 5,
    charges: { status: 'ready', page: EMPTY_CHARGES_PAGE },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.accounts.charges.noneShown')),
    ).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('billing.accounts.charges.hidden', { count: 5, amount: ZERO }),
      ),
    ).toBeVisible();
    await expect(
      canvas.queryByText(i18n.t('billing.accounts.charges.empty')),
    ).toBeNull();
  },
};
