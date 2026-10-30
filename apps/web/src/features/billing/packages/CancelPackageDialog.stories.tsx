import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CancelPackageDialog } from './CancelPackageDialog';
import { PACKAGE_ACTIVE } from './fixtures';

// "Cancel" a sold package (#386 F2): the units left and the proposed
// credit from the purchase charge's net, the checks (a credit above the
// net, no reason), a package without a purchase charge, and the charge
// loading. No request. The dialog is portalled to <body>. axe runs on each
// story in both themes.

const meta = {
  title: 'Billing/Packages/CancelPackageDialog',
  component: CancelPackageDialog,
  args: {
    open: true,
    pkg: PACKAGE_ACTIVE,
    charge: { status: 'ready', net: '1800.00' },
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/packages"
      routes={[{ path: '*', element: <CancelPackageDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof CancelPackageDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.packages.cancel.title'),
  });

export const Default: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(
        i18n.t('billing.packages.cancel.estimate', { amount: '$1,440.00' }),
      ),
    ).toBeVisible();
  },
};

export const Errors: Story = {
  args: { initialValues: { credit: '1800.01' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.packages.cancel.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.packages.cancel.creditAboveNet'),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.reasonRequired'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};

export const NoPurchaseCharge: Story = {
  args: { charge: { status: 'ready', net: null } },
};

export const Loading: Story = { args: { charge: { status: 'loading' } } };
