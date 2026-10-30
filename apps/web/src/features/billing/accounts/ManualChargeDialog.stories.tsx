import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { ANA, LUIS, TODAY } from './fixtures';
import { ManualChargeDialog } from './ManualChargeDialog';

// "Add charge" (#386 F2): the default form and the checks after an empty
// submit, plus a server error on the student. No request. The dialog is
// portalled to <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Accounts/ManualChargeDialog',
  component: ManualChargeDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    students: [ANA, LUIS],
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <ManualChargeDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof ManualChargeDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.accounts.manualCharge.title'),
  });

export const Default: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.accounts.manualCharge.fields.serviceDate'),
      ),
    ).toHaveValue(TODAY);
  },
};

export const Errors: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.accounts.manualCharge.submit'),
      }),
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.studentRequired'),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).getByText(
        i18n.t('billing.accounts.validation.descriptionRequired'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};

export const StudentNotInFamily: Story = {
  args: {
    initialValues: {
      studentId: LUIS.id,
      description: 'Material de laboratorio',
      amount: '350',
    },
    initialErrors: { studentId: 'errors:student_not_in_family' },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      within(dialog).getByText(i18n.t('errors:student_not_in_family')),
    ).toBeVisible();
  },
};
