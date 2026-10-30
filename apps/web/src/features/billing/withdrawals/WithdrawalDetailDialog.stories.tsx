import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { SUMMARY } from '../accounts/fixtures';
import {
  EMPTY_WITHDRAWALS_PAGE,
  WITHDRAWAL_DETAIL,
  WITHDRAWAL_ITEM,
  WITHDRAWAL_REFUND_DETAIL,
  WITHDRAWALS_PAGE,
} from './fixtures';
import { WithdrawalDetailDialog } from './WithdrawalDetailDialog';
import { WithdrawalsSection } from './WithdrawalsSection';

// The account page's Withdrawals section and a withdrawal's drawer (#387
// B4, D2): the list, the empty list, the drawer of an account-credit
// package withdrawal confirmed by the viewer, and of a refunded group
// withdrawal by someone else (with automatic credits). No request. The
// drawer is portalled to <body>. Axe runs on each story in both themes.

const meta = {
  title: 'Billing/Withdrawals/WithdrawalDetailDialog',
  component: WithdrawalDetailDialog,
  args: {
    open: true,
    state: { status: 'ready', withdrawal: WITHDRAWAL_DETAIL },
    currentUserId: WITHDRAWAL_ITEM.confirmed_by_user_id,
    onViewCharge: fn(),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <WithdrawalDetailDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof WithdrawalDetailDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AccountCredit: Story = {
  play: async ({ canvasElement, args, userEvent }) => {
    const dialog = within(
      await within(canvasElement.ownerDocument.body).findByRole('dialog'),
    );
    await expect(dialog.getByText(WITHDRAWAL_ITEM.reason)).toBeVisible();
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('billing.withdrawals.detail.viewChargeFor', {
          kind: i18n.t('billing.accounts.chargeKinds.package_purchase'),
        }),
      }),
    );
    await expect(args.onViewCharge).toHaveBeenCalled();
  },
};

export const RefundByAnother: Story = {
  args: { state: { status: 'ready', withdrawal: WITHDRAWAL_REFUND_DETAIL } },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

const studentNames = Object.fromEntries(
  SUMMARY.students.map((item) => [item.student.id, item.student.name]),
);

type SectionStory = StoryObj<typeof WithdrawalsSection>;

function section(state: Parameters<typeof WithdrawalsSection>[0]['state']) {
  return (
    <main className="p-4">
      <WithdrawalsSection
        state={state}
        studentNames={studentNames}
        onPage={fn()}
        onOpen={fn()}
      />
    </main>
  );
}

export const Section: SectionStory = {
  render: () => section({ status: 'ready', page: WITHDRAWALS_PAGE }),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getAllByRole('button', {
        name: new RegExp(`^${i18n.t('billing.withdrawals.section.view')} `),
      }),
    ).toHaveLength(2);
  },
};

export const SectionEmpty: SectionStory = {
  render: () => section({ status: 'ready', page: EMPTY_WITHDRAWALS_PAGE }),
};
