import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

// The status badge of a family (#172 H1, H3): "Active" / "Inactive" as text
// (Spanish agrees with "familia"); the border only reinforces it (WCAG
// 1.4.1).

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function FamilyStatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="family-status-badge"
      className={cn(
        BADGE,
        active ? 'border-border bg-background' : 'border-border bg-muted',
      )}
    >
      {active ? t('families.status.active') : t('families.status.inactive')}
    </span>
  );
}
