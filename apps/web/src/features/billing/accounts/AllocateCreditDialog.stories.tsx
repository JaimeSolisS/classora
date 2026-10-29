import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { AllocateCreditDialog } from './AllocateCreditDialog';
import { CHARGE_OVERDUE, OPEN_CHARGES } from './fixtures';
import { oldestFirst } from './rules';

// "Apply account credit" (#379 G2): oldest first (the optional amount) and
// chosen charges capped by the credit. No request. The dialog is
// portalled to <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/AllocateCreditDialog',
  component: AllocateCreditDialog,
  args: {
    open: true,
    credit: '300.00',
    charges: oldestFirst(OPEN_CHARGES),
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <AllocateCreditDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof AllocateCreditDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.credit.title'),
  });

export const OldestFirst: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.accounts.credit.fields.amount'),
      ),
    ).toBeVisible();
  },
};

export const ChooseCharges: Story = {
  args: {
    initialValues: {
      mode: 'explicit',
      lines: { [CHARGE_OVERDUE.id]: '300.00' },
    },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByRole('table', {
        name: i18n.t('billing.accounts.editor.label'),
      }),
    ).toBeVisible();
  },
};
