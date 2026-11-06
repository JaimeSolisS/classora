import { useTranslation } from 'react-i18next';
import { Money } from '../parts';
import type { CourseFigures, CourseProfitability } from './api';
import { isZeroAmount } from './format';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { KpiTable } from './KpiTable';
import { useCourseProfitability } from './queries';
import { kpiState, type KpiState } from './state';

// K2 "Profitability by course" (M8-09b C3; _docs/kpis.md K2): the cohorts
// in the API's order (gross profit descending), "No cohort assigned" when
// course revenue or cost is tied to none, and a bold total equal to the
// financial report's "Courses" row. Accrual only, no overhead.

const L = 'reports.kpis.courseProfitability';

const hasFigures = (row: CourseFigures) =>
  !isZeroAmount(row.revenue) || !isZeroAmount(row.direct_tutor_cost);

function isEmptyCourses(report: CourseProfitability): boolean {
  return report.cohorts.length === 0 && !hasFigures(report.total);
}

export function CourseProfitabilityView({
  state,
}: {
  state: KpiState<CourseProfitability>;
}) {
  const { t } = useTranslation();
  const cells = (row: CourseFigures) => [
    <Money key="revenue" amount={row.revenue} />,
    <Money key="cost" amount={row.direct_tutor_cost} />,
    <Money key="gross" amount={row.gross_profit} />,
  ];
  return (
    <KpiSection
      kpi="course-profitability"
      title={t(`${L}.title`)}
      state={state}
      isEmpty={isEmptyCourses}
    >
      {(report) => (
        <div className="flex min-w-0 flex-col gap-2">
          <KpiTable
            data="course-profitability"
            caption={t(`${L}.caption`)}
            columns={[
              t(`${L}.columns.cohort`),
              t(`${L}.columns.revenue`),
              t(`${L}.columns.cost`),
              t(`${L}.columns.grossProfit`),
            ]}
            rows={[
              ...report.cohorts.map((row) => ({
                key: row.cohort.id,
                header: row.cohort.name,
                cells: cells(row),
              })),
              ...(hasFigures(report.not_attributed)
                ? [
                    {
                      key: 'not-attributed',
                      header: (
                        <span className="italic">
                          {t(`${L}.notAttributed`)}
                        </span>
                      ),
                      cells: cells(report.not_attributed),
                    },
                  ]
                : []),
            ]}
            total={{
              key: 'total',
              header: t(`${L}.total`),
              cells: cells(report.total),
            }}
          />
          <p className={NOTE_CLASS} data-kpi-note="courses">
            {t(`${L}.note`)}
          </p>
        </div>
      )}
    </KpiSection>
  );
}

/** K2 with its own query (#127 mounts it as a widget). */
export function CourseProfitabilitySection({
  from,
  to,
}: {
  from: string;
  to: string;
}) {
  const query = useCourseProfitability({ from, to });
  return <CourseProfitabilityView state={kpiState(query)} />;
}
