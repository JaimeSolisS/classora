import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ExpenseStatus } from './api';

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

// An expense's status (M7-12b B1): the word always shows; the border style
// only reinforces it (dashed for void, heavy for overdue).
const STATUS_STYLE: Record<ExpenseStatus, string> = {
  unpaid: 'border-border bg-background',
  partially_paid: 'border-border bg-muted',
  paid: 'border-border bg-muted',
  overdue: 'border-2 border-foreground bg-background',
  void: 'border-dashed border-border bg-muted',
};

export function ExpenseStatusBadge({ status }: { status: ExpenseStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="expense-status-badge"
      data-status={status}
      className={cn(BADGE, STATUS_STYLE[status])}
    >
      {t(`finance.expenses.statuses.${status}`)}
    </span>
  );
}
