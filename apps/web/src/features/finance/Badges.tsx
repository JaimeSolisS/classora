import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// The category and vendor badges (M7-11b B1, C1): text badges, the word
// carries the meaning and the border style only reinforces it (never colour
// alone, WCAG 1.4.1), on the CatalogStatusBadge style.

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function StateBadge({
  active,
  children,
}: {
  active: boolean;
  children: ReactNode;
}) {
  return (
    <span
      data-slot="finance-state-badge"
      data-active={active}
      className={cn(
        BADGE,
        active
          ? 'border-border bg-background'
          : 'border-dashed border-border bg-muted',
      )}
    >
      {children}
    </span>
  );
}

export function DefaultBadge({ children }: { children: ReactNode }) {
  return (
    <span
      data-slot="finance-default-badge"
      className={cn(BADGE, 'border-foreground bg-background')}
    >
      {children}
    </span>
  );
}
