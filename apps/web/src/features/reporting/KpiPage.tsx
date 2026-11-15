import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { useFormatters } from '@/lib/formatters';
import {
  KPI_FINANCE_ACCESS,
  KPI_OPERATIONS_ACCESS,
  KPI_TUTOR_ACCESS,
} from './access';
import {
  BalancesKpiSection,
  CourseProfitabilitySection,
  RevenueTrendSection,
  RoomUtilizationSection,
  StudentRetentionSection,
  TutorPerformanceSection,
  useCourseProfitability,
  useKpiBalances,
  useRevenueTrend,
  useRoomUtilization,
  useStudentRetention,
  useTutorPerformance,
} from './kpis';
import { RangeControl } from './RangeControl';
import { ReportsSubnav } from './ReportsSubnav';
import { isRangeRefusal, refusedField } from './reportState';
import { activePreset } from './rules';
import { useReportRange } from './useReportRange';

// /reports/kpis "KPIs" (M8-09b; _docs/kpis.md), for holders of
// finance.report.view or schedule.view. One range control (URL `from`/`to`,
// default "Last 3 months") over fixed-order sections, each shown only with
// its API route's permission (#126 E3): K1 revenue trend (with its own
// basis switch, URL `basis`), K3 balances, K2 course profitability, K4 room
// utilization, K5 student retention, K6 tutor performance. Each section
// runs its own query; the page observes the same queries (one request per
// key) only to re-anchor "today" on the API's and to show a `422
// report_range_invalid` next to the field it names. Nothing is stored.

export function KpiPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('reports.kpis.title'));
  const { formatLocalDate } = useFormatters();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const show = {
    finance: canAccess(user, KPI_FINANCE_ACCESS),
    operations: canAccess(user, KPI_OPERATIONS_ACCESS),
    tutors: canAccess(user, KPI_TUTOR_ACCESS),
  };
  const {
    today,
    params,
    draft,
    draftError,
    asked,
    onDate,
    onBasis,
    onPreset,
    latchToday,
  } = useReportRange('last3Months');
  const range = { from: params.from, to: params.to };

  const trend = useRevenueTrend(
    { basis: params.basis, ...range },
    asked && show.finance,
  );
  const courses = useCourseProfitability(range, asked && show.finance);
  const balances = useKpiBalances(show.finance);
  const rooms = useRoomUtilization(range, asked && show.operations);
  const retention = useStudentRetention(range, asked && show.operations);
  const tutors = useTutorPerformance(range, asked && show.tutors);

  // The center's today: the first response that carries one (B1).
  latchToday(balances.data?.today ?? trend.data?.today ?? courses.data?.today);

  const refusal = asked
    ? [trend, courses, rooms, retention, tutors]
        .map((query) => query.error)
        .find(isRangeRefusal)
    : undefined;
  const error = draftError
    ? {
        field: draftError.field,
        message: t(`reports.profitability.range.errors.${draftError.problem}`),
      }
    : refusal
      ? {
          field: refusedField(refusal),
          message: t('reports.profitability.range.errors.server'),
        }
      : null;

  return (
    <div className="flex min-w-0 flex-col gap-6" data-kpi-page>
      <ReportsSubnav />
      <div className="flex min-w-0 flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('reports.kpis.title')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('reports.kpis.intro')}
        </p>
      </div>
      <RangeControl
        draft={draft}
        onDate={onDate}
        preset={activePreset(params, today)}
        onPreset={onPreset}
        today={today}
        error={error}
      />
      <p role="status" aria-live="polite" className="sr-only" data-kpi-range>
        {asked
          ? t('reports.kpis.rangeShown', {
              from: formatLocalDate(params.from),
              to: formatLocalDate(params.to),
            })
          : ''}
      </p>
      {show.finance && asked ? (
        <RevenueTrendSection
          {...range}
          basis={params.basis}
          onBasis={onBasis}
        />
      ) : null}
      {show.finance ? <BalancesKpiSection /> : null}
      {show.finance && asked ? <CourseProfitabilitySection {...range} /> : null}
      {show.operations && asked ? <RoomUtilizationSection {...range} /> : null}
      {show.operations && asked ? <StudentRetentionSection {...range} /> : null}
      {show.tutors && asked ? <TutorPerformanceSection {...range} /> : null}
    </div>
  );
}
