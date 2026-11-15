import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AXIS_TICK,
  CHART_HEIGHT,
  GRID_STROKE,
  TOOLTIP_PROPS,
} from '@/features/progress/charts/chartStyle';
import type { StudentRetention } from '../api';
import { useKpiFormatters } from '../format';
import { ChartDataTable } from './parts';

// K5's chart (M8-09b E3, the #322 rules): one bar of active students per
// month of the range, in --chart-1, with no animation, the accessibility
// layer on and a visually hidden data table as the text alternative. The
// counts are never negative, so one series needs no other cue.

const L = 'reports.kpis.retention';

export interface ActiveStudentsChartProps {
  retention: StudentRetention;
  /** A fixed width instead of the container's (tests). */
  width?: number;
}

export function ActiveStudentsChart({
  retention,
  width,
}: ActiveStudentsChartProps) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const captionId = useId();
  const series = t(`${L}.series`);
  const data = retention.months.map((month) => ({
    key: month.month,
    label: format.month(month.month),
    active: month.active_students,
  }));

  const chart = (
    <BarChart
      data={data}
      accessibilityLayer
      margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
      {...(width !== undefined ? { width, height: CHART_HEIGHT } : {})}
    >
      <CartesianGrid vertical={false} stroke={GRID_STROKE} />
      <XAxis
        dataKey="label"
        tick={AXIS_TICK}
        stroke={GRID_STROKE}
        interval="preserveStartEnd"
      />
      <YAxis allowDecimals={false} tick={AXIS_TICK} stroke={GRID_STROKE} />
      <Tooltip {...TOOLTIP_PROPS} />
      <Bar
        dataKey="active"
        name={series}
        fill="var(--chart-1)"
        isAnimationActive={false}
        maxBarSize={40}
      />
    </BarChart>
  );

  return (
    <figure
      aria-labelledby={captionId}
      className="flex min-w-0 flex-col gap-2"
      data-kpi-chart="student-retention"
    >
      <figcaption id={captionId} className="text-sm font-medium">
        {t(`${L}.chartCaption`)}
      </figcaption>
      <div className="min-w-0">
        {width !== undefined ? (
          chart
        ) : (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            {chart}
          </ResponsiveContainer>
        )}
      </div>
      <ChartDataTable
        caption={t(`${L}.chartTable`)}
        columns={[t(`${L}.columns.month`), series]}
        rows={retention.months.map((month) => ({
          key: month.month,
          cells: [
            format.month(month.month),
            format.count(month.active_students),
          ],
        }))}
      />
    </figure>
  );
}
