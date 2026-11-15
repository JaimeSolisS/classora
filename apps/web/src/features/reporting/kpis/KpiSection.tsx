import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ReportStatus } from '../parts';
import type { KpiState } from './state';

// One KPI section's frame (M8-09b C7): its heading, an optional subtitle and
// controls, then a skeleton while it loads, an inline error with "Try
// again" for its own query, the out-of-date alert after a failed refetch,
// and "No activity in this period" when every figure is zero. A hidden
// state (a 403) renders nothing, so the rest of the page stays.

export type KpiCode =
  | 'revenue-trend'
  | 'balances'
  | 'course-profitability'
  | 'room-utilization'
  | 'student-retention'
  | 'tutor-performance';

export const NOTE_CLASS = 'text-sm text-muted-foreground';

export function KpiSection<T>({
  kpi,
  title,
  subtitle,
  controls,
  state,
  isEmpty,
  rows = 4,
  children,
}: {
  kpi: KpiCode;
  title: string;
  subtitle?: ReactNode;
  controls?: ReactNode;
  state: KpiState<T>;
  isEmpty: (data: T) => boolean;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  if (state.status === 'hidden') return null;
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-4 rounded-lg border border-border bg-card p-4 text-card-foreground"
      data-kpi-section={kpi}
      data-kpi-status={state.status}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h2 id={titleId} className="text-xl font-semibold">
          {title}
        </h2>
        {subtitle ? <p className={NOTE_CLASS}>{subtitle}</p> : null}
      </div>
      {controls}
      <ReportStatus state={state} label={t('feedback.loading')} rows={rows}>
        {(data) =>
          isEmpty(data) ? (
            <p data-kpi-empty>{t('reports.kpis.empty')}</p>
          ) : (
            children(data)
          )
        }
      </ReportStatus>
    </section>
  );
}
