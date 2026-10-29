import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { TODAY } from '../pricing/fixtures';
import { searchFixtureStudents } from '../tuition/fixtures';
import { ACTIVE_PRODUCTS, PRODUCT_SESSIONS, STUDENT_ANA } from './fixtures';
import { SellPackageDialog } from './SellPackageDialog';

// The "Sell package" dialog (#377 G2): empty (one product disabled, not
// offered today), a chosen student and product with the charge summary,
// and the products still loading. No request: every call is a stub. The
// dialog is portalled to <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Packages/SellPackageDialog',
  component: SellPackageDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    products: ACTIVE_PRODUCTS,
    canOpenStudents: true,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
    search: (q) => searchFixtureStudents(q),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        path="/billing/packages"
        routes={[{ path: '*', element: <SellPackageDialog {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof SellPackageDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Empty: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.packages.sell.title'),
    });
    const options = within(dialog).getAllByRole('option');
    await expect(options).toHaveLength(4);
    await expect(options[3]).toBeDisabled();
    await expect(
      within(dialog).getByText(i18n.t('billing.packages.sell.notConverted')),
    ).toBeVisible();
  },
};

export const Chosen: Story = {
  args: { initialStudent: STUDENT_ANA, initialProductId: PRODUCT_SESSIONS.id },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.packages.sell.title'),
    });
    await expect(
      within(dialog).getByText(
        i18n.t('billing.packages.sell.chosenStudent', {
          name: STUDENT_ANA.name,
        }),
      ),
    ).toBeVisible();
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.packages.fields.product')),
    ).toHaveValue(PRODUCT_SESSIONS.id);
  },
};

export const LoadingProducts: Story = {
  args: { products: null },
};
