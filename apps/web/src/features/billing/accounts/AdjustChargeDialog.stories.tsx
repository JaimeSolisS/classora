import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { AdjustChargeDialog } from './AdjustChargeDialog';
import { CHARGE_DETAIL } from './fixtures';

// "Adjust" a charge (#386 F2): a credit, a debit, and the checks (a credit
// above the net, no reason). No request. The dialog is portalled to
// <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/AdjustChargeDialog',
  component: AdjustChargeDialog,
  args: {
    open: true,
    charge: CHARGE_DETAIL,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <AdjustChargeDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof AdjustChargeDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.adjust.title'),
  });

export const Credit: Story = {
  args: { initialValues: { amount: '250', reason: 'Error de captura' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByRole('radio', {
        name: i18n.t('billing.accounts.adjust.directions.credit'),
      }),
    ).toBeChecked();
  },
};

export const Debit: Story = {
  args: {
    initialValues: {
      direction: 'debit',
      amount: '120',
      reason: 'Material extra',
    },
  },
};

export const Errors: Story = {
  args: { initialValues: { amount: '1500.01' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.adjust.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.adjust.aboveNet')),
    ).toBeVisible();
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.reasonRequired'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};
