import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import {
  apiCents,
  centsText,
  sumCents,
} from '@/features/billing/accounts/cents';
import type { ExpenseDetail, ExpensePayment } from '../api';
import { ExpenseStatusBadge, StateBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { useCategoryLabel } from '../labels';
import { templatePath } from '../templates/rules';
import { usePaymentSummary } from './labels';
import {
  EXPENSES_PATH,
  expenseActions,
  type ExpenseCapabilities,
} from './rules';

// One expense (M7-12b C), without any query: the header figures (a
// generated one names its template and shows "Automático" as its recorder,
// M7-13b E1), the actions (A3, E3), the payments (a reversed one struck through, with its
// reason, who and when; it doesn't count in paid) and the adjustments. A
// failed refetch keeps what is shown, with the out-of-date alert, and holds
// every action until a reload lands (C3).

export interface ExpenseDetailViewProps {
  expense: ExpenseDetail;
  can: ExpenseCapabilities;
  /** A refetch hasn't landed (or failed): nothing can start. */
  blocked?: boolean;
  staleError?: unknown;
  onReload?: () => void;
  onRecord: () => void;
  onAdjust: () => void;
  onVoid: () => void;
  onReverse: (payment: ExpensePayment) => void;
  announcement?: string;
}

const L = 'finance.expenses';
const DT = `${L}.detail`;
const WRAP = '[overflow-wrap:anywhere]';
const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

export function BackLink() {
  const { t } = useTranslation();
  return (
    <Link to={EXPENSES_PATH} className={LINK_CLASS}>
      {t(`${DT}.back`)}
    </Link>
  );
}

export function ExpenseDetailView({
  expense,
  can,
  blocked = false,
  staleError = null,
  onReload,
  onRecord,
  onAdjust,
  onVoid,
  onReverse,
  announcement = '',
}: ExpenseDetailViewProps) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const categoryLabel = useCategoryLabel();
  const summary = usePaymentSummary();
  const actions = expenseActions(can, expense);
  const none = t(`${L}.none`);
  const adjustmentsTotal = centsText(
    sumCents(expense.adjustments.map((item) => apiCents(item.amount))),
  );
  const field = (key: string, value: ReactNode, wide = false) => (
    <div
      className={`flex min-w-0 flex-col gap-1 ${wide ? 'sm:col-span-2 lg:col-span-4' : ''}`}
      data-expense-field={key}
    >
      <dt className="text-sm text-muted-foreground">
        {t(`${DT}.fields.${key as 'category'}`)}
      </dt>
      <dd className={`${WRAP} whitespace-pre-line`}>{value}</dd>
    </div>
  );
  const anyAction = actions.record || actions.adjust || actions.void;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <ExpensesTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <BackLink />
        <div className="flex flex-wrap items-center gap-3">
          <h1 className={`text-2xl font-semibold ${WRAP}`}>
            {expense.description}
          </h1>
          <ExpenseStatusBadge status={expense.status} />
        </div>
        {expense.template ? (
          <p className="text-sm" data-expense-template>
            {t('finance.templates.generatedBy')}{' '}
            <Link
              to={templatePath(expense.template.id)}
              className={`${LINK_CLASS} ${WRAP}`}
            >
              {expense.template.name}
            </Link>
          </p>
        ) : null}
        <p
          role="status"
          aria-live="polite"
          className="text-sm font-medium"
          data-expense-announcement
        >
          {announcement}
        </p>
        {staleError ? (
          <ErrorAlert
            error={staleError}
            message={t(`${DT}.stale`)}
            onRetry={onReload}
            retryLabel={t(`${DT}.reload`)}
          />
        ) : null}
        <dl
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
          data-expense-head
        >
          {field(
            'category',
            <span className="flex flex-wrap items-center gap-1">
              {categoryLabel(expense.category)}
              {expense.category.is_active ? null : (
                <StateBadge active={false}>
                  {t(`${L}.inactiveCategory`)}
                </StateBadge>
              )}
            </span>,
          )}
          {field(
            'vendor',
            expense.vendor ? (
              <span className="flex flex-wrap items-center gap-1">
                {expense.vendor.name}
                {expense.vendor.is_active ? null : (
                  <StateBadge active={false}>
                    {t(`${L}.inactiveVendor`)}
                  </StateBadge>
                )}
              </span>
            ) : (
              none
            ),
          )}
          {field('incurredOn', labels.date(expense.incurred_on))}
          {field('dueOn', labels.date(expense.due_on))}
          {field('amount', labels.money(expense.amount))}
          {field('adjustments', labels.signed(adjustmentsTotal))}
          {field('net', labels.money(expense.adjusted_amount))}
          {field('paid', labels.money(expense.paid_amount))}
          {field('open', labels.money(expense.open_amount))}
          {field('status', <ExpenseStatusBadge status={expense.status} />)}
          {field('receiptReference', expense.receipt_reference ?? none)}
          {field(
            'createdBy',
            expense.created_by
              ? t(`${DT}.createdBy`, {
                  name: expense.created_by.display_name,
                  when: labels.instantDate(expense.created_at),
                })
              : t('finance.templates.automatic'),
          )}
          {field('note', expense.note ?? none, true)}
        </dl>
        {anyAction ? (
          <div
            role="group"
            aria-label={t(`${DT}.actions.label`)}
            className="flex flex-col gap-2"
          >
            <div className="flex flex-wrap gap-2">
              {actions.record ? (
                <Button
                  type="button"
                  className="min-h-11 md:min-h-9"
                  disabled={blocked}
                  onClick={onRecord}
                >
                  {t(`${DT}.actions.record`)}
                </Button>
              ) : null}
              {actions.adjust ? (
                <Button
                  type="button"
                  variant="outline"
                  className="min-h-11 md:min-h-9"
                  disabled={blocked}
                  onClick={onAdjust}
                >
                  {t(`${DT}.actions.adjust`)}
                </Button>
              ) : null}
              {actions.void ? (
                <Button
                  type="button"
                  variant="outline"
                  className="min-h-11 md:min-h-9"
                  disabled={blocked || actions.voidBlocked}
                  aria-describedby={
                    actions.voidBlocked ? 'expense-void-blocked' : undefined
                  }
                  onClick={onVoid}
                >
                  {t(`${DT}.actions.void`)}
                </Button>
              ) : null}
            </div>
            {actions.void && actions.voidBlocked ? (
              <p
                id="expense-void-blocked"
                className="text-sm text-muted-foreground"
              >
                {t(`${DT}.actions.voidBlocked`)}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <section
        className="flex min-w-0 flex-col gap-3"
        aria-labelledby="expense-payments-title"
      >
        <h2 id="expense-payments-title" className="text-lg font-semibold">
          {t(`${DT}.payments.title`)}
        </h2>
        {expense.payments.length === 0 ? (
          <p data-payments-empty>{t(`${DT}.payments.empty`)}</p>
        ) : (
          <>
            <div className="hidden md:block" data-payments-table>
              <Table>
                <TableCaption className="sr-only">
                  {t(`${DT}.payments.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    {(
                      [
                        'date',
                        'amount',
                        'method',
                        'reference',
                        'note',
                        'recordedBy',
                        'actions',
                      ] as const
                    ).map((column) => (
                      <TableHead key={column} scope="col">
                        {t(`${DT}.payments.columns.${column}`)}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {expense.payments.map((payment) => (
                    <TableRow
                      key={payment.id}
                      data-payment-id={payment.id}
                      data-reversed={payment.reversal ? true : undefined}
                      className="align-top"
                    >
                      <TableCell>{labels.date(payment.paid_on)}</TableCell>
                      <TableCell
                        className={
                          payment.reversal ? 'line-through' : undefined
                        }
                      >
                        {labels.money(payment.amount)}
                      </TableCell>
                      <TableCell>
                        {labels.method(payment.method_code)}
                      </TableCell>
                      <TableCell className={`whitespace-normal ${WRAP}`}>
                        {payment.reference ?? none}
                      </TableCell>
                      <TableCell
                        className={`max-w-xs whitespace-pre-line ${WRAP}`}
                      >
                        {payment.note ?? none}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {t(`${DT}.createdBy`, {
                          name: payment.recorded_by.display_name,
                          when: labels.instantDate(payment.created_at),
                        })}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <PaymentState
                          payment={payment}
                          canReverse={actions.reverse}
                          blocked={blocked}
                          onReverse={onReverse}
                          summary={summary(payment)}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-payments-cards>
              {expense.payments.map((payment) => (
                <li
                  key={payment.id}
                  data-payment-card={payment.id}
                  className="flex min-w-0 flex-col gap-1 rounded-lg border border-border p-4 text-sm"
                >
                  <p
                    className={`font-medium ${payment.reversal ? 'line-through' : ''}`}
                  >
                    {summary(payment)}
                  </p>
                  {payment.note ? (
                    <p className={`whitespace-pre-line ${WRAP}`}>
                      {payment.note}
                    </p>
                  ) : null}
                  <p className="text-muted-foreground">
                    {t(`${DT}.createdBy`, {
                      name: payment.recorded_by.display_name,
                      when: labels.instantDate(payment.created_at),
                    })}
                  </p>
                  <PaymentState
                    payment={payment}
                    canReverse={actions.reverse}
                    blocked={blocked}
                    onReverse={onReverse}
                    summary={summary(payment)}
                  />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section
        className="flex min-w-0 flex-col gap-3"
        aria-labelledby="expense-adjustments-title"
      >
        <h2 id="expense-adjustments-title" className="text-lg font-semibold">
          {t(`${DT}.adjustments.title`)}
        </h2>
        {expense.adjustments.length === 0 ? (
          <p data-adjustments-empty>{t(`${DT}.adjustments.empty`)}</p>
        ) : (
          <Table data-adjustments-table>
            <TableCaption className="sr-only">
              {t(`${DT}.adjustments.caption`)}
            </TableCaption>
            <TableHeader>
              <TableRow>
                {(['date', 'amount', 'reason', 'by'] as const).map((column) => (
                  <TableHead key={column} scope="col">
                    {t(`${DT}.adjustments.columns.${column}`)}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {expense.adjustments.map((adjustment) => (
                <TableRow
                  key={adjustment.id}
                  data-adjustment-id={adjustment.id}
                  className="align-top"
                >
                  <TableCell>
                    {labels.instantDate(adjustment.created_at)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {labels.signed(adjustment.amount)}
                  </TableCell>
                  <TableCell className={`max-w-xs whitespace-pre-line ${WRAP}`}>
                    {adjustment.reason}
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {adjustment.created_by.display_name}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  );
}

function PaymentState({
  payment,
  canReverse,
  blocked,
  onReverse,
  summary,
}: {
  payment: ExpensePayment;
  canReverse: boolean;
  blocked: boolean;
  onReverse: (payment: ExpensePayment) => void;
  summary: string;
}) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  if (payment.reversal) {
    return (
      <div className="flex flex-col gap-1" data-payment-reversal>
        <span className="font-medium">{t(`${DT}.payments.reversedState`)}</span>
        <span>
          {t(`${DT}.payments.reversedBy`, {
            name: payment.reversal.reversed_by.display_name,
            when: labels.instantDate(payment.reversal.created_at),
          })}
        </span>
        <span className={`whitespace-pre-line ${WRAP}`}>
          {t(`${DT}.payments.reason`, { reason: payment.reversal.reason })}
        </span>
      </div>
    );
  }
  if (!canReverse) return <>{t(`${L}.none`)}</>;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="min-h-11 md:min-h-8"
      disabled={blocked}
      aria-label={t(`${DT}.payments.reverseFor`, { payment: summary })}
      data-reverse-payment={payment.id}
      onClick={() => onReverse(payment)}
    >
      {t(`${DT}.payments.reverse`)}
    </Button>
  );
}
