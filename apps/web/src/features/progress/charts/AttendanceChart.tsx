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
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import type { ProgressAttendanceMonth, StudentProgress } from '../api';
import { monthLabel } from '../rules';
import { ChartFigure, DataTable } from './ChartFigure';
import { ATTENDANCE_SERIES, type AttendanceSeries } from './series';
import {
  AXIS_TICK,
  CHART_HEIGHT,
  GRID_STROKE,
  svgId,
  TOOLTIP_PROPS,
} from './chartStyle';

// "Attendance by month" (#322 C2.1, C3): a stacked bar per center-local
// month (present, late, absent, not recorded), each series told apart by a
// fill pattern (solid, diagonal hatch, dots, cross-hatch) as well as by the
// --chart-1…4 colours, with a text legend in stack order. No animation;
// the accessibility layer on. The table (Month | Present | Late | Absent |
// Not recorded | Total) has every value as text. Recharts is only imported
// under features/progress/charts/ (A3).

const COLORS: Record<AttendanceSeries, string> = {
  present: 'var(--chart-1)',
  late: 'var(--chart-2)',
  absent: 'var(--chart-3)',
  not_recorded: 'var(--chart-4)',
};

/** The SVG fill patterns, one per series (never colour alone). */
export function AttendancePatterns({ prefix }: { prefix: string }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="0"
      height="0"
      className="absolute"
    >
      <defs>
        <pattern
          id={`${prefix}-present`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
        >
          <rect width="6" height="6" style={{ fill: COLORS.present }} />
        </pattern>
        <pattern
          id={`${prefix}-late`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width="6" height="6" style={{ fill: 'var(--card)' }} />
          <rect width="3" height="6" style={{ fill: COLORS.late }} />
        </pattern>
        <pattern
          id={`${prefix}-absent`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
        >
          <rect width="6" height="6" style={{ fill: 'var(--card)' }} />
          <circle cx="3" cy="3" r="2" style={{ fill: COLORS.absent }} />
        </pattern>
        <pattern
          id={`${prefix}-not_recorded`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
        >
          <rect width="6" height="6" style={{ fill: 'var(--card)' }} />
          <path
            d="M0 0L6 6M6 0L0 6"
            style={{ stroke: COLORS.not_recorded, strokeWidth: 1.5 }}
          />
        </pattern>
      </defs>
    </svg>
  );
}

export interface AttendanceChartProps {
  progress: StudentProgress;
  /** A fixed width instead of the container's (tests). */
  width?: number;
  initialView?: 'chart' | 'table';
}

export function AttendanceChart({
  progress,
  width,
  initialView,
}: AttendanceChartProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const { formatLocalDate } = useFormatters();
  const prefix = svgId(useId());
  const { attendance } = progress;
  const months = progress.attendance_by_month;
  const label = (series: AttendanceSeries) =>
    series === 'not_recorded'
      ? t('attendance.notRecorded')
      : t(`attendance.statuses.${series}`);
  const summary = t('progress.attendance.summary', {
    from: formatLocalDate(progress.from),
    to: formatLocalDate(progress.to),
    present: attendance.present,
    late: attendance.late,
    absent: attendance.absent,
    notRecorded: attendance.not_recorded,
    attendance:
      attendance.attendance_percent === null
        ? t('progress.noDataLower')
        : t('progress.percent', { value: attendance.attendance_percent }),
  });
  const empty =
    attendance.sessions === 0 ? t('progress.attendance.empty') : null;
  const data = months.map((month: ProgressAttendanceMonth) => ({
    ...month,
    label: monthLabel(month.month, language),
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
      {ATTENDANCE_SERIES.map((series) => (
        <Bar
          key={series}
          dataKey={series}
          name={label(series)}
          stackId="attendance"
          fill={`url(#${prefix}-${series})`}
          stroke="var(--card)"
          strokeWidth={1}
          isAnimationActive={false}
          maxBarSize={40}
        />
      ))}
    </BarChart>
  );

  return (
    <ChartFigure
      heading={t('progress.attendance.heading')}
      summary={summary}
      empty={empty}
      initialView={initialView}
      chart={() => (
        <div className="flex min-w-0 flex-col gap-2">
          <AttendancePatterns prefix={prefix} />
          <ul
            aria-label={t('progress.chart.legend')}
            className="flex flex-wrap gap-x-4 gap-y-1 text-sm"
          >
            {ATTENDANCE_SERIES.map((series) => (
              <li key={series} className="flex items-center gap-1.5">
                <svg
                  aria-hidden="true"
                  focusable="false"
                  width="16"
                  height="16"
                  className="shrink-0"
                >
                  <rect
                    x="0.5"
                    y="0.5"
                    width="15"
                    height="15"
                    fill={`url(#${prefix}-${series})`}
                    style={{ stroke: COLORS[series] }}
                  />
                </svg>
                {label(series)}
              </li>
            ))}
          </ul>
          <div className="min-w-0" data-chart="attendance">
            {width !== undefined ? (
              chart
            ) : (
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                {chart}
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
      table={() => (
        <DataTable
          caption={t('progress.attendance.caption')}
          columns={[
            t('progress.attendance.columns.month'),
            label('present'),
            label('late'),
            label('absent'),
            label('not_recorded'),
            t('progress.attendance.columns.total'),
          ]}
          rows={data.map((month) => ({
            key: month.month,
            cells: [
              month.label,
              String(month.present),
              String(month.late),
              String(month.absent),
              String(month.not_recorded),
              String(month.sessions),
            ],
          }))}
        />
      )}
    />
  );
}
