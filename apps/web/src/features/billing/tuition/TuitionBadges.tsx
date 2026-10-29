import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ChargeStatus, TuitionPlanStatus } from './api';
import type { PeriodDisplay } from './rules';

// A plan's status, an upcoming period's state and a charge's status
// (#374 C1, E2): text badges (never color alone), on the PriceStatusBadge
// style.

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function PlanStatusBadge({ status }: { status: TuitionPlanStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="plan-status-badge"
      className={cn(
        BADGE,
        status === 'active'
          ? 'border-foreground bg-background'
          : status === 'scheduled'
            ? 'border-dashed border-border bg-background'
            : 'border-border bg-muted',
      )}
    >
      {t(`billing.tuition.status.${status}`)}
    </span>
  );
}

export function PeriodStateBadge({ display }: { display: PeriodDisplay }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="period-state-badge"
      className={cn(
        BADGE,
        display === 'charged' || display === 'due'
          ? 'border-foreground bg-background'
          : display === 'upcoming'
            ? 'border-dashed border-border bg-background'
            : 'border-border bg-muted',
      )}
    >
      {t(`billing.tuition.periodStates.${display}`)}
    </span>
  );
}

export function ChargeStatusBadge({ status }: { status: ChargeStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="charge-status-badge"
      className={cn(
        BADGE,
        status === 'paid'
          ? 'border-foreground bg-background'
          : status === 'void'
            ? 'border-border bg-muted line-through'
            : 'border-border bg-background',
      )}
    >
      {t(`billing.tuition.chargeStatus.${status}`)}
    </span>
  );
}
