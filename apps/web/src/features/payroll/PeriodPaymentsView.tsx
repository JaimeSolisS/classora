import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import type { PayrollPayment, PayrollPeriodDetail } from './api';
import { usePaymentSummary } from './labels';
import { remainingOf, type PaymentCapabilities } from './paymentForm';
import { PaidStateBadge } from './PeriodsView';

// The payments of a finalized period (M7-09b D1, D3, C3), without any query:
// the total, what's paid and what's left with the paid-state badge, "Registrar
// pago" and each payment, a table from 768 px and cards below it. A reversed
// payment stays listed, struck through, with its reason, who and when; it no
// longer counts (the API's paid total leaves it out). Rows whose refetch
// failed stay, marked as possibly out of date, and no action starts until a
// reload succeeds (D4). Every amount is the API's string.

export type PaymentsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly PayrollPayment[];
      /** The last refetch failed: these rows may be out of date. */
      refreshError: unknown;
      onRefresh: () => void;
    };

export interface PeriodPaymentsViewProps {
  period: PayrollPeriodDetail;
  payments: PaymentsState;
  can: PaymentCapabilities;
  /** A refetch or another action runs, or the page may be out of date. */
  busy: boolean;
  /** The last outcome, for the status region. */
  announcement: string;
  onRecord: () => void;
  onReverse: (payment: PayrollPayment) => void;
}

const BUTTON_CLASS = 'min-h-11 md:min-h-9';

function Reversal({ payment }: { payment: PayrollPayment }) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  if (!payment.reversal) {
    return <span>{t('payroll.payments.notReversed')}</span>;
  }
  const { reversal } = payment;
  return (
    <span className="flex min-w-0 flex-col gap-1" data-payment-reversal>
      <span className="font-medium">
        {t('payroll.payments.reversedBy', {
          name: reversal.reversed_by.display_name,
          when: formatInstant(reversal.created_at, 'dateTime'),
        })}
      </span>
      <span className="break-words whitespace-pre-line">
        {t('payroll.payments.reversedReason', { reason: reversal.reason })}
      </span>
    </span>
  );
}

function Rows({
  items,
  stale,
  canReverse,
  busy,
  onReverse,
}: {
  items: readonly PayrollPayment[];
  stale: boolean;
  canReverse: boolean;
  busy: boolean;
  onReverse: (payment: PayrollPayment) => void;
}) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney, formatInstant } = useFormatters();
  const labels = useAccountLabels();
  const summary = usePaymentSummary();
  const P = 'payroll.payments';
  const amount = (payment: PayrollPayment) => (
    <span
      className={cn(payment.reversal && 'line-through')}
      data-payment-amount={payment.amount}
    >
      {formatMoney(payment.amount)}
    </span>
  );
  const recordedBy = (payment: PayrollPayment) =>
    t('payroll.period.head.byAt', {
      name: payment.recorded_by.display_name,
      when: formatInstant(payment.created_at, 'dateTime'),
    });
  const action = (payment: PayrollPayment) =>
    canReverse && !payment.reversal ? (
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={BUTTON_CLASS}
        disabled={busy}
        aria-label={t(`${P}.reverseFor`, { payment: summary(payment) })}
        data-reverse-payment={payment.id}
        onClick={() => onReverse(payment)}
      >
        {t(`${P}.reverse`)}
      </Button>
    ) : null;
  const columns = [
    'date',
    'amount',
    'method',
    'reference',
    'note',
    'recordedBy',
    'reversal',
  ] as const;
  return (
    <div
      className={cn('flex min-w-0 flex-col', stale && 'opacity-70')}
      data-payments-stale={stale ? 'true' : undefined}
    >
      <div className="hidden md:block" data-payments-table>
        <Table>
          <TableCaption className="sr-only">{t(`${P}.caption`)}</TableCaption>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead
                  key={column}
                  scope="col"
                  className={column === 'amount' ? 'text-right' : undefined}
                >
                  {t(`${P}.columns.${column}`)}
                </TableHead>
              ))}
              {canReverse ? (
                <TableHead scope="col">
                  <span className="sr-only">{t(`${P}.columns.actions`)}</span>
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((payment) => (
              <TableRow
                key={payment.id}
                data-payment-id={payment.id}
                data-reversed={payment.reversal ? 'true' : undefined}
                className="align-top"
              >
                <TableCell>{formatLocalDate(payment.paid_on)}</TableCell>
                <TableCell className="text-right font-medium">
                  {amount(payment)}
                </TableCell>
                <TableCell>{labels.method(payment.method_code)}</TableCell>
                <TableCell className="break-words whitespace-normal">
                  {payment.reference}
                </TableCell>
                <TableCell className="break-words whitespace-pre-line">
                  {payment.note ?? '—'}
                </TableCell>
                <TableCell className="whitespace-normal">
                  {recordedBy(payment)}
                </TableCell>
                <TableCell className="min-w-40 whitespace-normal">
                  <Reversal payment={payment} />
                </TableCell>
                {canReverse ? <TableCell>{action(payment)}</TableCell> : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-payments-cards>
        {items.map((payment) => (
          <li
            key={payment.id}
            data-payment-card={payment.id}
            className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>{formatLocalDate(payment.paid_on)}</span>
              <span className="font-medium">{amount(payment)}</span>
            </div>
            <dl className="grid grid-cols-1 gap-1 text-sm">
              {(
                [
                  ['method', labels.method(payment.method_code)],
                  ['reference', payment.reference],
                  ['note', payment.note ?? '—'],
                  ['recordedBy', recordedBy(payment)],
                ] as const
              ).map(([column, value]) => (
                <div key={column} className="min-w-0">
                  <dt className="font-medium">{t(`${P}.columns.${column}`)}</dt>
                  <dd className="break-words whitespace-pre-line">{value}</dd>
                </div>
              ))}
              <div className="min-w-0">
                <dt className="font-medium">{t(`${P}.columns.reversal`)}</dt>
                <dd>
                  <Reversal payment={payment} />
                </dd>
              </div>
            </dl>
            {action(payment)}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PeriodPaymentsView({
  period,
  payments,
  can,
  busy,
  announcement,
  onRecord,
  onReverse,
}: PeriodPaymentsViewProps) {
  const { t } = useTranslation();
  const { formatMoney } = useFormatters();
  const id = useId();
  const P = 'payroll.payments';
  const remaining = remainingOf(period) ?? '0.00';
  const zero =
    period.total_amount !== null && /^-?0+(\.0+)?$/.test(period.total_amount);
  const stale = payments.status === 'ready' && payments.refreshError !== null;
  const locked = busy || stale || payments.status !== 'ready';

  const summary: [string, string, string][] = [
    [
      'total',
      t(`${P}.totals.total`),
      formatMoney(period.total_amount ?? '0.00'),
    ],
    ['paid', t(`${P}.totals.paid`), formatMoney(period.paid_total ?? '0.00')],
    ['remaining', t(`${P}.totals.remaining`), formatMoney(remaining)],
  ];

  let content;
  if (payments.status === 'loading') {
    content = <LoadingState rows={3} />;
  } else if (payments.status === 'error') {
    content = <ErrorAlert error={payments.error} onRetry={payments.onRetry} />;
  } else {
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        {payments.refreshError ? (
          <ErrorAlert
            error={payments.refreshError}
            message={t(`${P}.outdated`)}
            onRetry={payments.onRefresh}
            retryLabel={t('payroll.period.reload')}
          />
        ) : null}
        {payments.items.length > 0 ? (
          <Rows
            items={payments.items}
            stale={stale}
            canReverse={can.reverse}
            busy={locked}
            onReverse={onReverse}
          />
        ) : (
          <p className="text-sm" data-payments-empty>
            {zero ? t(`${P}.nothingToPay`) : t(`${P}.empty`)}
          </p>
        )}
      </div>
    );
  }

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-4"
      data-payments-section
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t(`${P}.title`)}
      </h2>
      <p className="text-sm text-muted-foreground">{t(`${P}.intro`)}</p>
      <p
        role="status"
        className="text-sm font-medium"
        data-payments-announcement={announcement}
      >
        {announcement}
      </p>
      <dl
        className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
        data-payments-totals
      >
        {summary.map(([key, term, value]) => (
          <div key={key} className="min-w-0">
            <dt className="text-sm font-medium text-muted-foreground">
              {term}
            </dt>
            <dd className="font-semibold" data-payments-total={key}>
              {value}
            </dd>
          </div>
        ))}
        <div className="min-w-0">
          <dt className="text-sm font-medium text-muted-foreground">
            {t(`${P}.totals.state`)}
          </dt>
          <dd>
            {period.paid_state ? (
              <PaidStateBadge state={period.paid_state} />
            ) : null}
          </dd>
        </div>
      </dl>
      {can.record ? (
        <div>
          <Button
            type="button"
            className={BUTTON_CLASS}
            disabled={locked}
            onClick={onRecord}
          >
            {t(`${P}.record`)}
          </Button>
        </div>
      ) : null}
      {content}
    </section>
  );
}
