import { useTranslation } from 'react-i18next';
import type { FamilyPackageDetail } from './api';
import { usePortalLabels } from './labels';
import { FamilyPackageBadges } from './PackageBadges';
import { DetailTerms, PortalDialog, type DetailState } from './PortalDialog';

// A package's usage in the family portal (#388 F2; #102 T3 GET
// …/packages/{package_id}): what is left, then every movement (date, kind,
// ± units; a `consume` names the class date). Read-only.

export function PackageUsageDialog({
  open,
  state,
  today,
  onClose,
}: {
  open: boolean;
  state: DetailState<FamilyPackageDetail>;
  /** The summary's "today" (the "Expires soon" mark). */
  today: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const labels = usePortalLabels();
  return (
    <PortalDialog
      open={open}
      title={t('familyBilling.packages.usageTitle')}
      description={t('familyBilling.packages.usageHelp')}
      closeLabel={t('familyBilling.close')}
      state={state}
      onClose={onClose}
    >
      {(pkg) => (
        <div className="flex flex-col gap-4" data-package-detail={pkg.id}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="min-w-0 font-medium break-words">
              {pkg.product.name}
            </p>
            <FamilyPackageBadges pkg={pkg} today={today} />
          </div>
          <DetailTerms
            items={[
              [t('familyBilling.columns.child'), pkg.student.name],
              [t('familyBilling.packages.subject'), labels.packageSubject(pkg)],
              [
                t('familyBilling.packages.remaining'),
                labels.packageBalance(pkg),
              ],
              [
                t('familyBilling.packages.expires'),
                labels.date(pkg.expires_on),
              ],
            ]}
          />
          <h3 className="font-semibold">
            {t('familyBilling.packages.movements')}
          </h3>
          {pkg.transactions.length === 0 ? (
            <p className="text-sm">{t('familyBilling.packages.usageEmpty')}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {pkg.transactions.map((movement) => (
                <li
                  key={movement.id}
                  data-package-transaction={movement.kind}
                  className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 rounded-md border border-border p-3"
                >
                  <span className="min-w-0 break-words">
                    {labels.transactionKind(movement)}
                    <span className="block text-sm text-muted-foreground">
                      {labels.instantDate(movement.created_at)}
                    </span>
                  </span>
                  <span>{labels.signedUnits(pkg, movement)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </PortalDialog>
  );
}
