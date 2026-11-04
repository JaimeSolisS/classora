import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn, within } from 'storybook/test';
import { ApiError, CLIENT_ERROR_CODES } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CAT_RENT,
  EXPENSE_METHODS,
  EXPENSE_PARTIAL,
  EXPENSE_TODAY,
  EXPENSE_UNPAID,
  EXPENSES_OWNER_SESSION,
  MIXED_CATEGORIES,
  PAYMENT_VALID,
  VENDORS,
} from '../fixtures';
import { ExpenseAdjustmentDialog } from './ExpenseAdjustmentDialog';
import { ExpensePaymentDialog } from './ExpensePaymentDialog';
import { NewExpenseDialog } from './NewExpenseDialog';
import { ReverseExpensePaymentDialog } from './ReverseExpensePaymentDialog';

// The four expense dialogs (M7-12b F2), each also in the locked state after a
// lost answer (E2 (a)): the play function submits, the call rejects with no
// response, and the fields lock with "Volver a enviar…" as the only action.
// No request. axe runs on each story in both themes.

const D = 'finance.expenses.dialog';
const lostCall = fn(() =>
  Promise.reject(new ApiError({ code: CLIENT_ERROR_CODES.network })),
);

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/records/x"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/ExpenseDialogs',
  render: () => <></>,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

async function submitThenLocked(
  context: Context,
  title: string,
  submit: string,
  resend: string,
) {
  const dialog = await body(context).findByRole('dialog', { name: title });
  await context.userEvent.click(
    within(dialog).getByRole('button', { name: submit }),
  );
  await expect(await within(dialog).findByRole('alert')).toBeVisible();
  await expect(
    within(dialog).getByRole('button', { name: resend }),
  ).toBeVisible();
}

const createArgs = {
  open: true,
  today: EXPENSE_TODAY,
  categories: { status: 'ready', items: MIXED_CATEGORIES } as const,
  vendors: { status: 'ready', items: VENDORS } as const,
  onReloadPickers: fn(() => Promise.resolve()),
  onSubmit: fn(() => Promise.resolve()),
  onSaved: fn(),
  onClose: fn(),
};

export const NewExpense: Story = {
  render: () => (
    <Shell>
      <NewExpenseDialog {...createArgs} />
    </Shell>
  ),
};

export const NewExpenseLost: Story = {
  render: () => (
    <Shell>
      <NewExpenseDialog
        {...createArgs}
        onSubmit={lostCall}
        initialValues={{
          categoryId: CAT_RENT.id,
          description: 'Renta de noviembre',
          amount: '1000',
        }}
      />
    </Shell>
  ),
  play: (context) =>
    submitThenLocked(
      context,
      i18n.t(`${D}.create.title`),
      i18n.t(`${D}.create.submit`),
      i18n.t(`${D}.create.resend`),
    ),
};

const paymentArgs = {
  open: true,
  today: EXPENSE_TODAY,
  expense: EXPENSE_PARTIAL,
  methods: { status: 'ready', items: EXPENSE_METHODS } as const,
  onReloadMethods: fn(),
  describePayment: () => null,
  onSubmit: fn(() => Promise.resolve()),
  onSaved: fn(),
  onClose: fn(),
};

export const Payment: Story = {
  render: () => (
    <Shell>
      <ExpensePaymentDialog {...paymentArgs} />
    </Shell>
  ),
};

export const PaymentLost: Story = {
  render: () => (
    <Shell>
      <ExpensePaymentDialog
        {...paymentArgs}
        onSubmit={lostCall}
        initialValues={{ methodCode: 'cash' }}
      />
    </Shell>
  ),
  play: (context) =>
    submitThenLocked(
      context,
      i18n.t(`${D}.payment.title`),
      i18n.t(`${D}.payment.submit`),
      i18n.t(`${D}.payment.resend`),
    ),
};

const adjustArgs = {
  open: true,
  expense: EXPENSE_PARTIAL,
  onSubmit: fn(() => Promise.resolve()),
  onSaved: fn(),
  onClose: fn(),
};

export const Adjust: Story = {
  render: () => (
    <Shell>
      <ExpenseAdjustmentDialog {...adjustArgs} mode="adjust" />
    </Shell>
  ),
};

export const AdjustLost: Story = {
  render: () => (
    <Shell>
      <ExpenseAdjustmentDialog
        {...adjustArgs}
        mode="adjust"
        onSubmit={lostCall}
        initialValues={{ direction: 'increase', amount: '50', reason: 'IVA' }}
      />
    </Shell>
  ),
  play: (context) =>
    submitThenLocked(
      context,
      i18n.t(`${D}.adjust.title`),
      i18n.t(`${D}.adjust.submit`),
      i18n.t(`${D}.adjust.resend`),
    ),
};

export const Void: Story = {
  render: () => (
    <Shell>
      <ExpenseAdjustmentDialog
        {...adjustArgs}
        expense={EXPENSE_UNPAID}
        mode="void"
      />
    </Shell>
  ),
};

export const VoidLost: Story = {
  render: () => (
    <Shell>
      <ExpenseAdjustmentDialog
        {...adjustArgs}
        expense={EXPENSE_UNPAID}
        mode="void"
        onSubmit={lostCall}
        initialValues={{ reason: 'Categoría equivocada' }}
      />
    </Shell>
  ),
  play: (context) =>
    submitThenLocked(
      context,
      i18n.t(`${D}.void.title`),
      i18n.t(`${D}.void.submit`),
      i18n.t(`${D}.void.resend`),
    ),
};

const reverseArgs = {
  open: true,
  summary: `15 oct 2026, $400.00, Transferencia ${PAYMENT_VALID.reference ?? ''}`,
  onSubmit: fn(() => Promise.resolve()),
  onSaved: fn(),
  onClose: fn(),
};

export const Reverse: Story = {
  render: () => (
    <Shell>
      <ReverseExpensePaymentDialog {...reverseArgs} />
    </Shell>
  ),
};

export const ReverseLost: Story = {
  render: () => (
    <Shell>
      <ReverseExpensePaymentDialog
        {...reverseArgs}
        onSubmit={lostCall}
        initialReason="Pago duplicado"
      />
    </Shell>
  ),
  play: (context) =>
    submitThenLocked(
      context,
      i18n.t(`${D}.reverse.title`),
      i18n.t(`${D}.reverse.submit`),
      i18n.t(`${D}.reverse.resend`),
    ),
};
