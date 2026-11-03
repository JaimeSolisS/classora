import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { TUTOR_ID } from './fixtures';
import { EMPTY_PERIODS, PERIODS_PAGE } from './periodFixtures';
import { PeriodsView, type PeriodsViewProps } from './PeriodsView';
import { FINANCE_SESSION } from './storySession';

// The period list (#110b H2): the table, the cards at 320 px, a left tutor, a
// negative and a too-large total, a refresh that failed, the empty, filtered,
// payroll-off and reviewer states, loading and error. No request. axe runs on
// each story in both themes.

const ready = (page = PERIODS_PAGE) =>
  ({ status: 'ready', page, refreshError: null, onRefresh: fn() }) as const;

const args: PeriodsViewProps = {
  state: ready(),
  payroll: { status: 'ready', off: false },
  filters: { status: null, tutor: null, page: 1 },
  onFilters: fn(),
  onPage: fn(),
  searchTutors: () => Promise.resolve([]),
  tutorName: null,
  onTutorName: fn(),
  reviewerOnly: false,
  canOpenSettings: true,
};

const meta = {
  title: 'Payroll/PeriodsView',
  component: PeriodsView,
  args,

  render: (args) => (
    <StoryQueryClient>
      <StoryAuth session={FINANCE_SESSION}>
        <StoryRouter
          path="/payroll/periods"
          routes={[
            {
              path: '*',
              element: (
                <main>
                  <PeriodsView {...args} />
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
} satisfies Meta<typeof PeriodsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Table: Story = {
  play: async ({ canvas }) => {
    const table = canvas.getByRole('table', {
      name: i18n.t('payroll.periods.caption'),
    });
    await expect(table).toBeVisible();
    await expect(table.textContent).toContain('-$150.00');
    await expect(table.textContent).toContain(
      i18n.t('payroll.periods.tooLarge'),
    );
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: {
    filters: { status: 'open', tutor: TUTOR_ID, page: 1 },
    tutorName: 'Ana Tutora',
  },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    const cards = main.querySelector('[data-periods-cards]')!;
    await expect(cards.getBoundingClientRect().height).toBeGreaterThan(0);
    const targets = main.querySelectorAll<HTMLElement>(
      'main button, [data-periods-cards] a, input, select',
    );
    for (const target of targets) {
      await expect(
        target.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

export const MayBeOutOfDate: Story = {
  args: {
    state: { ...ready(), refreshError: new Error('x') },
  },
};

export const Empty: Story = {
  args: { state: ready(EMPTY_PERIODS) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.periods.empty')),
    ).toBeVisible();
  },
};

export const EmptyFiltered: Story = {
  args: {
    state: ready(EMPTY_PERIODS),
    filters: { status: 'prepared', tutor: null, page: 1 },
  },
};

export const PayrollOff: Story = {
  args: {
    state: ready(EMPTY_PERIODS),
    payroll: { status: 'ready', off: true },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('payroll.periods.payrollOffLink'),
      }),
    ).toHaveAttribute('href', '/payroll/settings');
  },
};

/** A delegated Administrator: no picker, no settings, nothing waiting. */
export const Reviewer: Story = {
  args: {
    state: ready(EMPTY_PERIODS),
    payroll: null,
    searchTutors: null,
    reviewerOnly: true,
    canOpenSettings: false,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.periods.emptyReviewer')),
    ).toBeVisible();
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

export const LoadError: Story = {
  args: {
    state: { status: 'error', error: new Error('x'), onRetry: fn() },
  },
};
