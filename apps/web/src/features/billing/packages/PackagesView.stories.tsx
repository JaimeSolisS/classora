import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION, VIEWER_SESSION } from '../storySession';
import { searchFixtureStudents } from '../tuition/fixtures';
import {
  EMPTY_PACKAGES_PAGE,
  FAMILY_LOPEZ,
  PACKAGE_ACTIVE,
  PACKAGE_CANCELLED,
  PACKAGES_PAGE,
  searchFixtureFamilies,
  STUDENT_ANA,
} from './fixtures';
import { PackagesView } from './PackagesView';

// The sold package list without its query (#377 G2): rows with each
// status, filtered by a student and a family, empty, read-only
// (billing.view only) and loading. #386 F2: "Cancel" on the active row,
// and a cancelled package with the polite status after a cancellation. No request: every call is a stub. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/Packages/PackagesView',
  component: PackagesView,
  args: {
    state: { status: 'ready', page: PACKAGES_PAGE },
    filters: { statuses: [], studentId: null, familyId: null, page: 1 },
    studentName: null,
    familyName: null,
    onStudent: fn(),
    onFamily: fn(),
    onFilters: fn(),
    onPage: fn(),
    canManage: true,
    canFilterFamilies: true,
    onSell: fn(),
    listSearch: '',
    searchStudent: (q) => searchFixtureStudents(q),
    searchFamily: (q) => searchFixtureFamilies(q),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryQueryClient>
        <StoryRouter
          path="/billing/packages"
          routes={[{ path: '*', element: <PackagesView {...args} /> }]}
        />
      </StoryQueryClient>
    </StoryAuth>
  ),
} satisfies Meta<typeof PackagesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.packages.nav.sold') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.packages.sold.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.packages.sell.action'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('link', {
        name: new RegExp(`^${i18n.t('billing.packages.sold.view')}:`),
      }),
    ).toHaveLength(3);
  },
};

export const AfterCancel: Story = {
  args: {
    state: {
      status: 'ready',
      page: {
        ...PACKAGES_PAGE,
        items: [PACKAGE_CANCELLED, ...PACKAGES_PAGE.items],
      },
    },
    onCancel: fn(),
    announcement: i18n.t('billing.packages.cancel.done', {
      name: PACKAGE_ACTIVE.product.name,
      student: PACKAGE_ACTIVE.student.name,
      credit: '$1,440.00',
      after: '$0.00',
    }),
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByRole('button', {
        name: new RegExp(`^${i18n.t('billing.packages.cancel.action')} `),
      }),
    ).toHaveLength(1);
    await expect(
      canvas.getAllByText(i18n.t('billing.packages.status.cancelled')).length,
    ).toBeGreaterThan(0);
  },
};

export const Filtered: Story = {
  args: {
    filters: {
      statuses: ['active'],
      studentId: STUDENT_ANA.id,
      familyId: FAMILY_LOPEZ.id,
      page: 1,
    },
    studentName: STUDENT_ANA.name,
    familyName: FAMILY_LOPEZ.name,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.packages.sold.filters.clearFamily'),
      }),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PACKAGES_PAGE } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.packages.sold.empty')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: { canManage: false, canFilterFamilies: false },
  render: (args) => (
    <StoryAuth session={VIEWER_SESSION}>
      <StoryQueryClient>
        <StoryRouter
          path="/billing/packages"
          routes={[{ path: '*', element: <PackagesView {...args} /> }]}
        />
      </StoryQueryClient>
    </StoryAuth>
  ),
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.packages.sell.action'),
      }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('link', {
        name: i18n.t('billing.packages.nav.products'),
      }),
    ).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};
