import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { SETTINGS_OFF, SETTINGS_ON } from '../settings/fixtures';
import { FINANCE_SESSION } from '../storySession';
import {
  EMPTY_UNBILLED_ENROLLMENTS_PAGE,
  EMPTY_UNBILLED_PAGE,
  ITEM_GROUP,
  ITEM_NO_FAMILY,
  ITEM_NO_PRICE,
  EMPTY_UNBILLED_TUITION_PAGE,
  UNBILLED_ENROLLMENTS_PAGE,
  UNBILLED_PAGE,
  UNBILLED_TUITION_PAGE,
} from './fixtures';
import { UnbilledView } from './UnbilledView';

// The Unbilled tab without its queries (#366 E2, #372 D2): session rows
// with both reasons, the Class column and their fix links above the
// Enrollments and Tuition (#374 H2) sections, rows without the links (no
// permission), billing off (the Settings link, no Enrollments or Tuition
// section), billing on with nothing
// unbilled, loading and a failed load. No request: every call is a stub.
// axe runs on each story in both themes.

const meta = {
  title: 'Billing/UnbilledView',
  component: UnbilledView,
  args: {
    state: { status: 'ready', settings: SETTINGS_ON, page: UNBILLED_PAGE },
    enrollments: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: UNBILLED_ENROLLMENTS_PAGE,
    },
    tuition: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: UNBILLED_TUITION_PAGE,
    },
    filters: { reason: null, page: 1, enrollmentPage: 1, tuitionPage: 1 },
    onFilters: fn(),
    onPage: fn(),
    onEnrollmentPage: fn(),
    onTuitionPage: fn(),
    canOpenFamilies: true,
    canManagePrices: true,
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/unbilled"
        routes={[{ path: '*', element: <UnbilledView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof UnbilledView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.unbilled.sessions.title'),
      }),
    ).toBeVisible();
    await expect(canvas.getByText(ITEM_GROUP.group!.name)).toBeVisible();
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.unbilled.enrollments.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.unbilled') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.fix.familyFor', {
          student: ITEM_NO_FAMILY.student.name,
        }),
      }),
    ).toHaveAttribute('href', '/families');
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.fix.priceFor', {
          subject: ITEM_NO_PRICE.subject.name,
        }),
      }),
    ).toHaveAttribute('href', '/billing/prices');
  },
};

export const RowsWithoutLinks: Story = {
  args: { canOpenFamilies: false, canManagePrices: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('link', {
        name: i18n.t('billing.unbilled.fix.family'),
      }),
    ).toBeNull();
    await expect(
      canvas.getAllByText(i18n.t('billing.unbilled.noFamily'))[0],
    ).toBeVisible();
  },
};

export const EmptyBillingOff: Story = {
  args: {
    state: {
      status: 'ready',
      settings: SETTINGS_OFF,
      page: EMPTY_UNBILLED_PAGE,
    },
    enrollments: { status: 'hidden' },
    tuition: { status: 'hidden' },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('heading', {
        name: i18n.t('billing.unbilled.enrollments.title'),
      }),
    ).toBeNull();
    await expect(
      canvas.getByText(i18n.t('billing.unbilled.emptyOff')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.openSettings'),
      }),
    ).toHaveAttribute('href', '/billing/settings');
  },
};

export const EmptyBillingOn: Story = {
  args: {
    state: {
      status: 'ready',
      settings: SETTINGS_ON,
      page: EMPTY_UNBILLED_PAGE,
    },
    enrollments: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: EMPTY_UNBILLED_ENROLLMENTS_PAGE,
    },
    tuition: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: EMPTY_UNBILLED_TUITION_PAGE,
    },
  },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvas.queryByRole('table')).toBeNull();
    await expect(
      canvasElement.querySelector('[data-unbilled-empty="on"]'),
    ).toBeVisible();
    await expect(
      canvasElement.querySelector('[data-unbilled-enrollments-empty="on"]'),
    ).toBeVisible();
    await expect(
      canvasElement.querySelector('[data-unbilled-tuition-empty="on"]'),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: {
    state: { status: 'loading' },
    enrollments: { status: 'loading' },
    tuition: { status: 'loading' },
  },
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
        requestId: 'req-unbilled',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
