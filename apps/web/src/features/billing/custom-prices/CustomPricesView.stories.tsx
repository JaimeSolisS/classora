import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { FAMILY_LOPEZ, searchFixtureTargets } from '../discounts/fixtures';
import { FINANCE_SESSION } from '../storySession';
import { CustomPricesView } from './CustomPricesView';
import { CUSTOM_PRICES_PAGE, EMPTY_CUSTOM_PAGE } from './fixtures';

// The custom price list without its query (#367 G2): ready with each
// status and its actions, filtered by a family, empty, loading and a
// failed load. No request: every call is a stub. axe runs on each story in
// both themes.

const meta = {
  title: 'Billing/CustomPricesView',
  component: CustomPricesView,
  args: {
    state: { status: 'ready', page: CUSTOM_PRICES_PAGE },
    filters: { target: null, status: 'open', page: 1 },
    targetName: null,
    onTarget: fn(),
    onFilters: fn(),
    onPage: fn(),
    onAdd: fn(),
    onEnd: fn(),
    onDelete: fn(),
    today: '2026-10-20' as LocalDate,
    search: (kind) => searchFixtureTargets(kind),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryQueryClient>
        <StoryRouter
          path="/billing/custom-prices"
          routes={[{ path: '*', element: <CustomPricesView {...args} /> }]}
        />
      </StoryQueryClient>
    </StoryAuth>
  ),
} satisfies Meta<typeof CustomPricesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.customPrices') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', { name: i18n.t('billing.customPrices.title') }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(i18n.t('billing.customPrices.delete')),
      }),
    ).toHaveLength(1);
  },
};

export const FilteredByFamily: Story = {
  args: {
    filters: {
      target: { kind: 'family', id: FAMILY_LOPEZ.id },
      status: 'open',
      page: 1,
    },
    targetName: FAMILY_LOPEZ.name,
  },
  play: async ({ canvas }) => {
    await expect(
      within(
        canvas.getByRole('search', {
          name: i18n.t('billing.customPrices.filters.label'),
        }),
      ).getByText(FAMILY_LOPEZ.name),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.customPrices.filters.clear'),
      }),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_CUSTOM_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.customPrices.empty')),
    ).toBeVisible();
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
        requestId: 'req-custom-prices',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
