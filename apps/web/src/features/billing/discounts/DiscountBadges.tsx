import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { DiscountKind, DiscountStatus } from './api';

// A discount's kind and status badges (#367 B2): text badges (the status
// never relies on color), on the PriceStatusBadge style.

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function DiscountKindBadge({ kind }: { kind: DiscountKind }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="discount-kind-badge"
      className={cn(BADGE, 'border-border')}
    >
      {t(`billing.discounts.kinds.${kind}`)}
    </span>
  );
}

export function DiscountStatusBadge({ status }: { status: DiscountStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="discount-status-badge"
      className={cn(
        BADGE,
        status === 'current'
          ? 'border-foreground bg-background'
          : status === 'upcoming'
            ? 'border-dashed border-border bg-background'
            : 'border-border bg-muted',
      )}
    >
      {t(`billing.discounts.status.${status}`)}
    </span>
  );
}
