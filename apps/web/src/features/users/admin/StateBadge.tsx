import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { UserState } from './api';

// A user's state as a text badge (#31 J3): the word carries the meaning, the
// border style only reinforces it (never colour alone, WCAG 1.4.1).

const STYLE: Record<UserState, string> = {
  active: 'border-border bg-background',
  pending: 'border-dashed border-border bg-background',
  suspended: 'border-destructive bg-background',
  archived: 'border-border bg-muted',
};

export function StateBadge({
  state,
  className,
}: {
  state: UserState;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="state-badge"
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        STYLE[state],
        className,
      )}
    >
      {t(`userStates.${state}`)}
    </span>
  );
}
