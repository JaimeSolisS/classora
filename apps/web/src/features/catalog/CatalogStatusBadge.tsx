import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

// Active or inactive as a text badge (#32 H2): the word carries the meaning,
// the border style only reinforces it (never colour alone, WCAG 1.4.1).

export function CatalogStatusBadge({
  active,
  className,
}: {
  active: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="catalog-status-badge"
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        active
          ? 'border-border bg-background'
          : 'border-dashed border-border bg-muted',
        className,
      )}
    >
      {active ? t('catalog.status.active') : t('catalog.status.inactive')}
    </span>
  );
}
