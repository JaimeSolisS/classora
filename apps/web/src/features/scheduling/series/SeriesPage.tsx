import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import { todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { fetchClassroomOptions, SCHEDULE_OPTIONS_KEY } from '../create/api';
import {
  fetchClassSeries,
  fetchClassSession,
  seriesKey,
  sessionKey,
} from '../session/api';
import {
  cancelClassSeries,
  editClassSeries,
  overrideConflictOccurrence,
  updateClassSession,
  type SeriesOccurrence,
} from './api';
import { CancelSeriesDialog } from './CancelSeriesDialog';
import { ConflictOccurrenceDialog } from './ConflictOccurrenceDialog';
import { EditOccurrenceDialog } from './EditOccurrenceDialog';
import { EditSeriesDialog } from './EditSeriesDialog';
import {
  cancelBody,
  defaultFromDate,
  occurrenceBody,
  seriesEditBody,
  seriesPath,
  summaryParts,
} from './rules';
import { conflictOverrideBody, overriddenCount } from './overrides';
import { SeriesView } from './SeriesView';

// /schedule/series/:seriesId (#54b B1), for schedule.view holders (lazy
// route): the series, and for schedule.manage holders the three dialogs.
// An unknown id shows the shared not-found page. After "Edit this
// occurrence" the series is read again and the row shows "Edited"; after
// "Edit series" the page goes to the resulting series (a new id after a
// split) with the summary sentence; after a cancellation it says how many
// occurrences were cancelled. Every write also refreshes the calendar.
// Overrides (#236): "Edit series" can resend with every soft pair (the
// summary then says on how many occurrences), and a `conflict` row's
// "Override and schedule" overrides that one occurrence.

interface PageNotice {
  notice?: string;
}

export function SeriesPage() {
  const { seriesId = '' } = useParams();
  // Another series (a lineage link, the series after a split) starts fresh.
  return <SeriesPageContent key={seriesId} seriesId={seriesId} />;
}

function SeriesPageContent({ seriesId }: { seriesId: string }) {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.series.page.title'));
  const location = useLocation();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const canManage = useCan('schedule.manage');
  const canOverride = useCan('schedule.override');
  const client = useQueryClient();
  const [localNotice, setLocalNotice] = useState<string | null>(null);
  const [localNoticeFocus, setLocalNoticeFocus] = useState(false);
  const [editing, setEditing] = useState<SeriesOccurrence | null>(null);
  const [editingSeries, setEditingSeries] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [overriding, setOverriding] = useState<SeriesOccurrence | null>(null);

  const series = useQuery({
    queryKey: seriesKey(seriesId),
    queryFn: ({ signal }) => fetchClassSeries(seriesId, signal),
  });
  const classrooms = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomOptions(signal),
    enabled: canManage && (editing !== null || editingSeries),
  });
  const session = useQuery({
    queryKey: [...sessionKey(editing?.id ?? ''), 'detail'],
    queryFn: ({ signal }) => fetchClassSession(editing?.id ?? '', signal),
    enabled: editing !== null,
  });

  const navigationNotice = (location.state as PageNotice | null)?.notice;
  const notice = localNotice ?? navigationNotice ?? null;
  // After a cancellation or an edit the trigger may be gone (an ended
  // series, a new page): the notice takes the focus. Not after moving one
  // occurrence, whose row button stays.
  const noticeFocus = localNotice
    ? localNoticeFocus
    : Boolean(navigationNotice);

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: seriesKey(seriesId) }),
      client.invalidateQueries({ queryKey: CALENDAR_KEY }),
    ]);
  }

  function reloadOccurrence() {
    void series.refetch();
    void session.refetch();
  }

  if (series.isError) {
    if (series.error instanceof ApiError && series.error.status === 404) {
      return <NotFoundPage />;
    }
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-semibold">
          {t('scheduling.series.page.title')}
        </h1>
        <ErrorAlert
          error={series.error}
          onRetry={() => void series.refetch()}
        />
      </div>
    );
  }

  const detail = series.data;
  const today = todayInZone(timeZone);
  const fromDate = detail ? defaultFromDate(detail, timeZone, today) : today;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">
        {t('scheduling.series.page.title')}
      </h1>
      {!detail ? (
        <LoadingState />
      ) : (
        <>
          <SeriesView
            series={detail}
            timeZone={timeZone}
            canManage={canManage}
            canOverride={canOverride}
            notice={notice}
            focusNotice={noticeFocus}
            onEditOccurrence={(occurrence) => setEditing(occurrence)}
            onEditSeries={() => setEditingSeries(true)}
            onCancelSeries={() => setCancelling(true)}
            onOverrideOccurrence={(occurrence) => setOverriding(occurrence)}
          />
          {canManage ? (
            <>
              <EditOccurrenceDialog
                occurrence={editing}
                session={session.data ?? null}
                sessionError={session.isError ? session.error : undefined}
                classrooms={classrooms.data ?? []}
                timeZone={timeZone}
                onReload={reloadOccurrence}
                onCancel={() => setEditing(null)}
                onSubmit={async (values, version) => {
                  if (!editing) {
                    return;
                  }
                  await updateClassSession(
                    editing.id,
                    occurrenceBody(values, version, timeZone),
                  );
                  await client.invalidateQueries({
                    queryKey: sessionKey(editing.id),
                  });
                  await refresh();
                  setEditing(null);
                  setLocalNoticeFocus(false);
                  setLocalNotice(t('scheduling.series.occurrence.done'));
                }}
              />
              <EditSeriesDialog
                open={editingSeries}
                series={detail}
                defaultFromDate={fromDate}
                classrooms={classrooms.data ?? []}
                timeZone={timeZone}
                onReload={() => void series.refetch()}
                onCancel={() => setEditingSeries(false)}
                canOverride={canOverride}
                onSubmit={async (values, override) => {
                  const result = await editClassSeries(
                    detail.id,
                    seriesEditBody(values, detail.version, override),
                  );
                  const parts = summaryParts(result.summary).map((part) =>
                    t(`scheduling.series.summary.${part.key}`, {
                      count: part.count,
                    }),
                  );
                  const summary =
                    parts.length > 0
                      ? t('scheduling.series.summary.sentence', {
                          parts: parts.join(', '),
                        })
                      : t('scheduling.series.summary.nothing');
                  const overridden = override
                    ? overriddenCount(result.series.occurrences)
                    : 0;
                  const text =
                    overridden > 0
                      ? `${summary} ${t('scheduling.series.override.scheduledWith', { count: overridden })}`
                      : summary;
                  client.setQueryData(
                    seriesKey(result.series.id),
                    result.series,
                  );
                  await refresh();
                  setEditingSeries(false);
                  setLocalNotice(null);
                  void navigate(seriesPath(result.series.id), {
                    state: { notice: text } satisfies PageNotice,
                  });
                }}
              />
              <ConflictOccurrenceDialog
                occurrence={overriding}
                timeZone={timeZone}
                onCancel={() => setOverriding(null)}
                onReload={() => {
                  setOverriding(null);
                  void series.refetch();
                }}
                onSubmit={async (reason) => {
                  if (!overriding) {
                    return;
                  }
                  await overrideConflictOccurrence(
                    overriding.id,
                    conflictOverrideBody(overriding, reason),
                  );
                  await client.invalidateQueries({
                    queryKey: sessionKey(overriding.id),
                  });
                  await refresh();
                  setOverriding(null);
                  // The row's button is gone once it is scheduled.
                  setLocalNoticeFocus(true);
                  setLocalNotice(t('scheduling.series.override.rowDone'));
                }}
              />
              <CancelSeriesDialog
                open={cancelling}
                version={detail.version}
                defaultFromDate={fromDate}
                onReload={() => void series.refetch()}
                onCancel={() => setCancelling(false)}
                onSubmit={async (values) => {
                  const result = await cancelClassSeries(
                    detail.id,
                    cancelBody(values, detail.version),
                  );
                  client.setQueryData(seriesKey(detail.id), result.series);
                  await refresh();
                  setCancelling(false);
                  setLocalNoticeFocus(true);
                  setLocalNotice(
                    t('scheduling.series.cancel.done', {
                      count: result.summary.cancelled,
                    }),
                  );
                }}
              />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
