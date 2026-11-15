import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { PAYMENT_DETAIL, PAYMENT_METHODS, TODAY } from './fixtures';
import { RefundDialog } from './RefundDialog';

// "Refund" (#386 F2): the defaults (the unallocated amount, today), the
// checks after a submit above the limits, a server error on the date, and
// the methods loading. No request. The dialog is portalled to <body>. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/RefundDialog',
  component: RefundDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    payment: PAYMENT_DETAIL,
    methods: PAYMENT_METHODS,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <RefundDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof RefundDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.refund.title'),
  });

export const Default: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.accounts.refund.fields.amount'),
      ),
    ).toHaveValue('300.00');
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.refund.final')),
    ).toBeVisible();
  },
};

export const Errors: Story = {
  args: { initialValues: { amount: '300.01' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.refund.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.refund.aboveUnallocated'),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.methodRequired'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};

export const DateInFuture: Story = {
  args: {
    initialValues: { method: 'cash', reason: 'Pago de más' },
    initialErrors: { refundedOn: 'errors:refunded_on_in_future' },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(i18n.t('errors:refunded_on_in_future')),
    ).toBeVisible();
  },
};

export const MethodsLoading: Story = { args: { methods: null } };
