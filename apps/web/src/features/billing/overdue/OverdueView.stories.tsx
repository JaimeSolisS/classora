import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { noSideScroll, VIEWPORTS } from '@/features/assignments/storyKit';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { EMPTY_OVERDUE_PAGE, LOPEZ_ID, OVERDUE_PAGE } from './fixtures';
import { OverdueView } from './OverdueView';

// The overdue accounts without their query (#382 F2): with data (the
// totals, an inactive family, a reminder and none), empty, an error, and
// at 320 px (cards, the buckets collapsed, no sideways scroll). No
// request. axe runs on each story in both themes.

const meta = {
  title: 'Billing/Overdue/OverdueView',
  component: OverdueView,
  args: {
    state: { status: 'ready', page: OVERDUE_PAGE },
    filters: { minDays: 1, familyStatus: 'all', page: 1 },
    onFilters: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/overdue"
        routes={[{ path: '*', element: <OverdueView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof OverdueView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Data: Story = {
  globals: { viewport: { value: 'desktop1280' } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.overdue') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', { name: i18n.t('billing.overdue.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: 'Familia López' }),
    ).toHaveAttribute('href', `/billing/accounts/${LOPEZ_ID}?status=overdue`);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_OVERDUE_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.overdue.empty')),
    ).toBeVisible();
  },
};

export const Failed: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-overdue',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvas.queryByRole('table')).toBeNull();
    const cards = [
      ...canvasElement.querySelectorAll<HTMLElement>('[data-overdue-card]'),
    ];
    await expect(cards).toHaveLength(OVERDUE_PAGE.items.length);
    await expect(cards[0]).toBeVisible();
    const aging = within(cards[0]!).getByText(i18n.t('billing.overdue.aging'));
    await expect(aging.closest('details')).not.toHaveAttribute('open');
    await noSideScroll(canvasElement);
  },
};
