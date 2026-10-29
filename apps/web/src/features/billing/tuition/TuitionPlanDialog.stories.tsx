import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { PRICE_PER_PERIOD, TODAY } from '../pricing/fixtures';
import {
  CATALOG_PRICES,
  ENROLLMENTS,
  searchFixtureStudents,
  STUDENT_ANA,
} from './fixtures';
import { TuitionPlanDialog } from './TuitionPlanDialog';

// The "New plan" dialog (#374 H2): empty, an individual plan for N
// periods (the last day shown), a group plan with the enrollments (two
// disabled with their reasons), the student_has_no_family alert, and the
// prices still loading. No request: every call is a stub. The dialog is
// portalled to <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/TuitionPlanDialog',
  component: TuitionPlanDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    prices: CATALOG_PRICES.filter(
      (price) => price.pricing_model === 'per_period',
    ),
    canOpenFamilies: true,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
    search: (q) => searchFixtureStudents(q),
    fetchEnrollments: () => Promise.resolve(ENROLLMENTS),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        path="/billing/tuition-plans"
        routes={[{ path: '*', element: <TuitionPlanDialog {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof TuitionPlanDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Empty: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.tuition.add'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.tuition.fields.startsOn')),
    ).toHaveValue(TODAY);
    await expect(
      within(dialog).getByText(i18n.t('billing.tuition.hints.individual')),
    ).toBeVisible();
  },
};

export const IndividualForPeriods: Story = {
  args: {
    initialValues: {
      student: STUDENT_ANA,
      kind: 'individual',
      enrollmentId: '',
      priceId: PRICE_PER_PERIOD.id,
      startsOn: '2027-01-31',
      end: 'periods',
      periods: '2',
      dueOffset: '5',
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByText(/Mar 30, 2027|30 mar 2027/),
    ).toBeVisible();
  },
};

export const GroupEnrollments: Story = {
  args: {
    initialValues: {
      student: STUDENT_ANA,
      kind: 'group',
      enrollmentId: '',
      priceId: '',
      startsOn: TODAY,
      end: 'open',
      periods: '12',
      dueOffset: '0',
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    const enrollment = within(dialog).getByLabelText(
      i18n.t('billing.tuition.fields.enrollment'),
    );
    await expect(enrollment).toBeEnabled();
    await expect(dialog.querySelectorAll('option[disabled]').length).toBe(2);
  },
};

export const NoFamily: Story = {
  args: {
    initialValues: {
      student: STUDENT_ANA,
      kind: 'individual',
      enrollmentId: '',
      priceId: PRICE_PER_PERIOD.id,
      startsOn: TODAY,
      end: 'open',
      periods: '12',
      dueOffset: '0',
    },
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'student_has_no_family',
          status: 409,
          requestId: 'req-no-family',
        }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.tuition.create'),
      }),
    );
    await expect(
      await within(dialog).findByRole('link', {
        name: i18n.t('billing.tuition.openFamilies'),
      }),
    ).toHaveAttribute('href', '/families');
  },
};

export const LoadingPrices: Story = {
  args: { prices: null },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(dialog.querySelector('[aria-busy="true"]')).not.toBeNull();
  },
};
