import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { PAYMENT_DETAIL } from './fixtures';
import { ReversePaymentDialog } from './ReversePaymentDialog';

// "Reverse payment" (#386 F2): the effects and the mis-allocation note,
// and the reason check. No request. The dialog is portalled to <body>.
// axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/ReversePaymentDialog',
  component: ReversePaymentDialog,
  args: {
    open: true,
    payment: PAYMENT_DETAIL,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <ReversePaymentDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof ReversePaymentDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.reverse.title'),
  });

export const Default: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.reverse.misallocation'),
      ),
    ).toBeVisible();
  },
};

export const Errors: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.reverse.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.reasonRequired'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};
