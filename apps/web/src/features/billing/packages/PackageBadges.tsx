import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { PackageStatus } from './api';

// A sold package's status and a product's active state (#377 B1, C1):
// text badges (never color alone), on the PriceStatusBadge style.

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function PackageStatusBadge({ status }: { status: PackageStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="package-status-badge"
      className={cn(
        BADGE,
        status === 'active'
          ? 'border-foreground bg-background'
          : 'border-border bg-muted',
      )}
    >
      {t(`billing.packages.status.${status}`)}
    </span>
  );
}

export function ProductActiveBadge({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="product-active-badge"
      className={cn(
        BADGE,
        active ? 'border-foreground bg-background' : 'border-border bg-muted',
      )}
    >
      {active
        ? t('billing.packages.products.active')
        : t('billing.packages.products.inactive')}
    </span>
  );
}
