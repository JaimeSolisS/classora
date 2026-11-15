import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import {
  fetchPaymentMethods,
  PAYMENT_METHODS_KEY,
  type PaymentMethod,
} from '@/features/billing/accounts/api';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { isCode, isUuid } from '@/features/payroll/periodRules';
import { todayInZone } from '@/lib/time';
import {
  EXPENSES_KEY,
  expenseKey,
  fetchExpense,
  postExpenseAdjustment,
  recordExpensePayment,
  reverseExpensePayment,
  type ExpenseAdjustmentCreate,
  type ExpensePayment,
  type ExpensePaymentCreate,
  type ExpensePaymentReversalCreate,
} from '../api';
import { ExpensesTabs } from '../ExpensesTabs';
import { ExpenseAdjustmentDialog } from './ExpenseAdjustmentDialog';
import { BackLink, ExpenseDetailView } from './ExpenseDetailView';
import { ExpensePaymentDialog } from './ExpensePaymentDialog';
import { usePaymentSummary } from './labels';
import type { PickerState } from './NewExpenseDialog';
import { ReverseExpensePaymentDialog } from './ReverseExpensePaymentDialog';
import { expenseCapabilities, REFETCH_AFTER } from './rules';

// /expenses/records/:expenseId (M7-12b C, D), for expense.view holders,
// with its query and the four dialogs. A 201 refetches the expense (the
// answers hold no amount, #115 E7) and says what happened. A refusal that
// means the expense moved (void, the caps, a duplicate, already reversed)
// refetches before the dialog shows it, so a duplicate can name the stored
// payment. Closing a dialog after any failed attempt refetches the expense
// and holds every action until that lands; after a lost answer the status
// line says the row may already exist (E2 (a), D6). A failed refetch keeps
// the expense with the out-of-date alert and holds the actions (C3).

type OpenDialog =
  | { kind: 'payment' }
  | { kind: 'adjust' }
  | { kind: 'void' }
  | { kind: 'reverse'; payment: ExpensePayment };
type DialogKind = OpenDialog['kind'];
type Block = { kind: 'refreshing' } | { kind: 'stale'; error: unknown };

const DT = 'finance.expenses.detail';
const SAVED_KEY: Record<
  DialogKind,
  'payment' | 'adjustment' | 'void' | 'reversal'
> = {
  payment: 'payment',
  adjust: 'adjustment',
  void: 'void',
  reverse: 'reversal',
};

export function ExpensePage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${DT}.title`));
  const { expenseId = '' } = useParams();
  const location = useLocation();
  const valid = isUuid(expenseId);
  const id = expenseId.toLowerCase();
  const queryClient = useQueryClient();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const can = expenseCapabilities(user);
  const timeZone = useCenterTimeZone();
  const summary = usePaymentSummary();
  const [dialog, setDialog] = useState<OpenDialog | null>(null);
  const [block, setBlock] = useState<Block | null>(null);
  const created =
    (location.state as { created?: boolean } | null)?.created === true;
  const [announcement, setAnnouncement] = useState(() =>
    created ? t(`${DT}.created`) : '',
  );

  const key = expenseKey(id);
  const expense = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchExpense(id, signal),
    enabled: valid,
    meta: { expectedStatuses: [403, 404] },
  });
  const methods = useQuery({
    queryKey: PAYMENT_METHODS_KEY,
    queryFn: ({ signal }) => fetchPaymentMethods(signal),
    enabled: dialog?.kind === 'payment',
  });

  /** The expense and the lists. Never rejects. */
  async function refresh(): Promise<void> {
    void queryClient.invalidateQueries({ queryKey: [...EXPENSES_KEY, 'list'] });
    await queryClient.invalidateQueries({ queryKey: key });
  }

  /** Refetches and holds every action until it lands (D6). */
  async function reload() {
    setBlock({ kind: 'refreshing' });
    await refresh();
    const state = queryClient.getQueryState(key);
    setBlock(
      state?.status === 'error' ? { kind: 'stale', error: state.error } : null,
    );
  }

  /** Runs a post; a refusal that means the expense moved refetches first. */
  async function post(run: () => Promise<unknown>) {
    try {
      await run();
    } catch (caught) {
      if (isCode(caught, ...REFETCH_AFTER)) await refresh();
      throw caught;
    }
  }

  function saved(kind: DialogKind) {
    setDialog(null);
    setAnnouncement(t(`${DT}.saved.${SAVED_KEY[kind]}`));
    void reload();
  }

  function closed(kind: DialogKind, failed: AttemptFailure) {
    setDialog(null);
    if (failed === 'none') return;
    if (failed === 'lost') {
      setAnnouncement(t(`${DT}.closedLost.${SAVED_KEY[kind]}`));
    }
    void reload();
  }

  function open(next: OpenDialog) {
    setAnnouncement('');
    setDialog(next);
  }

  const status =
    expense.error instanceof ApiError ? expense.error.status : undefined;
  if (status === 403) return <ForbiddenPage />;

  let content;
  if (!valid || status === 404) {
    content = (
      <div
        role="status"
        className="flex min-w-0 flex-col items-start gap-2 rounded-md border border-border p-4"
        data-expense-not-found
      >
        <p className="font-medium">{t(`${DT}.notFound`)}</p>
        <BackLink />
      </div>
    );
  } else if (!expense.data) {
    content = expense.isError ? (
      <ErrorAlert
        error={expense.error}
        onRetry={() => void expense.refetch()}
      />
    ) : (
      <LoadingState rows={6} />
    );
  }
  if (content || !expense.data) {
    return (
      <div className="flex min-w-0 flex-col gap-8">
        <ExpensesTabs />
        <div className="flex min-w-0 flex-col gap-4">
          <BackLink />
          {content}
        </div>
      </div>
    );
  }

  const data = expense.data;
  const staleError =
    block?.kind === 'stale'
      ? block.error
      : expense.isError
        ? expense.error
        : null;
  const methodOptions: PickerState<PaymentMethod> = methods.data
    ? { status: 'ready', items: methods.data }
    : methods.isError
      ? {
          status: 'error',
          error: methods.error,
          onRetry: () => void methods.refetch(),
        }
      : { status: 'loading' };
  const describePayment = (paymentId: string) => {
    const found = queryClient
      .getQueryData<typeof data>(key)
      ?.payments.find((item) => item.id === paymentId);
    return found ? summary(found) : null;
  };

  return (
    <>
      <ExpenseDetailView
        expense={data}
        can={can}
        blocked={block !== null || staleError !== null || dialog !== null}
        staleError={staleError}
        onReload={() => void reload()}
        onRecord={() => open({ kind: 'payment' })}
        onAdjust={() => open({ kind: 'adjust' })}
        onVoid={() => open({ kind: 'void' })}
        onReverse={(payment) => open({ kind: 'reverse', payment })}
        announcement={announcement}
      />
      {can.manage ? (
        <>
          <ExpensePaymentDialog
            open={dialog?.kind === 'payment'}
            today={todayInZone(timeZone)}
            expense={data}
            methods={methodOptions}
            onReloadMethods={() => void methods.refetch()}
            describePayment={describePayment}
            onSubmit={(body: ExpensePaymentCreate, idempotencyKey) =>
              post(() => recordExpensePayment(data.id, body, idempotencyKey))
            }
            onSaved={() => saved('payment')}
            onClose={(failed) => closed('payment', failed)}
          />
          <ExpenseAdjustmentDialog
            open={dialog?.kind === 'adjust' || dialog?.kind === 'void'}
            mode={dialog?.kind === 'void' ? 'void' : 'adjust'}
            key={dialog?.kind === 'void' ? 'void' : 'adjust'}
            expense={data}
            onSubmit={(body: ExpenseAdjustmentCreate, idempotencyKey) =>
              post(() => postExpenseAdjustment(data.id, body, idempotencyKey))
            }
            onSaved={() => saved(dialog?.kind === 'void' ? 'void' : 'adjust')}
            onClose={(failed) =>
              closed(dialog?.kind === 'void' ? 'void' : 'adjust', failed)
            }
          />
        </>
      ) : null}
      {dialog?.kind === 'reverse' ? (
        <ReverseExpensePaymentDialog
          key={dialog.payment.id}
          open
          summary={summary(dialog.payment)}
          onSubmit={(body: ExpensePaymentReversalCreate, idempotencyKey) =>
            post(() =>
              reverseExpensePayment(dialog.payment.id, body, idempotencyKey),
            )
          }
          onSaved={() => saved('reverse')}
          onClose={(failed) => closed('reverse', failed)}
        />
      ) : null}
    </>
  );
}
