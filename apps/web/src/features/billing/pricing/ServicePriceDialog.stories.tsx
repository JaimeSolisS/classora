import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import {
  GROUP_OPTIONS,
  LEVEL_OPTIONS,
  PRICE_WITH_NEXT,
  SUBJECT_ALGEBRA,
  SUBJECT_OPTIONS,
  TODAY,
} from './fixtures';
import { ServicePriceDialog } from './ServicePriceDialog';

// The "Add price" dialog (#363 F3): empty, a group price scoped to one
// group (level and duration disabled), the client checks, the 409 link to
// the existing price, and the subjects still loading. No request: every
// call is a stub. The dialog is portalled to <body>. axe runs on each story
// in both themes.

const meta = {
  title: 'Billing/ServicePriceDialog',
  component: ServicePriceDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    subjects: SUBJECT_OPTIONS,
    levels: LEVEL_OPTIONS,
    fetchScopes: fn(() => Promise.resolve(GROUP_OPTIONS)),
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        path="/billing/prices"
        routes={[{ path: '*', element: <ServicePriceDialog {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ServicePriceDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

const field = (dialog: HTMLElement, key: string) =>
  within(dialog).getByLabelText(
    i18n.t(`billing.prices.fields.${key}` as 'billing.prices.fields.amount'),
  );

export const Empty: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.prices.add'),
    });
    await expect(field(dialog, 'effectiveFrom')).toHaveValue(TODAY);
    await expect(field(dialog, 'pricingModel')).toBeDisabled();
    await expect(field(dialog, 'amount')).toHaveAccessibleDescription(
      i18n.t('billing.prices.hints.amount'),
    );
  },
};

export const GroupScope: Story = {
  args: {
    initialValues: {
      classType: 'group',
      pricingModel: 'full_course',
      subjectId: SUBJECT_ALGEBRA.id,
      scopeId: GROUP_OPTIONS[0]!.id,
      levelId: '',
      duration: '',
      effectiveFrom: TODAY,
      amount: '1200.00',
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      await within(dialog).findByRole('option', {
        name: GROUP_OPTIONS[0]!.name,
      }),
    ).toBeInTheDocument();
    await expect(field(dialog, 'level')).toBeDisabled();
    await expect(field(dialog, 'duration')).toBeDisabled();
  },
};

export const ValidationErrors: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.type(field(dialog, 'amount'), '12.345');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.prices.save'),
      }),
    );
    await expect(field(dialog, 'amount')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await expect(
      within(dialog).getByText(
        i18n.t('billing.prices.validation.amountInvalid'),
      ),
    ).toBeVisible();
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};

export const ExistingPrice: Story = {
  args: {
    initialValues: {
      classType: 'individual',
      pricingModel: 'per_session',
      subjectId: SUBJECT_ALGEBRA.id,
      scopeId: '',
      levelId: '',
      duration: '60',
      effectiveFrom: TODAY,
      amount: '350.00',
    },
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'service_price_exists',
          status: 409,
          requestId: 'req-exists',
          problem: {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            code: 'service_price_exists',
            request_id: 'req-exists',
            details: { service_price_id: PRICE_WITH_NEXT.id },
          },
        }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.prices.save'),
      }),
    );
    await expect(
      await within(dialog).findByRole('link', {
        name: i18n.t('billing.prices.viewExisting'),
      }),
    ).toHaveAttribute('href', `/billing/prices/${PRICE_WITH_NEXT.id}`);
  },
};

export const LoadingOptions: Story = {
  args: { subjects: null },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(dialog.querySelector('[aria-busy="true"]')).not.toBeNull();
  },
};
