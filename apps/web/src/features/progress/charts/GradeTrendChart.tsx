import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { decimalText } from '@/lib/decimal';
import type { StudentProgress } from '../api';
import { monthLabel } from '../rules';
import { ChartFigure, DataTable } from './ChartFigure';
import { chartValue } from './chartValue';
import { MAX_CHART_SUBJECTS, trendSubjects } from './series';
import {
  AXIS_TICK,
  CHART_HEIGHT,
  GRID_STROKE,
  SERIES_COLORS,
  TOOLTIP_PROPS,
} from './chartStyle';

// "Grade trend" (#322 C2.2, C3): the evaluation average per subject and
// center-local month (grades.by_month), and with "Show homework averages"
// (aria-pressed) the homework averages too. Each series has its own colour
// (--chart-1…4, by subject), dash pattern and marker shape; homework
// series use hollow markers. Y 0–100 %, gaps where a month has no value
// (connectNulls off), no animation, the accessibility layer on. More than
// 4 subjects: the chart view asks for one subject (4 colour tokens); the
// table always lists everything, in API order, as the API's text.

const EVALUATION_DASHES = ['0', '8 4', '2 4', '10 4 2 4'] as const;
const HOMEWORK_DASHES = ['1 3', '6 2 1 2', '4 2', '12 3'] as const;
type MarkerShape = 'circle' | 'square' | 'triangle' | 'diamond';
const MARKERS: readonly MarkerShape[] = [
  'circle',
  'square',
  'triangle',
  'diamond',
];

export interface GradeSeries {
  key: string;
  name: string;
  color: string;
  dash: string;
  marker: MarkerShape;
  hollow: boolean;
}

function Marker({
  cx,
  cy,
  shape,
  color,
  hollow,
}: {
  cx: number;
  cy: number;
  shape: MarkerShape;
  color: string;
  hollow: boolean;
}) {
  const style = {
    fill: hollow ? 'var(--card)' : color,
    stroke: color,
    strokeWidth: 2,
  };
  const r = 4.5;
  switch (shape) {
    case 'square':
      return (
        <rect
          x={cx - r}
          y={cy - r}
          width={r * 2}
          height={r * 2}
          style={style}
        />
      );
    case 'triangle':
      return (
        <path
          d={`M${cx} ${cy - r - 1}L${cx + r + 1} ${cy + r}L${cx - r - 1} ${cy + r}Z`}
          style={style}
        />
      );
    case 'diamond':
      return (
        <path
          d={`M${cx} ${cy - r - 1}L${cx + r + 1} ${cy}L${cx} ${cy + r + 1}L${cx - r - 1} ${cy}Z`}
          style={style}
        />
      );
    default:
      return <circle cx={cx} cy={cy} r={r} style={style} />;
  }
}

/** A legend swatch: the series' dash and marker. */
function Swatch({ series }: { series: GradeSeries }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="32"
      height="14"
      className="shrink-0"
    >
      <line
        x1="1"
        y1="7"
        x2="31"
        y2="7"
        style={{ stroke: series.color, strokeWidth: 2 }}
        strokeDasharray={series.dash}
      />
      <Marker
        cx={16}
        cy={7}
        shape={series.marker}
        color={series.color}
        hollow={series.hollow}
      />
    </svg>
  );
}

interface DotProps {
  cx?: number | null;
  cy?: number | null;
  value?: unknown;
  index?: number;
}

export interface GradeTrendChartProps {
  progress: StudentProgress;
  width?: number;
  initialView?: 'chart' | 'table';
  initialHomework?: boolean;
}

export function GradeTrendChart({
  progress,
  width,
  initialView,
  initialHomework = false,
}: GradeTrendChartProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const [homework, setHomework] = useState(initialHomework);
  const { grades } = progress;
  const subjects = trendSubjects(progress);
  const tooMany = subjects.length > MAX_CHART_SUBJECTS;
  const percentText = (value: string | null) => {
    const text = decimalText(value);
    return text === null
      ? t('progress.noData')
      : t('progress.percent', { value: text });
  };
  const averageText = (value: string | null) => {
    const text = decimalText(value);
    return text === null
      ? t('progress.noDataLower')
      : t('progress.percent', { value: text });
  };
  const summary = [
    t('progress.grades.summaryEvaluations', {
      count: grades.evaluation_count,
      average: averageText(grades.evaluation_average_percent),
    }),
    t('progress.grades.summaryHomework', {
      count: grades.assignment_count,
      average: averageText(grades.assignment_average_percent),
    }),
  ].join(' ');
  const empty =
    grades.by_month.length === 0 ? t('progress.grades.empty') : null;

  const series: GradeSeries[] = subjects.flatMap((subject, index) => {
    const color = SERIES_COLORS[index % SERIES_COLORS.length]!;
    const own: GradeSeries[] = [
      {
        key: `e${index}`,
        name: t('progress.grades.series.evaluation', { subject: subject.name }),
        color,
        dash: EVALUATION_DASHES[index % 4]!,
        marker: MARKERS[index % 4]!,
        hollow: false,
      },
    ];
    if (homework) {
      own.push({
        key: `h${index}`,
        name: t('progress.grades.series.homework', { subject: subject.name }),
        color,
        dash: HOMEWORK_DASHES[index % 4]!,
        marker: MARKERS[index % 4]!,
        hollow: true,
      });
    }
    return own;
  });

  // One row per month of the range (gaps where a subject has no value);
  // the y value is geometry only, the API text rides along for tooltips.
  const months = progress.attendance_by_month.map((month) => month.month);
  for (const row of grades.by_month) {
    if (!months.includes(row.month)) {
      months.push(row.month);
    }
  }
  months.sort();
  const data = months.map((month) => {
    const point: Record<string, string | number | null> = {
      label: monthLabel(month, language),
    };
    subjects.forEach((subject, index) => {
      const row = grades.by_month.find(
        (item) => item.month === month && item.subject.id === subject.id,
      );
      const evaluation = row?.evaluation_average_percent ?? null;
      const assignment = row?.assignment_average_percent ?? null;
      point[`e${index}`] = chartValue(evaluation);
      point[`e${index}text`] = evaluation;
      point[`h${index}`] = chartValue(assignment);
      point[`h${index}text`] = assignment;
    });
    return point;
  });

  const chart = (
    <LineChart
      data={data}
      accessibilityLayer
      margin={{ top: 8, right: 8, bottom: 0, left: -8 }}
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
        domain={[0, 100]}
        ticks={[0, 25, 50, 75, 100]}
        tick={AXIS_TICK}
        stroke={GRID_STROKE}
        tickFormatter={(value: number) => `${value} %`}
      />
      <Tooltip
        {...TOOLTIP_PROPS}
        cursor={{ stroke: 'var(--border)' }}
        formatter={(_value, name, item) => {
          const key = String(item.dataKey);
          const payload = item.payload as Record<string, string | null>;
          return [percentText(payload[`${key}text`] ?? null), name];
        }}
      />
      {series.map((line) => (
        <Line
          key={line.key}
          type="linear"
          dataKey={line.key}
          name={line.name}
          stroke={line.color}
          strokeWidth={2}
          strokeDasharray={line.dash}
          connectNulls={false}
          isAnimationActive={false}
          dot={(props: DotProps) =>
            typeof props.cx === 'number' &&
            typeof props.cy === 'number' &&
            props.value !== null &&
            props.value !== undefined ? (
              <Marker
                key={`${line.key}-${props.index}`}
                cx={props.cx}
                cy={props.cy}
                shape={line.marker}
                color={line.color}
                hollow={line.hollow}
              />
            ) : (
              <g key={`${line.key}-${props.index}`} />
            )
          }
          activeDot={false}
        />
      ))}
    </LineChart>
  );

  return (
    <ChartFigure
      heading={t('progress.grades.heading')}
      summary={summary}
      empty={empty}
      initialView={initialView}
      controls={
        tooMany ? null : (
          <Button
            type="button"
            size="sm"
            variant={homework ? 'default' : 'outline'}
            aria-pressed={homework}
            className="self-start"
            onClick={() => setHomework((value) => !value)}
          >
            {t('progress.grades.showHomework')}
          </Button>
        )
      }
      chart={() =>
        tooMany ? (
          <p className="text-sm">{t('progress.grades.tooManySubjects')}</p>
        ) : (
          <div className="flex min-w-0 flex-col gap-2">
            <ul
              aria-label={t('progress.chart.legend')}
              className="flex flex-wrap gap-x-4 gap-y-1 text-sm"
            >
              {series.map((line) => (
                <li
                  key={line.key}
                  className="flex min-w-0 items-center gap-1.5 break-words"
                >
                  <Swatch series={line} />
                  {line.name}
                </li>
              ))}
            </ul>
            <div className="min-w-0" data-chart="grades">
              {width !== undefined ? (
                chart
              ) : (
                <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                  {chart}
                </ResponsiveContainer>
              )}
            </div>
          </div>
        )
      }
      table={() => (
        <DataTable
          caption={t('progress.grades.caption')}
          columns={[
            t('progress.grades.columns.month'),
            t('progress.grades.columns.subject'),
            t('progress.grades.columns.evaluationAverage'),
            t('progress.grades.columns.evaluations'),
            t('progress.grades.columns.homeworkAverage'),
            t('progress.grades.columns.homeworkGrades'),
          ]}
          rows={grades.by_month.map((row) => ({
            key: `${row.month}-${row.subject.id}`,
            cells: [
              monthLabel(row.month, language),
              row.subject.name,
              percentText(row.evaluation_average_percent),
              String(row.evaluation_count),
              percentText(row.assignment_average_percent),
              String(row.assignment_count),
            ],
          }))}
        />
      )}
    />
  );
}
