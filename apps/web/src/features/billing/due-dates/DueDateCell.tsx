import { CalendarClock } from 'lucide-react';
import { Tooltip } from 'radix-ui';
import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ChargeItem } from '../accounts/api';
import { dueDateChanged } from './rules';

// A charge's due date (#382 D1): the effective date and, when it was moved
// from the posted one, a "changed" marker. The marker is a small button
// whose name says it changed and the original date (visually hidden text,
// so touch and screen-reader users get it too); hovering or focusing it
// shows "Original: <date>" in a tooltip (Radix, from the pinned radix-ui
// package; Escape closes it). Reusable by the family portal (#102); #388
// passes `touch` for a 44 px marker on the phone layout.

export function DueDateCell({
  charge,
  touch = false,
}: {
  charge: Pick<ChargeItem, 'due_date' | 'original_due_date'>;
  /** A 44 × 44 px marker (the family portal's touch targets, #388 I1). */
  touch?: boolean;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const date = formatLocalDate(charge.due_date);
  if (!dueDateChanged(charge)) {
    return <>{date}</>;
  }
  const original = formatLocalDate(charge.original_due_date);
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {date}
      <Tooltip.Provider delayDuration={200}>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <button
              type="button"
              data-due-date-changed
              className={`inline-flex ${touch ? 'size-11' : 'size-6'} items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring`}
            >
              <CalendarClock aria-hidden="true" className="size-4" />
              <span className="sr-only">
                {t('billing.dueDates.marker.label', { date: original })}
              </span>
            </button>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content
              sideOffset={4}
              className="z-50 rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
            >
              {t('billing.dueDates.marker.tooltip', { date: original })}
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
    </span>
  );
}
