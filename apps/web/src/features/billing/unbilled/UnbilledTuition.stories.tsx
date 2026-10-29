import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  EMPTY_UNBILLED_TUITION_PAGE,
  TUITION_FAMILY_CHANGED,
  TUITION_NO_PRICE,
  UNBILLED_TUITION_PAGE,
} from './fixtures';
import { UnbilledTuitionSection } from './UnbilledTuition';

// The Tuition section of the Unbilled tab without its query (#374 H2):
// rows with both reasons and their fix links, rows without the price link
// (no billing.manage), nothing unbilled, and a failed load. No request:
// every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/UnbilledTuition',
  component: UnbilledTuitionSection,
  args: {
    state: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: UNBILLED_TUITION_PAGE,
    },
    filtered: false,
    onPage: fn(),
    canManagePrices: true,
  },
  render: (args) => (
    <StoryRouter
      path="/billing/unbilled"
      routes={[{ path: '*', element: <UnbilledTuitionSection {...args} /> }]}
    />
  ),
} satisfies Meta<typeof UnbilledTuitionSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.unbilled.tuition.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.tuition.fix.priceFor', {
          subject: TUITION_NO_PRICE.subject.name,
        }),
      }),
    ).toHaveAttribute(
      'href',
      '/billing/prices?class_type=individual&pricing_model=per_period',
    );
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('billing.unbilled.tuition.fix.planFor', {
          student: TUITION_FAMILY_CHANGED.student.name,
        }),
      }),
    ).toHaveAttribute(
      'href',
      `/billing/tuition-plans/${TUITION_FAMILY_CHANGED.tuition_plan_id}`,
    );
  },
};

export const RowsWithoutPriceLink: Story = {
  args: { canManagePrices: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('link', {
        name: i18n.t('billing.unbilled.fix.price'),
      }),
    ).toBeNull();
    await expect(canvas.getAllByRole('link')).toHaveLength(1);
  },
};

export const Empty: Story = {
  args: {
    state: {
      status: 'ready',
      startsOn: '2026-09-01',
      page: EMPTY_UNBILLED_TUITION_PAGE,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-unbilled-tuition-empty="on"]'),
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
        requestId: 'req-unbilled-tuition',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
