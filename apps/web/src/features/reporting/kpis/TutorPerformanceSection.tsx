import { useTranslation } from 'react-i18next';
import type { ActivityFigures, TutorPerformance } from './api';
import { useKpiFormatters } from './format';
import { Percent } from './Figures';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { KpiTable } from './KpiTable';
import { useTutorPerformance } from './queries';
import { kpiState, type KpiState } from './state';

// K6 "Tutor performance" and K7 "center activity" (M8-09b C6;
// _docs/kpis.md K6): the tutors in the API's order (by name; no sorting
// control or ranking), hours as scheduled minutes / 60 with one decimal,
// and a bold "Center" row with the center-wide totals.

const L = 'reports.kpis.tutors';

function isEmptyTutors(report: TutorPerformance): boolean {
  return (
    report.tutors.length === 0 &&
    report.totals.sessions === 0 &&
    report.totals.cancelled === 0
  );
}

export function TutorPerformanceView({
  state,
}: {
  state: KpiState<TutorPerformance>;
}) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const cells = (row: ActivityFigures) => [
    format.count(row.sessions),
    format.count(row.completed),
    format.count(row.pending_closeout),
    format.count(row.cancelled),
    format.hours(row.scheduled_minutes),
    format.count(row.students),
    <Percent key="attendance" value={row.attendance_percent} />,
  ];
  return (
    <KpiSection
      kpi="tutor-performance"
      title={t(`${L}.title`)}
      state={state}
      isEmpty={isEmptyTutors}
    >
      {(report) => (
        <div className="flex min-w-0 flex-col gap-2">
          <KpiTable
            data="tutor-performance"
            caption={t(`${L}.caption`)}
            columns={[
              t(`${L}.columns.tutor`),
              t(`${L}.columns.sessions`),
              t(`${L}.columns.completed`),
              t(`${L}.columns.pending`),
              t(`${L}.columns.cancelled`),
              t(`${L}.columns.hours`),
              t(`${L}.columns.students`),
              t(`${L}.columns.attendance`),
            ]}
            rows={report.tutors.map((row) => ({
              key: row.tutor.id,
              header: row.tutor.display_name,
              cells: cells(row),
            }))}
            total={{
              key: 'center',
              header: t(`${L}.center`),
              cells: cells(report.totals),
            }}
          />
          <p className={NOTE_CLASS} data-kpi-note="tutors">
            {t(`${L}.note`)}
          </p>
        </div>
      )}
    </KpiSection>
  );
}

/** K6/K7 with its own query (#127 mounts it as a widget). */
export function TutorPerformanceSection({
  from,
  to,
}: {
  from: string;
  to: string;
}) {
  const query = useTutorPerformance({ from, to });
  return <TutorPerformanceView state={kpiState(query)} />;
}
