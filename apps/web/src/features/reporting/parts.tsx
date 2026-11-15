import { TrendingDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { formatMoney } from '@/lib/money';
import { cn } from '@/lib/utils';
import { isNegative } from './rules';

// Shared pieces of the financial report page (M7-14b B2, B6).

/** One section's data (B6). `idle`: the range is invalid, nothing asked. */
export type ReportState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      data: T;
      /** A refetch failed: the figures may be out of date. */
      refreshError?: unknown;
      /** A refetch is in flight: "Volver a cargar" waits for it. */
      refreshing?: boolean;
      onRefresh?: () => void;
    };

/**
 * An amount from the API, never recomputed. A negative one keeps its minus
 * sign and gets an icon too, so colour is never the only cue (B2).
 */
export function Money({
  amount,
  className,
}: {
  amount: string;
  className?: string;
}) {
  const negative = isNegative(amount);
  return (
    <span
      data-money
      data-negative={negative || undefined}
      className={cn(
        'inline-flex items-center justify-end gap-1 tabular-nums whitespace-nowrap',
        negative && 'text-destructive',
        className,
      )}
    >
      {negative ? (
        <TrendingDown aria-hidden="true" className="size-4 shrink-0" />
      ) : null}
      {formatMoney(amount)}
    </span>
  );
}

/** Loading, error and the out-of-date alert around a section's figures. */
export function ReportStatus<T>({
  state,
  label,
  rows = 4,
  children,
}: {
  state: ReportState<T>;
  label: string;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  const { t } = useTranslation();
  if (state.status === 'idle') return null;
  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={rows} label={label} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  return (
    <>
      {state.refreshError ? (
        <ErrorAlert
          error={state.refreshError}
          message={t('reports.stale')}
          onRetry={state.refreshing ? undefined : state.onRefresh}
          retryLabel={t('reports.reload')}
        />
      ) : null}
      {children(state.data)}
    </>
  );
}

/** A label and an amount on one line (the balances, the cash summary). */
export function FigureRow({
  label,
  amount,
  strong,
  detail,
  data,
}: {
  label: ReactNode;
  amount: string;
  strong?: boolean;
  detail?: ReactNode;
  data?: string;
}) {
  return (
    <div
      data-figure={data}
      className={cn(
        'flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-1',
        strong && 'border-t border-border pt-2 font-semibold',
      )}
    >
      <dt className="min-w-0 [overflow-wrap:anywhere]">
        {label}
        {detail ? (
          <span className="ml-1 text-sm font-normal text-muted-foreground">
            {detail}
          </span>
        ) : null}
      </dt>
      <dd>
        <Money amount={amount} />
      </dd>
    </div>
  );
}
