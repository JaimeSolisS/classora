import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import type { ChargeItem, PaymentDetail } from './api';
import { ReleasedBadge, ReversedBadge } from './AccountBadges';
import { canRefund, reversalBlock } from './corrections';
import { useAccountLabels } from './labels';

// A payment's detail (#379 C4), without any query: the payment's fields
// and every allocation it has had, oldest first (charge, student, amount,
// date; a released one is marked "Released"). The charges are read from
// the account's charges; one that couldn't be read shows its kind as
// "Charge". #386 B1-B3: the reversal (date, reason, "Reversed") and the
// refunds (date, amount, method, reference, reason); finance.refund.approve
// holders get "Reverse payment" (not on a reversed payment, nor on one with
// refunds, which says why) and "Refund" (not reversed, something
// unallocated). The polite status inside the dialog reads what an action
// changed (the page behind the modal is hidden from assistive technology).

export type PaymentDetailState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      payment: PaymentDetail;
      /** Charge id → charge (null while they load). */
      charges: Readonly<Record<string, ChargeItem>> | null;
    };

export interface PaymentDetailDialogProps {
  open: boolean;
  state: PaymentDetailState;
  /** Student id → name (the summary's students). */
  studentNames: Readonly<Record<string, string>>;
  onClose: () => void;
  /** finance.refund.approve: "Reverse payment" and "Refund" (#386). */
  canApproveRefunds?: boolean;
  onReverse?: (payment: PaymentDetail) => void;
  onRefund?: (payment: PaymentDetail) => void;
  /** Read by the dialog's polite status (a reversal or refund just saved). */
  notice?: string;
}

export function PaymentDetailDialog({
  open,
  state,
  studentNames,
  onClose,
  canApproveRefunds = false,
  onReverse,
  onRefund,
  notice = '',
}: PaymentDetailDialogProps) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();

  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { payment, charges } = state;
    const terms: [string, ReactNode][] = [
      [
        t('billing.accounts.payments.columns.amount'),
        labels.money(payment.amount),
      ],
      [
        t('billing.accounts.payments.columns.receivedOn'),
        labels.date(payment.received_on),
      ],
      [
        t('billing.accounts.payments.columns.method'),
        labels.method(payment.payment_method_code),
      ],
      [
        t('billing.accounts.payments.columns.allocated'),
        labels.money(payment.allocated_amount),
      ],
      [
        t('billing.accounts.payments.columns.unallocated'),
        labels.money(payment.unallocated_amount),
      ],
      [
        t('billing.accounts.payments.columns.reference'),
        payment.reference ?? '—',
      ],
      [t('billing.accounts.payments.columns.payer'), payment.payer_name ?? '—'],
      [t('billing.accounts.payments.columns.note'), payment.note ?? '—'],
      [
        t('billing.accounts.payments.recordedAt'),
        labels.instantDate(payment.recorded_at),
      ],
    ];
    const blocked = reversalBlock(payment);
    const refundable = canRefund(payment);
    const actions =
      canApproveRefunds && (blocked !== 'reversed' || refundable) ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-3">
            {blocked === null ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => onReverse?.(payment)}
              >
                {t('billing.accounts.reverse.action')}
              </Button>
            ) : null}
            {refundable ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => onRefund?.(payment)}
              >
                {t('billing.accounts.refund.action')}
              </Button>
            ) : null}
          </div>
          {blocked === 'hasRefunds' ? (
            <p className="text-sm text-muted-foreground">
              {t('billing.accounts.reverse.hasRefunds')}
            </p>
          ) : null}
        </div>
      ) : null;
    content = (
      <div className="flex flex-col gap-4">
        {payment.is_reversed ? (
          <p>
            <ReversedBadge />
          </p>
        ) : null}
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[max-content_minmax(0,1fr)]">
          {terms.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="text-sm font-medium text-muted-foreground">
                {term}
              </dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
        {actions}
        {payment.reversal ? (
          <div className="flex flex-col gap-2" data-payment-reversal="">
            <h3 className="font-semibold">
              {t('billing.accounts.payments.reversal')}
            </h3>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[max-content_minmax(0,1fr)]">
              <dt className="text-sm font-medium text-muted-foreground">
                {t('billing.accounts.payments.reversedOn')}
              </dt>
              <dd>{labels.instantDate(payment.reversal.reversed_at)}</dd>
              <dt className="text-sm font-medium text-muted-foreground">
                {t('billing.accounts.payments.reason')}
              </dt>
              <dd className="break-words">{payment.reversal.reason ?? '—'}</dd>
            </dl>
          </div>
        ) : null}
        {payment.refunds.length > 0 ? (
          <>
            <h3 className="font-semibold">
              {t('billing.accounts.payments.refunds')}
            </h3>
            <Table aria-label={t('billing.accounts.payments.refunds')}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('billing.accounts.payments.refundColumns.date')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.payments.refundColumns.amount')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.payments.refundColumns.method')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.payments.refundColumns.reference')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.payments.refundColumns.reason')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payment.refunds.map((refund) => (
                  <TableRow key={refund.id} data-payment-refund={refund.id}>
                    <TableCell>{labels.date(refund.refunded_on)}</TableCell>
                    <TableCell>{labels.money(refund.amount)}</TableCell>
                    <TableCell>
                      {labels.method(refund.payment_method_code)}
                    </TableCell>
                    <TableCell className="max-w-40 whitespace-normal break-words">
                      {refund.reference ?? '—'}
                    </TableCell>
                    <TableCell className="max-w-56 whitespace-normal break-words">
                      {refund.reason ?? '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}
        <h3 className="font-semibold">
          {t('billing.accounts.payments.allocations')}
        </h3>
        {payment.allocations.length === 0 ? (
          <p className="text-sm">
            {t('billing.accounts.payments.noAllocations')}
          </p>
        ) : (
          <Table aria-label={t('billing.accounts.payments.allocations')}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.accounts.charges.columns.description')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.charges.columns.student')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.payments.columns.amount')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.payments.allocatedOn')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payment.allocations.map((allocation) => {
                const charge = charges?.[allocation.charge_id];
                return (
                  <TableRow
                    key={allocation.id}
                    data-payment-allocation={allocation.id}
                  >
                    <TableCell className="max-w-48 whitespace-normal break-words">
                      {charge
                        ? labels.chargeLabel(charge)
                        : t('billing.accounts.payments.charge')}
                      {charge ? (
                        <span className="block text-xs text-muted-foreground">
                          {labels.date(charge.service_date)}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-40 whitespace-normal break-words">
                      {studentNames[allocation.student_id] ?? '—'}
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex flex-wrap items-center gap-2">
                        {labels.money(allocation.amount)}
                        {allocation.is_valid ? null : <ReleasedBadge />}
                      </span>
                    </TableCell>
                    <TableCell>
                      {labels.instantDate(allocation.created_at)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {open ? (
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
          onOpenAutoFocus={returnFocus.onOpenAutoFocus}
          onCloseAutoFocus={returnFocus.onCloseAutoFocus}
        >
          <DialogHeader>
            <DialogTitle>
              {t('billing.accounts.payments.detailTitle')}
            </DialogTitle>
            <DialogDescription>
              {t('billing.accounts.payments.detailHelp')}
            </DialogDescription>
          </DialogHeader>
          <p role="status" className="text-sm font-medium">
            {notice}
          </p>
          {content}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('billing.accounts.payments.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
