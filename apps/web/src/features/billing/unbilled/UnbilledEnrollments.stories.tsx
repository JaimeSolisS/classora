import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ENROLLMENT_COHORT_NO_PRICE,
  ENROLLMENT_GROUP_NO_FAMILY,
  EMPTY_UNBILLED_ENROLLMENTS_PAGE,
  UNBILLED_ENROLLMENTS_PAGE,
} from './fixtures';
import { UnbilledEnrollments } from './UnbilledEnrollments';

// The Enrollments section of the Unbilled tab without its query (#372 D2):
// rows of both kinds and both reasons with their fix links, rows without
// the links (no permission), nothing unbilled, and a failed load. No
// request: every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/UnbilledEnrollments',
  component: UnbilledEnrollments,
  args: {
    state: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: UNBILLED_ENROLLMENTS_PAGE,
    },
    filtered: false,
    onPage: fn(),
    canOpenFamilies: true,
    canManagePrices: true,
  },
  render: (args) => (
    <StoryRouter
      path="/billing/unbilled"
      routes={[{ path: '*', element: <UnbilledEnrollments {...args} /> }]}
    />
  ),
} satisfies Meta<typeof UnbilledEnrollments>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.unbilled.enrollments.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.fix.familyFor', {
          student: ENROLLMENT_GROUP_NO_FAMILY.student.name,
        }),
      }),
    ).toHaveAttribute('href', '/families');
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.enrollments.fix.priceFor', {
          name: ENROLLMENT_COHORT_NO_PRICE.cohort!.name,
        }),
      }),
    ).toHaveAttribute('href', '/billing/prices?class_type=course');
    await expect(
      canvas.getByText(i18n.t('billing.unbilled.enrollments.note')),
    ).toBeVisible();
  },
};

export const RowsWithoutLinks: Story = {
  args: { canOpenFamilies: false, canManagePrices: false },
  play: async ({ canvas }) => {
    await expect(canvas.queryAllByRole('link')).toHaveLength(0);
  },
};

export const Empty: Story = {
  args: {
    state: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: EMPTY_UNBILLED_ENROLLMENTS_PAGE,
    },
  },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvas.queryByRole('table')).toBeNull();
    await expect(
      canvasElement.querySelector('[data-unbilled-enrollments-empty="on"]'),
    ).toBeVisible();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-unbilled-enrollments',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
