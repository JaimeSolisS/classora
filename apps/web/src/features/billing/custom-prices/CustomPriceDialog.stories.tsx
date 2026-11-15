import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { FAMILY_LOPEZ, searchFixtureTargets } from '../discounts/fixtures';
import { PRICES_PAGE, PRICE_WITH_NEXT } from '../pricing/fixtures';
import { CustomPriceDialog } from './CustomPriceDialog';

// "Add custom price" (#367 G2): empty, a family preselected (a deep link),
// and the 409 overlap shown inside the dialog. No request: every call is a
// stub. The dialog is portalled to <body>. axe runs on each story in both
// themes.

const TODAY = '2026-10-20' as LocalDate;

const meta = {
  title: 'Billing/CustomPriceDialog',
  component: CustomPriceDialog,
  args: {
    open: true,
    today: TODAY,
    catalog: PRICES_PAGE.items,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
    search: (kind) => searchFixtureTargets(kind),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        path="/billing/custom-prices"
        routes={[{ path: '*', element: <CustomPriceDialog {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof CustomPriceDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Empty: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.customPrices.add'),
    });
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.customPrices.fields.effectiveFrom'),
      ),
    ).toHaveValue(TODAY);
  },
};

export const FamilyPreselected: Story = {
  args: { initialTarget: FAMILY_LOPEZ },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByRole('radio', {
        name: i18n.t('billing.targets.kinds.family'),
      }),
    ).toBeChecked();
    await expect(
      within(dialog).getByText(
        i18n.t('billing.targets.chosen', { name: FAMILY_LOPEZ.name }),
      ),
    ).toBeVisible();
  },
};

export const Overlap: Story = {
  args: {
    initialValues: {
      target: FAMILY_LOPEZ,
      servicePriceId: PRICE_WITH_NEXT.id,
      amount: '300.00',
      effectiveFrom: '2026-10-21',
      expiresOn: '',
    },
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'custom_price_overlap',
          status: 409,
          requestId: 'req-overlap',
        }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.prices.save'),
      }),
    );
    await expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      i18n.t('billing.customPrices.overlap.family'),
    );
  },
};
