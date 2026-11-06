import { useQueryClient } from '@tanstack/react-query';
import { Settings2 } from 'lucide-react';
import { lazy, Suspense, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useSession } from '@/features/auth/useSession';
import {
  RevenueTrendSection,
  useCourseProfitability,
  useKpiBalances,
  useRevenueTrend,
  useRoomUtilization,
  useStudentRetention,
  useTutorPerformance,
} from '@/features/reporting/kpis';
import { RangeControl } from '@/features/reporting/RangeControl';
import { isRangeRefusal, refusedField } from '@/features/reporting/reportState';
import { activePreset } from '@/features/reporting/rules';
import { useReportRange } from '@/features/reporting/useReportRange';
import { useFormatters } from '@/lib/formatters';
import {
  fetchLayout,
  LAYOUT_KEY,
  resetLayout,
  saveLayout,
  useDashboardLayout,
  type LayoutEntry,
  type WidgetCode,
} from './api';
import type { DialogEntry } from './CustomizeDialog';
import { defaultLayout, mayShow, widgetOf, type Widget } from './widgets';

// The staff dashboard on the home page (M8-10 D3–D5, #127 E2–E4). One
// range control (URL `from`/`to`, default "Last 3 months", as the KPI
// page), then the visible widgets of the caller's layout in order, each
// the M8-09b KPI section unchanged (its own card, h2, query, loading,
// error and empty states; a 403 hides it). A widget shows only when the
// layout lists it as visible **and** its KPI requirement passes; hidden
// widgets are not mounted, so they ask nothing. "Customize" opens the
// dialog (its own chunk, with dnd-kit). The range and the basis are not
// saved.

const CustomizeDialog = lazy(async () => ({
  default: (await import('./CustomizeDialog')).CustomizeDialog,
}));

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function DashboardPage() {
  const { t } = useTranslation();
  // The home page: the bare brand title, as `HomePage`.
  useDocumentTitle();
  const { formatLocalDate } = useFormatters();
  const queryClient = useQueryClient();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;

  const layout = useDashboardLayout();
  const forbidden = isForbidden(layout.error);
  const failed = layout.isError && !forbidden && layout.data === undefined;
  // D5: on a failure, the role default from the web registry.
  const entries: LayoutEntry[] = layout.data
    ? layout.data.widgets
    : failed
      ? defaultLayout(user)
      : [];
  // D4, F3: listed, visible and allowed; an unknown code is ignored.
  const known = entries.filter((entry) => mayShow(user, entry.widget));
  const shown = known.filter((entry) => entry.visible);
  const isShown = (code: WidgetCode) =>
    shown.some((entry) => entry.widget === code);

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

  // The same queries as the shown widgets (one request per key), only to
  // re-anchor "today" and to show a range refusal at its field, as on the
  // KPI page. A hidden widget's query is never enabled.
  const trend = useRevenueTrend(
    { basis: params.basis, ...range },
    asked && isShown('revenue_trend'),
  );
  const courses = useCourseProfitability(
    range,
    asked && isShown('course_profitability'),
  );
  const balances = useKpiBalances(isShown('balances'));
  const rooms = useRoomUtilization(range, asked && isShown('room_utilization'));
  const retention = useStudentRetention(
    range,
    asked && isShown('student_retention'),
  );
  const tutors = useTutorPerformance(
    range,
    asked && isShown('tutor_performance'),
  );
  latchToday(balances.data?.today ?? trend.data?.today ?? courses.data?.today);

  const refusal = asked
    ? [trend, courses, rooms, retention, tutors]
        .map((query) => query.error)
        .find(isRangeRefusal)
    : undefined;
  const rangeError = draftError
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

  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogLoaded, setDialogLoaded] = useState(false);

  const titled = (list: readonly LayoutEntry[]): DialogEntry[] =>
    list
      .filter((entry) => mayShow(user, entry.widget))
      .map((entry) => ({
        widget: entry.widget,
        visible: entry.visible,
        title: t(widgetOf(entry.widget)!.titleKey),
      }));

  function renderWidget(widget: Widget) {
    if (widget.kind === 'trend') {
      return asked ? (
        <RevenueTrendSection
          {...range}
          basis={params.basis}
          onBasis={onBasis}
        />
      ) : null;
    }
    if (widget.kind === 'today') {
      return <widget.Component {...range} />;
    }
    return asked ? <widget.Component {...range} /> : null;
  }

  return (
    <div className="flex min-w-0 flex-col gap-6" data-dashboard-page>
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-2">
          <h1 className="text-2xl font-semibold">{t('dashboard.title')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('dashboard.intro')}
          </p>
        </div>
        {forbidden ? null : (
          <Button
            type="button"
            variant="outline"
            className="min-h-11 self-start md:min-h-9"
            disabled={layout.data === undefined}
            onClick={() => {
              setDialogLoaded(true);
              setDialogOpen(true);
            }}
          >
            <Settings2 aria-hidden="true" />
            {t('dashboard.customize')}
          </Button>
        )}
      </div>
      <RangeControl
        draft={draft}
        onDate={onDate}
        preset={activePreset(params, today)}
        onPreset={onPreset}
        today={today}
        error={rangeError}
      />
      <p role="status" aria-live="polite" className="sr-only" data-kpi-range>
        {asked
          ? t('reports.kpis.rangeShown', {
              from: formatLocalDate(params.from),
              to: formatLocalDate(params.to),
            })
          : ''}
      </p>
      {failed ? (
        <div data-dashboard-layout-error>
          <ErrorAlert
            error={layout.error}
            message={t('dashboard.layoutError')}
            detail={t('dashboard.layoutErrorDetail')}
            onRetry={() => void layout.refetch()}
          />
        </div>
      ) : null}
      {layout.isPending && !failed ? (
        <div data-dashboard-loading>
          <LoadingState label={t('dashboard.loading')} />
        </div>
      ) : null}
      {!layout.isPending || failed ? (
        shown.length === 0 ? (
          <div
            className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-border p-6"
            data-dashboard-empty
          >
            <p className="font-medium">{t('dashboard.empty')}</p>
            {forbidden ? null : (
              <p className="text-sm text-muted-foreground">
                {t('dashboard.emptyHint')}
              </p>
            )}
          </div>
        ) : (
          <div className="flex min-w-0 flex-col gap-6" data-dashboard-widgets>
            {shown.map((entry) => {
              const widget = widgetOf(entry.widget)!;
              return (
                <div
                  key={entry.widget}
                  className="contents"
                  data-dashboard-widget={entry.widget}
                >
                  {renderWidget(widget)}
                </div>
              );
            })}
          </div>
        )
      ) : null}
      {dialogLoaded && layout.data ? (
        <Suspense fallback={null}>
          <CustomizeDialog
            open={dialogOpen}
            entries={titled(layout.data.widgets)}
            onClose={() => setDialogOpen(false)}
            onSave={async (widgets) => {
              const saved = await saveLayout(widgets);
              queryClient.setQueryData(LAYOUT_KEY, saved);
            }}
            onReset={async () => {
              await resetLayout();
              await queryClient.invalidateQueries({ queryKey: LAYOUT_KEY });
            }}
            onUnavailable={async () => {
              const fresh = await queryClient.fetchQuery({
                queryKey: LAYOUT_KEY,
                queryFn: ({ signal }) => fetchLayout(signal),
                staleTime: 0,
              });
              return titled(fresh.widgets);
            }}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
