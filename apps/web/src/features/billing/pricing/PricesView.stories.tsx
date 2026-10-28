import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { EMPTY_PAGE, PRICES_PAGE, PRICE_WITH_NEXT } from './fixtures';
import { PricesView } from './PricesView';

// The price list without its query (#363 F3): ready with each price state
// (current with a next change, not offered, no price yet), empty, loading
// and a failed load. No request: every call is a stub. axe runs on each
// story in both themes.

const meta = {
  title: 'Billing/PricesView',
  component: PricesView,
  args: {
    state: { status: 'ready', page: PRICES_PAGE },
    filters: { classType: null, pricingModel: null, page: 1 },
    onFilters: fn(),
    onPage: fn(),
    onAdd: fn(),
    listSearch: '',
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/prices"
        routes={[{ path: '*', element: <PricesView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof PricesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('billing.prices.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.prices') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', { name: i18n.t('billing.prices.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.prices.notOffered')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.prices.noPriceYet')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.prices.viewFor', {
          service: i18n.t('billing.prices.service', {
            classType: i18n.t('billing.prices.classTypes.individual'),
            pricingModel: i18n.t('billing.prices.pricingModels.per_session'),
          }),
          subject: i18n.t('billing.prices.subjectOfLevel', {
            level: PRICE_WITH_NEXT.subject.education_level_name,
            subject: PRICE_WITH_NEXT.subject.name,
          }),
        }),
      }),
    ).toHaveAttribute('href', `/billing/prices/${PRICE_WITH_NEXT.id}`);
  },
};

export const Filtered: Story = {
  args: { filters: { classType: 'group', pricingModel: null, page: 1 } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('billing.prices.fields.classType')),
    ).toHaveValue('group');
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.prices.empty')),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button', { name: i18n.t('billing.prices.add') }),
    ).toHaveLength(2);
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-prices',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
