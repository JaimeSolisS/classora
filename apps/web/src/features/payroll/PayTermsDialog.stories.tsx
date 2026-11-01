import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { TODAY } from './fixtures';
import { PayTermsDialog } from './PayTermsDialog';

// "Add terms" (#425 H2): the empty form, the stop toggle and a date refusal.
// No request: every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Payroll/PayTermsDialog',
  component: PayTermsDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <PayTermsDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof PayTermsDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

const body = (element: HTMLElement) => within(element.ownerDocument.body);

export const Terms: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      body(canvasElement).getByRole('dialog', {
        name: i18n.t('payroll.termsDialog.title'),
      }),
    ).toBeVisible();
  },
};

export const TermsStop: Story = {
  play: async ({ canvasElement }) => {
    const canvas = body(canvasElement);
    await userEvent.click(
      canvas.getByRole('checkbox', {
        name: i18n.t('payroll.termsDialog.stop'),
      }),
    );
    await expect(
      canvas.getByLabelText(i18n.t('payroll.fields.frequency')),
    ).toBeDisabled();
    await expect(
      canvas.getByLabelText(i18n.t('payroll.fields.salary')),
    ).toBeDisabled();
  },
};

export const TermsRefused: Story = {
  args: {
    initialErrors: {
      effectiveFrom: 'errors:effective_from_before_first_period',
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      body(canvasElement).getByText(
        i18n.t('errors:effective_from_before_first_period'),
      ),
    ).toBeVisible();
  },
};
