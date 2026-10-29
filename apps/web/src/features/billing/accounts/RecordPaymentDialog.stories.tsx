import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CHARGE_OVERDUE,
  CHARGE_PARTIAL,
  OPEN_CHARGES,
  PAYMENT_METHODS,
  TODAY,
} from './fixtures';
import { RecordPaymentDialog } from './RecordPaymentDialog';
import { oldestFirst } from './rules';

// "Record payment" (#379 G2): oldest first with the estimate, chosen
// charges (filled rows), keep as credit, and the checks shown after an
// empty submit. No request: every call is a stub. The dialog is portalled
// to <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/RecordPaymentDialog',
  component: RecordPaymentDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    methods: PAYMENT_METHODS,
    charges: oldestFirst(OPEN_CHARGES),
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <RecordPaymentDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof RecordPaymentDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.record.title'),
  });

export const OldestFirst: Story = {
  args: { initialValues: { amount: '2000', method: 'cash' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.record.estimateNote')),
    ).toBeVisible();
  },
};

export const ChooseCharges: Story = {
  args: {
    initialValues: {
      amount: '2500',
      method: 'transfer',
      mode: 'explicit',
      lines: { [CHARGE_OVERDUE.id]: '1500.00', [CHARGE_PARTIAL.id]: '800.00' },
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

export const KeepAsCredit: Story = {
  args: { initialValues: { amount: '500', method: 'cash', mode: 'none' } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(i18n.t('billing.accounts.record.noneHelp')),
    ).toBeVisible();
  },
};

export const Errors: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.record.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.amountRequired'),
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
