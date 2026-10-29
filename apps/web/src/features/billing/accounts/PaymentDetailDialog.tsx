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
import { useAccountLabels } from './labels';

// A payment's detail (#379 C4), without any query: the payment's fields
// and every allocation it has had, oldest first (charge, student, amount,
// date; a released one is marked "Released"). The charges are read from
// the account's charges; one that couldn't be read shows its kind as
// "Charge".

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
}

export function PaymentDetailDialog({
  open,
  state,
  studentNames,
  onClose,
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
