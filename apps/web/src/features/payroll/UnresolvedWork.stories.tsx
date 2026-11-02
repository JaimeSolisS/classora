import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import {
  UNRESOLVED_BEFORE_FIRST,
  UNRESOLVED_NO_RATE,
  UNRESOLVED_NO_TERMS,
} from './fixtures';
import { FINANCE_SESSION } from './storySession';
import { UnresolvedWorkView } from './UnresolvedWorkView';

// The Unresolved work tab (#444 D2): the table, the cards at 320 px, "Load
// more", no session link, the empty, filtered, payroll-off, loading and error
// states. No request. axe runs on each story in both themes.

const items = [
  UNRESOLVED_NO_RATE,
  UNRESOLVED_NO_TERMS,
  UNRESOLVED_BEFORE_FIRST,
];
const ready = {
  status: 'ready',
  items,
  hasMore: false,
  loadingMore: false,
  moreError: null,
  onMore: fn(),
} as const;

const meta = {
  title: 'Payroll/UnresolvedWorkView',
  component: UnresolvedWorkView,
  args: {
    state: ready,
    payroll: { status: 'ready', off: false },
    filters: { tutor: null, reason: null },
    onFilters: fn(),
    searchTutors: () => Promise.resolve([]),
    tutorName: null,
    onTutorName: fn(),
    canOpenSession: true,
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryAuth session={FINANCE_SESSION}>
        <StoryRouter
          path="/payroll/unresolved"
          routes={[
            {
              path: '*',
              element: (
                <main>
                  <UnresolvedWorkView {...args} />
                </main>
              ),
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  ),
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
} satisfies Meta<typeof UnresolvedWorkView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Table: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('payroll.unresolved.caption'),
      }),
    ).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: {
    state: { ...ready, hasMore: true },
    filters: { tutor: UNRESOLVED_NO_RATE.tutor_user_id, reason: 'no_rate' },
    tutorName: UNRESOLVED_NO_RATE.tutor_display_name,
  },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    const cards = main.querySelector('[data-unresolved-cards]')!;
    await expect(cards.getBoundingClientRect().height).toBeGreaterThan(0);
    const targets = main.querySelectorAll<HTMLElement>(
      'main button, [data-unresolved-cards] a, input, select',
    );
    for (const target of targets) {
      await expect(
        target.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

export const LoadMore: Story = { args: { state: { ...ready, hasMore: true } } };

export const NextPageFailed: Story = {
  args: {
    state: { ...ready, hasMore: true, moreError: new Error('x') },
  },
};

export const NoSessionLink: Story = {
  args: { canOpenSession: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('columnheader', {
        name: i18n.t('payroll.unresolved.columns.session'),
      }),
    ).toBeNull();
    await expect(
      canvas.queryByText(i18n.t('payroll.unresolved.open')),
    ).toBeNull();
  },
};

export const Empty: Story = {
  args: { state: { ...ready, items: [] } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.unresolved.empty')),
    ).toBeVisible();
  },
};

export const EmptyFiltered: Story = {
  args: {
    state: { ...ready, items: [] },
    filters: { tutor: null, reason: 'no_rate' },
  },
};

export const PayrollOff: Story = {
  args: {
    state: { ...ready, items: [] },
    payroll: { status: 'ready', off: true },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('payroll.unresolved.payrollOffLink'),
      }),
    ).toHaveAttribute('href', '/payroll/settings');
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

export const LoadError: Story = {
  args: {
    state: { status: 'error', error: new Error('x'), onRetry: fn() },
  },
};
