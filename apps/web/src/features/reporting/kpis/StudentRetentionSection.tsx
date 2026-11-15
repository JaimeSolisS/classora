import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { StudentRetention } from './api';
import { ActiveStudentsChart } from './charts/ActiveStudentsChart';
import { useKpiFormatters } from './format';
import { Percent } from './Figures';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { useStudentRetention } from './queries';
import { kpiState, type KpiState } from './state';

// K5 "Student retention" (M8-09b C5; _docs/kpis.md K5): tiles for the
// previous and current windows (the previous window's dates in the
// subtitle), the active students per month as bars, and the definition of
// "active". No student is named.

const L = 'reports.kpis.retention';

const TILES = [
  'active_previous',
  'active_current',
  'retained',
  'new',
  'lost',
] as const;

function isEmptyRetention(report: StudentRetention): boolean {
  return (
    TILES.every((key) => report[key] === 0) &&
    report.months.every((month) => month.active_students === 0)
  );
}

export function StudentRetentionView({
  state,
  chartWidth,
}: {
  state: KpiState<StudentRetention>;
  /** A fixed chart width (tests). */
  chartWidth?: number;
}) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const { formatLocalDate } = useFormatters();
  const subtitle =
    state.status === 'ready'
      ? t(`${L}.previousWindow`, {
          from: formatLocalDate(state.data.previous_from),
          to: formatLocalDate(state.data.previous_to),
        })
      : undefined;
  const tile =
    'flex min-w-0 flex-col gap-1 rounded-lg border border-border p-3';
  return (
    <KpiSection
      kpi="student-retention"
      title={t(`${L}.title`)}
      subtitle={subtitle}
      state={state}
      isEmpty={isEmptyRetention}
    >
      {(report) => (
        <div className="flex min-w-0 flex-col gap-4">
          <dl
            className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6"
            data-kpi-tiles
          >
            {TILES.map((key) => (
              <div key={key} className={tile} data-kpi-tile={key}>
                <dt className="text-sm text-muted-foreground">
                  {t(`${L}.tiles.${key}`)}
                </dt>
                <dd className="text-2xl font-semibold tabular-nums">
                  {format.count(report[key])}
                </dd>
              </div>
            ))}
            <div className={tile} data-kpi-tile="retention_percent">
              <dt className="text-sm text-muted-foreground">
                {t(`${L}.tiles.retention_percent`)}
              </dt>
              <dd className="text-2xl font-semibold tabular-nums">
                <Percent
                  value={report.retention_percent}
                  missing={t(`${L}.noPrevious`)}
                />
              </dd>
            </div>
          </dl>
          <ActiveStudentsChart retention={report} width={chartWidth} />
          <p className={NOTE_CLASS} data-kpi-note="active">
            {t(`${L}.note`)}
          </p>
        </div>
      )}
    </KpiSection>
  );
}

/** K5 with its own query (#127 mounts it as a widget). */
export function StudentRetentionSection({
  from,
  to,
}: {
  from: string;
  to: string;
}) {
  const query = useStudentRetention({ from, to });
  return <StudentRetentionView state={kpiState(query)} />;
}
