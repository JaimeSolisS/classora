import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ANA,
  CHARGE_PAID,
  CHARGE_PARTIAL,
  LUIS,
  PAYMENT_DETAIL,
  PAYMENT_REFUNDED_DETAIL,
  PAYMENT_REVERSED_DETAIL,
} from './fixtures';
import { PaymentDetailDialog } from './PaymentDetailDialog';

// A payment's detail (#379 G2): its fields and allocations, one released.
// #386 F2: with "Reverse payment" and "Refund" (finance.refund.approve),
// reversed (the reversal and its reason), and refunded (the refunds; no
// reversal, and why).
// No request. The dialog is portalled to <body>. axe runs on each story in
// both themes.

const meta = {
  title: 'Billing/Accounts/PaymentDetailDialog',
  component: PaymentDetailDialog,
  args: {
    open: true,
    state: {
      status: 'ready',
      payment: PAYMENT_DETAIL,
      charges: {
        [CHARGE_PARTIAL.id]: CHARGE_PARTIAL,
        [CHARGE_PAID.id]: CHARGE_PAID,
      },
    },
    studentNames: { [ANA.id]: ANA.name, [LUIS.id]: LUIS.name },
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <PaymentDetailDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof PaymentDetailDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {
  play: async (context) => {
    const dialog = await within(
      context.canvasElement.ownerDocument.body,
    ).findByRole('dialog', {
      name: i18n.t('billing.accounts.payments.detailTitle'),
    });
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.payments.released')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

const charges = {
  [CHARGE_PARTIAL.id]: CHARGE_PARTIAL,
  [CHARGE_PAID.id]: CHARGE_PAID,
};

export const WithActions: Story = {
  args: { canApproveRefunds: true, onReverse: fn(), onRefund: fn() },
  play: async (context) => {
    const dialog = await within(
      context.canvasElement.ownerDocument.body,
    ).findByRole('dialog', {
      name: i18n.t('billing.accounts.payments.detailTitle'),
    });
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.reverse.action'),
      }),
    ).toBeVisible();
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.refund.action'),
      }),
    ).toBeVisible();
  },
};

export const Reversed: Story = {
  args: {
    canApproveRefunds: true,
    state: { status: 'ready', payment: PAYMENT_REVERSED_DETAIL, charges },
  },
  play: async (context) => {
    const dialog = await within(
      context.canvasElement.ownerDocument.body,
    ).findByRole('dialog', {
      name: i18n.t('billing.accounts.payments.detailTitle'),
    });
    await expect(
      within(dialog).getByText('Transferencia rechazada por el banco'),
    ).toBeVisible();
  },
};

export const Refunded: Story = {
  args: {
    canApproveRefunds: true,
    state: { status: 'ready', payment: PAYMENT_REFUNDED_DETAIL, charges },
  },
  play: async (context) => {
    const dialog = await within(
      context.canvasElement.ownerDocument.body,
    ).findByRole('dialog', {
      name: i18n.t('billing.accounts.payments.detailTitle'),
    });
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.reverse.hasRefunds')),
    ).toBeVisible();
  },
};
