import { useTranslation } from 'react-i18next';
import type { ReportBasis } from '../api';
import { Money } from '../parts';
import { BasisSwitch } from '../RangeControl';
import type { RevenueTrend, TrendFigures } from './api';
import { RevenueTrendChart } from './charts/RevenueTrendChart';
import { isPartialMonth, isZeroAmount } from './format';
import { MonthLabel } from './Figures';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { KpiTable } from './KpiTable';
import { useRevenueTrend } from './queries';
import { kpiState, type KpiState } from './state';

// K1 "Revenue and result by month" (M8-09b C1; _docs/kpis.md K1): its own
// basis switch, the chart, then a row per month (partial months marked)
// and a bold totals row, every amount as the API sent it. The accrual
// restatement note shows on the accrual basis only.

const L = 'reports.kpis.revenueTrend';

const FIGURES = [
  'revenue_total',
  'direct_tutor_cost_total',
  'gross_profit',
  'operating_expenses_total',
  'net_result',
] as const satisfies readonly (keyof TrendFigures)[];

function isEmptyTrend(trend: RevenueTrend): boolean {
  return [trend.totals, ...trend.months].every((row) =>
    FIGURES.every((key) => isZeroAmount(row[key])),
  );
}

export interface RevenueTrendViewProps {
  state: KpiState<RevenueTrend>;
  basis: ReportBasis;
  /** Shows the basis switch (the page writes it to the URL). */
  onBasis?: (basis: ReportBasis) => void;
  /** A fixed chart width (tests). */
  chartWidth?: number;
}

export function RevenueTrendView({
  state,
  basis,
  onBasis,
  chartWidth,
}: RevenueTrendViewProps) {
  const { t } = useTranslation();
  const cells = (row: TrendFigures) =>
    FIGURES.map((key) => <Money key={key} amount={row[key]} />);
  return (
    <KpiSection
      kpi="revenue-trend"
      title={t(`${L}.title`)}
      controls={
        onBasis ? <BasisSwitch basis={basis} onBasis={onBasis} /> : undefined
      }
      state={state}
      isEmpty={isEmptyTrend}
      rows={5}
    >
      {(trend) => (
        <div
          className="flex min-w-0 flex-col gap-4"
          data-kpi-basis-shown={trend.basis}
        >
          <RevenueTrendChart trend={trend} width={chartWidth} />
          <KpiTable
            data="revenue-trend"
            caption={t(`${L}.caption`)}
            columns={[
              t(`${L}.columns.month`),
              t(`${L}.columns.revenue`),
              t(`${L}.columns.cost`),
              t(`${L}.columns.grossProfit`),
              t(`${L}.columns.expenses`),
              t(`${L}.columns.net`),
            ]}
            rows={trend.months.map((month) => ({
              key: month.month,
              header: (
                <MonthLabel
                  month={month.month}
                  from={month.from}
                  to={month.to}
                  partial={isPartialMonth(month)}
                />
              ),
              cells: cells(month),
            }))}
            total={{
              key: 'totals',
              header: t(`${L}.totals`),
              cells: cells(trend.totals),
            }}
          />
          {trend.basis === 'accrual' ? (
            <p className={NOTE_CLASS} data-kpi-note="accrual">
              {t('reports.profitability.notes.accrual')}
            </p>
          ) : null}
        </div>
      )}
    </KpiSection>
  );
}

export interface RevenueTrendSectionProps {
  from: string;
  to: string;
  basis: ReportBasis;
  onBasis?: (basis: ReportBasis) => void;
}

/** K1 with its own query (#127 mounts it as a widget). */
export function RevenueTrendSection({
  from,
  to,
  basis,
  onBasis,
}: RevenueTrendSectionProps) {
  const query = useRevenueTrend({ basis, from, to });
  return (
    <RevenueTrendView state={kpiState(query)} basis={basis} onBasis={onBasis} />
  );
}
