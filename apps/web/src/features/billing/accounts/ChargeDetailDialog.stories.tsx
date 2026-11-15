import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { ChargeDetailDialog } from './ChargeDetailDialog';
import { CHARGE_DETAIL, CHARGE_PACKAGE_DETAIL } from './fixtures';

// A charge's detail (#386 F2): amounts, an adjustment with its reason and
// a released allocation, with "Adjust" for billing.manage; a package
// purchase (no "Adjust", the hint instead); read-only; loading. No
// request. The dialog is portalled to <body>. axe runs on each story in
// both themes.

const meta = {
  title: 'Billing/Accounts/ChargeDetailDialog',
  component: ChargeDetailDialog,
  args: {
    open: true,
    state: { status: 'ready', charge: CHARGE_DETAIL },
    canManage: true,
    onAdjust: fn(),
    onViewPayment: fn(),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <ChargeDetailDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof ChargeDetailDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.chargeDetail.title'),
  });

export const Ready: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText('Clase cancelada por el centro'),
    ).toBeVisible();
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.adjust.action'),
      }),
    ).toBeVisible();
  },
};

export const Adjusted: Story = {
  args: {
    notice: i18n.t('billing.accounts.adjust.done', {
      amount: '−$250.00',
      net: '$1,250.00',
    }),
  },
};

export const PackagePurchase: Story = {
  args: { state: { status: 'ready', charge: CHARGE_PACKAGE_DETAIL } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.chargeDetail.packageHint'),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).queryByRole('button', {
        name: i18n.t('billing.accounts.adjust.action'),
      }),
    ).toBeNull();
  },
};

export const ReadOnly: Story = { args: { canManage: false } };

export const Loading: Story = { args: { state: { status: 'loading' } } };
