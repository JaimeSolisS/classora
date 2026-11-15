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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { useAccountLabels } from '../accounts/labels';
import type { StoredPreview, WithdrawalDetail } from './api';
import { useWithdrawalLabels } from './labels';
import { PreviewSnapshot } from './WithdrawalSettlement';

// A withdrawal's drawer (#387 B4), without any query: the outcome, the
// applied credit and the cash refunded, the reason, who confirmed it and
// when, the stored preview read-only, each credited charge with "View
// charge" (the account page opens the charge's detail with its
// `withdrawal` adjustment), and the refunds, which the payment details
// list as "From a withdrawal". The API keeps only the confirming user's
// id, so the actor reads "you" or "another staff member" (#387 D5).

export type WithdrawalDetailState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; withdrawal: WithdrawalDetail };

export interface WithdrawalDetailDialogProps {
  open: boolean;
  state: WithdrawalDetailState;
  /** The signed-in user's id (the actor line). */
  currentUserId: string | null;
  onViewCharge: (chargeId: string) => void;
  onClose: () => void;
}

export function WithdrawalDetailDialog({
  open,
  state,
  currentUserId,
  onViewCharge,
  onClose,
}: WithdrawalDetailDialogProps) {
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {open ? (
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-4xl [&>*]:min-w-0"
          onOpenAutoFocus={returnFocus.onOpenAutoFocus}
          onCloseAutoFocus={returnFocus.onCloseAutoFocus}
        >
          <DialogHeader>
            <DialogTitle>{t('billing.withdrawals.detail.title')}</DialogTitle>
            <DialogDescription>
              {t('billing.withdrawals.detail.help')}
            </DialogDescription>
          </DialogHeader>
          {state.status === 'loading' ? (
            <LoadingState rows={6} />
          ) : state.status === 'error' ? (
            <ErrorAlert error={state.error} onRetry={state.onRetry} />
          ) : (
            <Detail
              withdrawal={state.withdrawal}
              currentUserId={currentUserId}
              onViewCharge={onViewCharge}
            />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('billing.withdrawals.detail.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function Detail({
  withdrawal,
  currentUserId,
  onViewCharge,
}: {
  withdrawal: WithdrawalDetail;
  currentUserId: string | null;
  onViewCharge: (chargeId: string) => void;
}) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const labels = useWithdrawalLabels();
  const preview = withdrawal.preview as unknown as StoredPreview;
  // `applied` follows the lines' order (#101 C2 step 8).
  const applied = (preview.applied ?? [])
    .map((item, index) => ({ ...item, kind: preview.lines?.[index]?.kind }))
    .filter((item) => !/^0+(\.0+)?$/.test(item.applied_credit));
  const rows: { label: string; value: string }[] = [
    {
      label: t('billing.withdrawals.detail.outcome'),
      value: labels.outcome(withdrawal.outcome),
    },
    {
      label: t('billing.withdrawals.detail.credit'),
      value: money.money(withdrawal.amount),
    },
    {
      label: t('billing.withdrawals.detail.refund'),
      value: money.money(withdrawal.refund_amount),
    },
    {
      label: t('billing.withdrawals.detail.confirmed'),
      value: t(
        withdrawal.confirmed_by_user_id === currentUserId
          ? 'billing.withdrawals.detail.byYou'
          : 'billing.withdrawals.detail.byOther',
        { date: money.instantDate(withdrawal.created_at) },
      ),
    },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-6" data-withdrawal-detail>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.label} className="flex flex-wrap gap-x-2">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="font-medium">{row.value}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-col gap-1 text-sm">
        <h3 className="font-semibold">
          {t('billing.withdrawals.detail.reason')}
        </h3>
        <p className="break-words whitespace-pre-line" data-withdrawal-reason>
          {withdrawal.reason}
        </p>
      </div>
      <section className="flex flex-col gap-2 text-sm">
        <h3 className="font-semibold">
          {t('billing.withdrawals.detail.applied')}
        </h3>
        {applied.length === 0 ? (
          <p>{t('billing.withdrawals.detail.nothingApplied')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {applied.map((item) => {
              const label = item.kind ? money.chargeKind(item.kind) : '';
              return (
                <li
                  key={item.charge_id}
                  className="flex flex-wrap items-center gap-3"
                  data-applied-charge={item.charge_id}
                >
                  <span>
                    {t('billing.withdrawals.detail.appliedLine', {
                      kind: label,
                      amount: money.money(item.applied_credit),
                    })}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={t('billing.withdrawals.detail.viewChargeFor', {
                      kind: label,
                    })}
                    onClick={() => onViewCharge(item.charge_id)}
                  >
                    {t('billing.withdrawals.detail.viewCharge')}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
        {withdrawal.refund_ids.length > 0 ? (
          <p data-withdrawal-refunds>
            {t('billing.withdrawals.detail.refunds', {
              count: withdrawal.refund_ids.length,
              amount: money.money(withdrawal.refund_amount),
            })}
          </p>
        ) : null}
        {withdrawal.automatic_credit_adjustment_ids.length > 0 ? (
          <p>
            {t('billing.withdrawals.detail.automatic', {
              count: withdrawal.automatic_credit_adjustment_ids.length,
            })}
          </p>
        ) : null}
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">
          {t('billing.withdrawals.detail.snapshot')}
        </h3>
        <PreviewSnapshot preview={preview} />
      </section>
    </div>
  );
}
