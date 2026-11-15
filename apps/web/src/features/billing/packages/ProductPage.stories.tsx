import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { TODAY } from '../pricing/fixtures';
import { FINANCE_SESSION } from '../storySession';
import { PRODUCT_DETAIL, PRODUCT_INACTIVE_DETAIL } from './fixtures';
import { ProductPageView } from './ProductPage';

// The product page without its query (#377 G2): an active product with
// its price versions (Delete on the upcoming one), an inactive one
// (Activate), and the name dialog. No request: every call is a stub. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/Packages/ProductPage',
  component: ProductPageView,
  args: {
    product: PRODUCT_DETAIL,
    today: TODAY as LocalDate,
    onRename: fn(() => Promise.resolve()),
    onSetActive: fn(() => Promise.resolve()),
    onSchedule: fn(() => Promise.resolve()),
    onDelete: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/package-products/x"
        routes={[{ path: '*', element: <ProductPageView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof ProductPageView>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Active: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: PRODUCT_DETAIL.name }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.packages.products.fixedHint')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.packages.product.deactivate'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(i18n.t('billing.prices.versions.delete')),
      }),
    ).toHaveLength(1);
  },
};

export const Inactive: Story = {
  args: { product: PRODUCT_INACTIVE_DETAIL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.packages.product.activate'),
      }),
    ).toBeVisible();
  },
};

export const EditName: Story = {
  play: async (context) => {
    await userEvent.click(
      context.canvas.getByRole('button', {
        name: i18n.t('billing.packages.product.edit'),
      }),
    );
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.packages.product.editTitle'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.packages.fields.name')),
    ).toHaveValue(PRODUCT_DETAIL.name);
  },
};
