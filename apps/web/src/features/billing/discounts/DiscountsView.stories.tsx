import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { DISCOUNTS_PAGE, EMPTY_PAGE, SCHOLARSHIP } from './fixtures';
import { DiscountsView } from './DiscountsView';

// The discount list without its query (#367 G2): ready with each kind,
// value type and status, empty, loading and a failed load. No request:
// every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/DiscountsView',
  component: DiscountsView,
  args: {
    state: { status: 'ready', page: DISCOUNTS_PAGE },
    filters: { kind: null, status: 'open', q: '', page: 1 },
    onFilters: fn(),
    onPage: fn(),
    onAdd: fn(),
    listSearch: '',
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/discounts"
        routes={[{ path: '*', element: <DiscountsView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof DiscountsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('billing.discounts.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.discounts') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', { name: i18n.t('billing.discounts.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.discounts.viewFor', { name: SCHOLARSHIP.name }),
      }),
    ).toHaveAttribute('href', `/billing/discounts/${SCHOLARSHIP.id}`);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.discounts.empty')),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('button', { name: i18n.t('billing.discounts.add') }),
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
        requestId: 'req-discounts',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
