import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { SUBJECT_OPTIONS } from '../pricing/fixtures';
import { FINANCE_SESSION } from '../storySession';
import { EMPTY_PRODUCTS_PAGE, PRODUCTS_PAGE } from './fixtures';
import { ProductsView } from './ProductsView';

// The package product list without its query (#377 G2): rows (sessions
// and hours, not offered, inactive), empty and loading. No request. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/Packages/ProductsView',
  component: ProductsView,
  args: {
    state: { status: 'ready', page: PRODUCTS_PAGE },
    filters: { isActive: null, subjectId: null, page: 1 },
    subjects: SUBJECT_OPTIONS,
    onFilters: fn(),
    onPage: fn(),
    onAdd: fn(),
    listSearch: '',
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/package-products"
        routes={[{ path: '*', element: <ProductsView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof ProductsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.packages') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.packages.nav.products'),
      }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.packages.products.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('link', {
        name: new RegExp(`^${i18n.t('billing.packages.products.view')}:`),
      }),
    ).toHaveLength(4);
  },
};

export const Filtered: Story = {
  args: {
    filters: { isActive: false, subjectId: SUBJECT_OPTIONS[0]!.id, page: 1 },
    state: { status: 'ready', page: EMPTY_PRODUCTS_PAGE },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.packages.products.emptyFiltered')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PRODUCTS_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.packages.products.empty')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' }, subjects: null },
};
