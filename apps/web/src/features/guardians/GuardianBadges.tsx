import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

// The account badge of a guardian (#34 I1, I3, I4): "Has account" / "No
// account" as text; the border only reinforces it (WCAG 1.4.1). Status and
// minor/adult reuse the student badges.

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function AccountBadge({ hasAccount }: { hasAccount: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="guardian-account-badge"
      className={cn(
        BADGE,
        hasAccount ? 'border-border bg-background' : 'border-dashed bg-muted',
      )}
    >
      {hasAccount ? t('guardians.account.has') : t('guardians.account.none')}
    </span>
  );
}
