import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AXIS_TICK,
  CHART_HEIGHT,
  GRID_STROKE,
  svgId,
  TOOLTIP_PROPS,
} from '@/features/progress/charts/chartStyle';
import { formatMoney } from '@/lib/money';
import type { RevenueTrend } from '../api';
import { useKpiFormatters } from '../format';
import { ChartDataTable, ChartLegend, type LegendItem } from './parts';

// K1's chart (M8-09b E3, the #322 rules): grouped bars per month for revenue,
// direct tutor cost and operating expenses, each told apart by a fill
// pattern (solid, diagonal hatch, dots) as well as by the --chart tokens,
// and a net-result line whose negative points are drawn as a downward
// triangle (never colour alone). No animation, the accessibility layer on,
// and a visually hidden data table as the text alternative. Recharts plots
// numbers; every amount shown as text is the API's string.

const BARS = ['revenue', 'cost', 'expenses'] as const;
type BarSeries = (typeof BARS)[number];

const COLORS: Record<BarSeries | 'net', string> = {
  revenue: 'var(--chart-1)',
  cost: 'var(--chart-2)',
  expenses: 'var(--chart-3)',
  net: 'var(--chart-4)',
};

const L = 'reports.kpis.revenueTrend';

function Patterns({ prefix }: { prefix: string }) {
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
          id={`${prefix}-revenue`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
        >
          <rect width="6" height="6" style={{ fill: COLORS.revenue }} />
        </pattern>
        <pattern
          id={`${prefix}-cost`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width="6" height="6" style={{ fill: 'var(--card)' }} />
          <rect width="3" height="6" style={{ fill: COLORS.cost }} />
        </pattern>
        <pattern
          id={`${prefix}-expenses`}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
        >
          <rect width="6" height="6" style={{ fill: 'var(--card)' }} />
          <circle cx="3" cy="3" r="2" style={{ fill: COLORS.expenses }} />
        </pattern>
      </defs>
    </svg>
  );
}

interface DotProps {
  cx?: number;
  cy?: number;
  index?: number;
  payload?: { negative?: boolean };
}

/** A circle, or a downward triangle for a negative net result. */
function NetDot({ cx, cy, index, payload }: DotProps) {
  if (cx === undefined || cy === undefined) return null;
  const negative = payload?.negative === true;
  return negative ? (
    <path
      key={index}
      d={`M${cx - 5},${cy - 4}L${cx + 5},${cy - 4}L${cx},${cy + 5}Z`}
      style={{ fill: COLORS.net, stroke: 'var(--card)' }}
      data-net-dot="negative"
    />
  ) : (
    <circle
      key={index}
      cx={cx}
      cy={cy}
      r={4}
      style={{ fill: COLORS.net, stroke: 'var(--card)' }}
      data-net-dot="positive"
    />
  );
}

export interface RevenueTrendChartProps {
  trend: RevenueTrend;
  /** A fixed width instead of the container's (tests). */
  width?: number;
}

export function RevenueTrendChart({ trend, width }: RevenueTrendChartProps) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const prefix = svgId(useId());
  const captionId = useId();
  const label = (series: BarSeries | 'net') => t(`${L}.series.${series}`);
  const data = trend.months.map((month) => ({
    key: month.month,
    label: format.month(month.month),
    revenue: Number(month.revenue_total),
    cost: Number(month.direct_tutor_cost_total),
    expenses: Number(month.operating_expenses_total),
    net: Number(month.net_result),
    negative: Number(month.net_result) < 0,
  }));
  const legend: LegendItem[] = [
    ...BARS.map((series) => ({
      key: series,
      label: label(series),
      swatch: (
        <rect
          x="0.5"
          y="0.5"
          width="15"
          height="15"
          fill={`url(#${prefix}-${series})`}
          style={{ stroke: COLORS[series] }}
        />
      ),
    })),
    {
      key: 'net',
      label: label('net'),
      swatch: (
        <>
          <line
            x1="0"
            y1="8"
            x2="16"
            y2="8"
            style={{ stroke: COLORS.net, strokeWidth: 2 }}
          />
          <circle cx="8" cy="8" r="3.5" style={{ fill: COLORS.net }} />
        </>
      ),
    },
    {
      key: 'negative',
      label: t(`${L}.series.negative`),
      swatch: <path d="M3,4L13,4L8,13Z" style={{ fill: COLORS.net }} />,
    },
  ];

  const chart = (
    <ComposedChart
      data={data}
      accessibilityLayer
      margin={{ top: 8, right: 8, bottom: 0, left: 8 }}
      {...(width !== undefined ? { width, height: CHART_HEIGHT } : {})}
    >
      <CartesianGrid vertical={false} stroke={GRID_STROKE} />
      <XAxis
        dataKey="label"
        tick={AXIS_TICK}
        stroke={GRID_STROKE}
        interval="preserveStartEnd"
      />
      <YAxis
        tick={AXIS_TICK}
        stroke={GRID_STROKE}
        width={72}
        tickFormatter={(value: number) =>
          Number.isFinite(value) ? formatMoney(value.toFixed(0)) : ''
        }
      />
      <ReferenceLine y={0} stroke={GRID_STROKE} />
      <Tooltip
        {...TOOLTIP_PROPS}
        formatter={(value) =>
          typeof value === 'number' && Number.isFinite(value)
            ? formatMoney(value.toFixed(2))
            : String(value)
        }
      />
      {BARS.map((series) => (
        <Bar
          key={series}
          dataKey={series}
          name={label(series)}
          fill={`url(#${prefix}-${series})`}
          stroke={COLORS[series]}
          strokeWidth={1}
          isAnimationActive={false}
          maxBarSize={32}
        />
      ))}
      <Line
        dataKey="net"
        name={label('net')}
        type="linear"
        stroke={COLORS.net}
        strokeWidth={2}
        dot={(props: DotProps) => <NetDot {...props} />}
        activeDot={false}
        isAnimationActive={false}
      />
    </ComposedChart>
  );

  return (
    <figure
      aria-labelledby={captionId}
      className="flex min-w-0 flex-col gap-2"
      data-kpi-chart="revenue-trend"
    >
      <figcaption id={captionId} className="text-sm font-medium">
        {t(`${L}.chartCaption`)}
      </figcaption>
      <Patterns prefix={prefix} />
      <ChartLegend items={legend} />
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
        columns={[
          t(`${L}.columns.month`),
          label('revenue'),
          label('cost'),
          label('expenses'),
          label('net'),
        ]}
        rows={trend.months.map((month) => ({
          key: month.month,
          cells: [
            format.month(month.month),
            formatMoney(month.revenue_total),
            formatMoney(month.direct_tutor_cost_total),
            formatMoney(month.operating_expenses_total),
            formatMoney(month.net_result),
          ],
        }))}
      />
    </figure>
  );
}
