import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { VIEWPORTS } from '@/features/assignments/storyKit';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CHARGE_PENDING, TODAY } from '../accounts/fixtures';
import { DueDateDialog } from './DueDateDialog';
import { CHARGE_MOVED, DUE_DATE_CHANGES } from './fixtures';

// "Change due date" (#382 F2): a moved charge with its history, a charge
// without history and a past date (the warning), the refusals (409
// charge_settled as an alert, due_date_unchanged on the date), and the
// history at phone width (nothing scrolls the dialog sideways, the #379 D2
// check). No request: every call is a stub. The dialog is portalled to
// <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/DueDates/DueDateDialog',
  component: DueDateDialog,
  args: {
    open: true,
    charge: CHARGE_MOVED,
    today: TODAY,
    history: { status: 'ready', items: DUE_DATE_CHANGES },
    onSubmit: fn(() => Promise.resolve()),
    onStale: fn(),
    onClose: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <DueDateDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof DueDateDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const dialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).findByRole('dialog', {
    name: i18n.t('billing.dueDates.change.title'),
  });

export const WithHistory: Story = {
  play: async (context) => {
    const body = within(await dialog(context));
    await expect(
      body.getByLabelText(i18n.t('billing.dueDates.change.newDate')),
    ).toHaveValue(CHARGE_MOVED.due_date);
    await expect(body.getByText(DUE_DATE_CHANGES[0]!.reason)).toBeVisible();
  },
};

export const NoHistoryPastDate: Story = {
  args: {
    charge: CHARGE_PENDING,
    history: { status: 'ready', items: [] },
    initialValues: { dueDate: '2026-10-10', reason: 'Error de captura' },
  },
  play: async (context) => {
    const body = within(await dialog(context));
    await expect(
      body.getByText(i18n.t('billing.dueDates.history.empty')),
    ).toBeVisible();
    await expect(
      body.getByText(i18n.t('billing.dueDates.change.pastWarning')),
    ).toBeVisible();
  },
};

export const Refused: Story = {
  args: {
    charge: CHARGE_PENDING,
    history: { status: 'ready', items: [] },
    initialValues: { dueDate: CHARGE_PENDING.due_date, reason: 'Motivo' },
    initialErrors: { dueDate: 'errors:due_date_unchanged' },
    initialFailure: new ApiError({
      code: 'charge_settled',
      status: 409,
      requestId: 'req-due-date',
    }),
  },
  play: async (context) => {
    const body = within(await dialog(context));
    await expect(body.getByRole('alert')).toHaveTextContent(
      i18n.t('charge_settled', { ns: 'errors' }),
    );
    await expect(
      body.getByLabelText(i18n.t('billing.dueDates.change.newDate')),
    ).toHaveAttribute('aria-invalid', 'true');
  },
};

export const WithHistoryPhone: Story = {
  ...WithHistory,
  globals: { viewport: { value: 'phone390' } },
  play: async (context) => {
    await WithHistory.play?.(context);
    const element = await dialog(context);
    const view = context.canvasElement.ownerDocument.defaultView!;
    await expect(view.innerWidth).toBeLessThanOrEqual(390);
    await expect(element.scrollWidth).toBeLessThanOrEqual(element.clientWidth);
    await expect(element.getBoundingClientRect().right).toBeLessThanOrEqual(
      view.innerWidth,
    );
  },
};
