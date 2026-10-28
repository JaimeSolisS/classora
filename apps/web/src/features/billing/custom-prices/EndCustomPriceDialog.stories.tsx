import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { EndCustomPriceDialog } from './EndCustomPriceDialog';
import { CP_CURRENT } from './fixtures';

// "End" a custom price (#367 G2): the date from today, and the server's
// refusal (charges already use it after that date) on the field. No
// request: every call is a stub. The dialog is portalled to <body>. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/EndCustomPriceDialog',
  component: EndCustomPriceDialog,
  args: {
    item: CP_CURRENT,
    today: '2026-10-20' as LocalDate,
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/custom-prices"
      routes={[{ path: '*', element: <EndCustomPriceDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof EndCustomPriceDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Default: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.customPrices.endDialog.title'),
    });
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.customPrices.fields.expiresOn'),
      ),
    ).toHaveValue('2026-10-20');
  },
};

export const ExpiryRefused: Story = {
  args: { initialError: 'errors:custom_price_expiry_invalid' },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.customPrices.fields.expiresOn'),
      ),
    ).toHaveAttribute('aria-invalid', 'true');
  },
};
