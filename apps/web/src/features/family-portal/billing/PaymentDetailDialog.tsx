import { useTranslation } from 'react-i18next';
import {
  ReleasedBadge,
  ReversedBadge,
} from '@/features/billing/accounts/AccountBadges';
import { ReceiptLink } from '@/features/billing/accounts/ReceiptLink';
import type { FamilyCharge, FamilyPaymentDetail } from './api';
import { usePortalLabels } from './labels';
import { DetailTerms, PortalDialog, type DetailState } from './PortalDialog';

// A payment's detail in the family portal (#388 E3): its amounts and the
// receipt, every allocation it has had (charge, child, amount; "Released"
// for an invalid one), the reversal (its date only) and the refunds (date,
// amount, method, reference). Never a note, a reason or who did it (E4).

export interface PaymentDetailValue {
  payment: FamilyPaymentDetail;
  /** Charge id → charge (null while they load). */
  charges: Readonly<Record<string, FamilyCharge>> | null;
}

export function PaymentDetailDialog({
  open,
  familyId,
  state,
  studentNames,
  onClose,
}: {
  open: boolean;
  familyId: string;
  state: DetailState<PaymentDetailValue>;
  /** Student id → name (the summary's students). */
  studentNames: Readonly<Record<string, string>>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const labels = usePortalLabels();
  return (
    <PortalDialog
      open={open}
      title={t('familyBilling.paymentDetail.title')}
      description={t('familyBilling.paymentDetail.help')}
      closeLabel={t('familyBilling.close')}
      state={state}
      onClose={onClose}
    >
      {({ payment, charges }) => (
        <div className="flex flex-col gap-4" data-payment-detail={payment.id}>
          {payment.is_reversed ? (
            <p>
              <ReversedBadge />
            </p>
          ) : null}
          <DetailTerms
            items={[
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
                t('familyBilling.payments.applied'),
                labels.money(payment.allocated_amount),
              ],
              [
                t('familyBilling.payments.credit'),
                labels.money(payment.unallocated_amount),
              ],
              [
                t('billing.accounts.payments.columns.reference'),
                payment.reference ?? '—',
              ],
              [
                t('billing.accounts.payments.columns.payer'),
                payment.payer_name ?? '—',
              ],
              [
                t('billing.accounts.payments.columns.receipt'),
                <ReceiptLink
                  key="receipt"
                  familyId={familyId}
                  receipt={payment.receipt}
                  linkClassName="inline-flex min-h-11 items-center"
                />,
              ],
            ]}
          />
          {payment.reversal ? (
            <div className="flex flex-col gap-2" data-payment-reversal="">
              <h3 className="font-semibold">
                {t('familyBilling.paymentDetail.reversal')}
              </h3>
              <DetailTerms
                items={[
                  [
                    t('familyBilling.paymentDetail.reversedOn'),
                    labels.instantDate(payment.reversal.reversed_at),
                  ],
                ]}
              />
            </div>
          ) : null}
          {payment.refunds.length > 0 ? (
            <>
              <h3 className="font-semibold">
                {t('familyBilling.paymentDetail.refunds')}
              </h3>
              <ul className="flex flex-col gap-2">
                {payment.refunds.map((refund) => (
                  <li
                    key={refund.id}
                    data-payment-refund={refund.id}
                    className="rounded-md border border-border p-3"
                  >
                    <DetailTerms
                      items={[
                        [
                          t('billing.accounts.payments.refundColumns.date'),
                          labels.date(refund.refunded_on),
                        ],
                        [
                          t('billing.accounts.payments.refundColumns.amount'),
                          labels.money(refund.amount),
                        ],
                        [
                          t('billing.accounts.payments.refundColumns.method'),
                          labels.method(refund.payment_method_code),
                        ],
                        [
                          t(
                            'billing.accounts.payments.refundColumns.reference',
                          ),
                          refund.reference ?? '—',
                        ],
                      ]}
                    />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <h3 className="font-semibold">
            {t('familyBilling.paymentDetail.allocations')}
          </h3>
          {payment.allocations.length === 0 ? (
            <p className="text-sm">
              {t('familyBilling.paymentDetail.noAllocations')}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {payment.allocations.map((allocation) => {
                const charge = charges?.[allocation.charge_id];
                return (
                  <li
                    key={allocation.id}
                    data-payment-allocation={allocation.id}
                    className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 rounded-md border border-border p-3"
                  >
                    <span className="min-w-0 break-words">
                      {charge
                        ? labels.chargeLabel(charge)
                        : t('familyBilling.paymentDetail.charge')}
                      <span className="block text-sm text-muted-foreground">
                        {charge?.student.name ??
                          studentNames[allocation.student_id] ??
                          '—'}
                        {charge ? ` · ${labels.date(charge.service_date)}` : ''}
                      </span>
                    </span>
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {labels.money(allocation.amount)}
                      {allocation.is_valid ? null : <ReleasedBadge />}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </PortalDialog>
  );
}
