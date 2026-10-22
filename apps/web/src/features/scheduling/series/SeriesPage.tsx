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
  updateClassSession,
  type SeriesOccurrence,
} from './api';
import { CancelSeriesDialog } from './CancelSeriesDialog';
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
import { SeriesView } from './SeriesView';

// /schedule/series/:seriesId (#54b B1), for schedule.view holders (lazy
// route): the series, and for schedule.manage holders the three dialogs.
// An unknown id shows the shared not-found page. After "Edit this
// occurrence" the series is read again and the row shows "Edited"; after
// "Edit series" the page goes to the resulting series (a new id after a
// split) with the summary sentence; after a cancellation it says how many
// occurrences were cancelled. Every write also refreshes the calendar.

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
  const client = useQueryClient();
  const [localNotice, setLocalNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<SeriesOccurrence | null>(null);
  const [editingSeries, setEditingSeries] = useState(false);
  const [cancelling, setCancelling] = useState(false);

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
            notice={notice}
            onEditOccurrence={(occurrence) => setEditing(occurrence)}
            onEditSeries={() => setEditingSeries(true)}
            onCancelSeries={() => setCancelling(true)}
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
                onSubmit={async (values) => {
                  const result = await editClassSeries(
                    detail.id,
                    seriesEditBody(values, detail.version),
                  );
                  const parts = summaryParts(result.summary).map((part) =>
                    t(`scheduling.series.summary.${part.key}`, {
                      count: part.count,
                    }),
                  );
                  const text =
                    parts.length > 0
                      ? t('scheduling.series.summary.sentence', {
                          parts: parts.join(', '),
                        })
                      : t('scheduling.series.summary.nothing');
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
