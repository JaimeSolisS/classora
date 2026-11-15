import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import {
  EMPTY_PLANS_PAGE,
  PLANS_PAGE,
  searchFixtureStudents,
  STUDENT_ANA,
} from './fixtures';
import { TuitionPlansView } from './TuitionPlansView';

// The tuition plan list without its query (#374 H2): rows with each status,
// filtered by a student, read-only (billing.view only), empty and loading.
// No request: every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/TuitionPlansView',
  component: TuitionPlansView,
  args: {
    state: { status: 'ready', page: PLANS_PAGE },
    filters: { statuses: [], studentId: null, page: 1 },
    studentName: null,
    onStudent: fn(),
    onFilters: fn(),
    onPage: fn(),
    canManage: true,
    onAdd: fn(),
    listSearch: '',
    search: (q) => searchFixtureStudents(q),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryQueryClient>
        <StoryRouter
          path="/billing/tuition-plans"
          routes={[{ path: '*', element: <TuitionPlansView {...args} /> }]}
        />
      </StoryQueryClient>
    </StoryAuth>
  ),
} satisfies Meta<typeof TuitionPlansView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.tuitionPlans') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', { name: i18n.t('billing.tuition.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('billing.tuition.add') }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('link', {
        name: new RegExp(`^${i18n.t('billing.tuition.view')}:`),
      }),
    ).toHaveLength(3);
  },
};

export const FilteredByStudent: Story = {
  args: {
    filters: {
      statuses: ['active', 'paused'],
      studentId: STUDENT_ANA.id,
      page: 1,
    },
    studentName: STUDENT_ANA.name,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(STUDENT_ANA.name, { selector: 'p' }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('checkbox', {
        name: i18n.t('billing.tuition.status.active'),
      }),
    ).toBeChecked();
  },
};

export const ReadOnly: Story = {
  args: { canManage: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: i18n.t('billing.tuition.add') }),
    ).toBeNull();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PLANS_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.tuition.empty')),
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
