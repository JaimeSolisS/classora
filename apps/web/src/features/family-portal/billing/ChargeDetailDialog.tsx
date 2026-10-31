import { useTranslation } from 'react-i18next';
import { ReleasedBadge } from '@/features/billing/accounts/AccountBadges';
import { DueDateCell } from '@/features/billing/due-dates/DueDateCell';
import { ChargeStatusBadge } from '@/features/billing/tuition/TuitionBadges';
import type { FamilyChargeDetail } from './api';
import { usePortalLabels } from './labels';
import { DetailTerms, PortalDialog, type DetailState } from './PortalDialog';

// A charge's detail in the family portal (#388 D4; #100 GET
// …/charges/{charge_id} with #102 T4's discount lines): the list price,
// each discount line (name, percent or fixed value, amount), the amount
// after discounts, each adjustment (kind, date, signed amount; never its
// reason), the payments applied (date, amount; "Released" for a released
// one), then net, paid, open, the due date (with the #382 "changed" marker,
// never a reason or history) and the status. Read-only.

export function ChargeDetailDialog({
  open,
  state,
  onClose,
}: {
  open: boolean;
  state: DetailState<FamilyChargeDetail>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const labels = usePortalLabels();
  return (
    <PortalDialog
      open={open}
      title={t('familyBilling.chargeDetail.title')}
      description={t('familyBilling.chargeDetail.help')}
      closeLabel={t('familyBilling.close')}
      state={state}
      onClose={onClose}
    >
      {(charge) => (
        <div className="flex flex-col gap-4" data-charge-detail={charge.id}>
          <p className="font-medium break-words">
            {labels.chargeLabel(charge)}
            {/* A charge without a description is already named by its kind. */}
            {labels.chargeLabel(charge) ===
            labels.chargeKind(charge.kind) ? null : (
              <span className="block text-sm font-normal text-muted-foreground">
                {labels.chargeKind(charge.kind)}
              </span>
            )}
          </p>
          <DetailTerms
            items={[
              [t('familyBilling.columns.child'), charge.student.name],
              [
                t('billing.accounts.charges.columns.serviceDate'),
                labels.date(charge.service_date),
              ],
              [
                t('familyBilling.chargeDetail.listPrice'),
                labels.money(charge.list_amount),
              ],
            ]}
          />
          <h3 className="font-semibold">
            {t('familyBilling.chargeDetail.discounts')}
          </h3>
          {charge.discounts.length === 0 ? (
            <p className="text-sm">
              {t('familyBilling.chargeDetail.noDiscounts')}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {charge.discounts.map((line) => (
                <li
                  key={line.position}
                  data-charge-discount={line.kind}
                  className="flex flex-wrap justify-between gap-x-4 gap-y-1 rounded-md border border-border p-3"
                >
                  <span className="min-w-0 break-words">
                    {labels.discountName(line)}{' '}
                    <span className="text-sm text-muted-foreground">
                      ({labels.discountValue(line)})
                    </span>
                  </span>
                  <span>{labels.money(line.amount)}</span>
                </li>
              ))}
            </ul>
          )}
          <DetailTerms
            items={[
              [
                t('familyBilling.chargeDetail.afterDiscounts'),
                labels.money(charge.amount),
              ],
            ]}
          />
          <h3 className="font-semibold">
            {t('familyBilling.chargeDetail.adjustments')}
          </h3>
          {charge.adjustments.length === 0 ? (
            <p className="text-sm">
              {t('familyBilling.chargeDetail.noAdjustments')}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {charge.adjustments.map((adjustment) => (
                <li
                  key={adjustment.id}
                  data-charge-adjustment={adjustment.kind}
                  className="flex flex-wrap justify-between gap-x-4 gap-y-1 rounded-md border border-border p-3"
                >
                  <span className="min-w-0 break-words">
                    {labels.adjustmentKind(adjustment.kind)}
                    <span className="block text-sm text-muted-foreground">
                      {labels.instantDate(adjustment.posted_at)}
                    </span>
                  </span>
                  <span>{labels.signed(adjustment.amount)}</span>
                </li>
              ))}
            </ul>
          )}
          <h3 className="font-semibold">
            {t('familyBilling.chargeDetail.payments')}
          </h3>
          {charge.allocations.length === 0 ? (
            <p className="text-sm">
              {t('familyBilling.chargeDetail.noPayments')}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {charge.allocations.map((allocation) => (
                <li
                  key={allocation.id}
                  data-charge-allocation={allocation.id}
                  className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-md border border-border p-3"
                >
                  <span>{labels.instantDate(allocation.created_at)}</span>
                  <span className="inline-flex flex-wrap items-center gap-2">
                    {labels.money(allocation.amount)}
                    {allocation.is_valid ? null : <ReleasedBadge />}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <DetailTerms
            items={[
              [
                t('familyBilling.chargeDetail.net'),
                <span key="net" data-charge-net={charge.net}>
                  {labels.money(charge.net)}
                </span>,
              ],
              [t('familyBilling.charges.paid'), labels.money(charge.allocated)],
              [
                t('billing.accounts.charges.columns.open'),
                labels.money(charge.open),
              ],
              [
                t('billing.accounts.charges.columns.dueDate'),
                <DueDateCell key="due" charge={charge} touch />,
              ],
              [
                t('billing.accounts.charges.columns.status'),
                <ChargeStatusBadge key="status" status={charge.status} />,
              ],
            ]}
          />
        </div>
      )}
    </PortalDialog>
  );
}
