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
import { ChargeStatusBadge } from '../tuition/TuitionBadges';
import { ReleasedBadge } from './AccountBadges';
import type { ChargeDetail } from './api';
import { useAccountLabels } from './labels';

// A charge's detail (#386 A2; #100 GET …/charges/{charge_id}), without any
// query: its amounts (amount, adjustments, net, paid, open, status), every
// adjustment with its kind, signed amount, date and reason, and every
// allocation (a released one is marked "Released"; "View payment" opens
// that payment). billing.manage holders get "Adjust" (A3), except on a
// package purchase, which is corrected by cancelling the package. The
// polite status inside the dialog reads what an adjustment changed (the
// page behind the modal is hidden from assistive technology).

export type ChargeDetailState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; charge: ChargeDetail };

export interface ChargeDetailDialogProps {
  open: boolean;
  state: ChargeDetailState;
  /** billing.manage: "Adjust". */
  canManage: boolean;
  onAdjust: (charge: ChargeDetail) => void;
  onViewPayment: (paymentId: string) => void;
  /** Read by the dialog's polite status (an adjustment just saved). */
  notice?: string;
  onClose: () => void;
}

export function ChargeDetailDialog({
  open,
  state,
  canManage,
  onAdjust,
  onViewPayment,
  notice = '',
  onClose,
}: ChargeDetailDialogProps) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();

  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { charge } = state;
    const isPackage = charge.kind === 'package_purchase';
    const terms: [string, ReactNode][] = [
      [t('billing.accounts.chargeDetail.amounts.student'), charge.student.name],
      [
        t('billing.accounts.chargeDetail.amounts.serviceDate'),
        labels.date(charge.service_date),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.dueDate'),
        labels.date(charge.due_date),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.amount'),
        labels.money(charge.amount),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.adjustments'),
        labels.signed(charge.adjustments_total),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.net'),
        <span key="net" data-charge-net={charge.net}>
          {labels.money(charge.net)}
        </span>,
      ],
      [
        t('billing.accounts.chargeDetail.amounts.allocated'),
        labels.money(charge.allocated),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.open'),
        labels.money(charge.open),
      ],
      [
        t('billing.accounts.chargeDetail.amounts.status'),
        <ChargeStatusBadge key="status" status={charge.status} />,
      ],
    ];
    content = (
      <div className="flex flex-col gap-4">
        <p className="font-medium break-words">
          {labels.chargeLabel(charge)}
          <span className="block text-sm font-normal text-muted-foreground">
            {labels.chargeKind(charge.kind)}
          </span>
        </p>
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
        {canManage ? (
          isPackage ? (
            <p className="text-sm text-muted-foreground">
              {t('billing.accounts.chargeDetail.packageHint')}
            </p>
          ) : (
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={() => onAdjust(charge)}
              >
                {t('billing.accounts.adjust.action')}
              </Button>
            </div>
          )
        ) : null}
        <h3 className="font-semibold">
          {t('billing.accounts.chargeDetail.adjustments')}
        </h3>
        {charge.adjustments.length === 0 ? (
          <p className="text-sm">
            {t('billing.accounts.chargeDetail.noAdjustments')}
          </p>
        ) : (
          <Table aria-label={t('billing.accounts.chargeDetail.adjustments')}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.date')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.kind')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.amount')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.reason')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {charge.adjustments.map((adjustment) => (
                <TableRow
                  key={adjustment.id}
                  data-charge-adjustment={adjustment.kind}
                >
                  <TableCell>
                    {labels.instantDate(adjustment.posted_at)}
                  </TableCell>
                  <TableCell>
                    {labels.adjustmentKind(adjustment.kind)}
                  </TableCell>
                  <TableCell>{labels.signed(adjustment.amount)}</TableCell>
                  <TableCell className="max-w-56 whitespace-normal break-words">
                    {adjustment.reason ?? '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <h3 className="font-semibold">
          {t('billing.accounts.chargeDetail.allocations')}
        </h3>
        {charge.allocations.length === 0 ? (
          <p className="text-sm">
            {t('billing.accounts.chargeDetail.noAllocations')}
          </p>
        ) : (
          <Table aria-label={t('billing.accounts.chargeDetail.allocations')}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.date')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.amount')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.accounts.chargeDetail.columns.payment')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {charge.allocations.map((allocation) => (
                <TableRow
                  key={allocation.id}
                  data-charge-allocation={allocation.id}
                >
                  <TableCell>
                    {labels.instantDate(allocation.created_at)}
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {labels.money(allocation.amount)}
                      {allocation.is_valid ? null : <ReleasedBadge />}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t(
                        'billing.accounts.chargeDetail.viewPaymentFor',
                        { date: labels.instantDate(allocation.created_at) },
                      )}
                      onClick={() => onViewPayment(allocation.payment_id)}
                    >
                      {t('billing.accounts.chargeDetail.viewPayment')}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
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
          className="max-h-[90vh] overflow-y-auto sm:max-w-2xl [&>*]:min-w-0"
          onOpenAutoFocus={returnFocus.onOpenAutoFocus}
          onCloseAutoFocus={returnFocus.onCloseAutoFocus}
        >
          <DialogHeader>
            <DialogTitle>
              {t('billing.accounts.chargeDetail.title')}
            </DialogTitle>
            <DialogDescription>
              {t('billing.accounts.chargeDetail.help')}
            </DialogDescription>
          </DialogHeader>
          <p role="status" className="text-sm font-medium">
            {notice}
          </p>
          {content}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('billing.accounts.chargeDetail.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
