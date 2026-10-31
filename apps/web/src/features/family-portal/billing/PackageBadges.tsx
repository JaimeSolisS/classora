import { useTranslation } from 'react-i18next';
import { PackageStatusBadge } from '@/features/billing/packages/PackageBadges';
import type { FamilyPackage } from './api';
import { usePortalLabels } from './labels';
import { expiresSoon } from './rules';

// A package's status (the #377 badge; "Other" for a status the client
// doesn't know) and "Expires soon" for an active package that expires
// within 14 days (#388 F1). Text badges, never colour alone.

const BADGE =
  'inline-flex items-center rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';
const KNOWN = new Set([
  'active',
  'exhausted',
  'expired',
  'cancelled',
  'withdrawn',
]);

export function FamilyPackageBadges({
  pkg,
  today,
}: {
  pkg: Pick<FamilyPackage, 'status' | 'expires_on'>;
  today: string;
}) {
  const { t } = useTranslation();
  const labels = usePortalLabels();
  return (
    <span className="inline-flex flex-wrap gap-2">
      {KNOWN.has(pkg.status) ? (
        <PackageStatusBadge status={pkg.status} />
      ) : (
        <span data-slot="package-status-badge" className={BADGE}>
          {labels.packageStatus(pkg.status)}
        </span>
      )}
      {expiresSoon(pkg, today) ? (
        <span data-package-expires-soon="" className={BADGE}>
          {t('familyBilling.packages.expiresSoon')}
        </span>
      ) : null}
    </span>
  );
}
