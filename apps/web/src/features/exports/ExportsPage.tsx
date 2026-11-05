import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { todayInZone } from '@/lib/time';
import { isExportDataset } from './access';
import {
  EXPORT_DATASETS_KEY,
  EXPORTS_LIST_KEY,
  exportsListKey,
  fetchExportDatasets,
  fetchExports,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
  type ExportJob,
  type ExportJobPage,
} from './api';
import { ExportForm, type KnownDataset } from './ExportForm';
import { ExportHistory, type ExportHistoryState } from './ExportHistory';

// /exports, "Exportaciones" (M8-07b): the request form, then the caller's
// own exports. The datasets come from `GET /exports/datasets` (only those
// the caller may export, A2); `?dataset=` preselects one (the "Export"
// links of /students, /schedule and /expenses/records, A3), and an unknown
// or forbidden value falls back to the first. A 403 is the standard
// no-access page. E2: while any listed export is queued, the history polls
// every 3 s, for at most 10 minutes ("still in progress; you'll get a
// notification"), and a polite live region announces each export this page
// saw finish ("ready" or "failed").

export function ExportsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('exports.title'));
  const timeZone = useCenterTimeZone();
  const queryClient = useQueryClient();
  const id = useId();
  const ids = { newTitle: `${id}-new`, historyTitle: `${id}-history` };
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [timedOut, setTimedOut] = useState(false);
  // Bumped by each request, so the 10-minute limit starts again.
  const [requests, setRequests] = useState(0);
  const [announcement, setAnnouncement] = useState('');

  const datasets = useQuery({
    queryKey: EXPORT_DATASETS_KEY,
    queryFn: ({ signal }) => fetchExportDatasets(signal),
  });
  const list = useQuery({
    queryKey: exportsListKey(page),
    queryFn: ({ signal }) => fetchExports(page, signal),
    placeholderData: keepPreviousData,
    enabled: datasets.isSuccess,
    refetchInterval: (query) =>
      hasQueued(query.state.data) && !timedOut ? POLL_INTERVAL_MS : false,
  });
  const anyQueued = hasQueued(list.data);

  // The outcome of each export this page saw queued (E2): compared with
  // the previous list while rendering, not in an effect.
  const datasetName = (job: ExportJob) =>
    isExportDataset(job.dataset)
      ? t(`exports.datasets.${job.dataset}`)
      : job.dataset;
  const [seen, setSeen] = useState<ExportJobPage | undefined>(undefined);
  if (list.data !== seen) {
    setSeen(list.data);
    const before = new Map(seen?.items.map((job) => [job.id, job.status]));
    const finished = (list.data?.items ?? []).filter(
      (job) => before.get(job.id) === 'queued' && job.status !== 'queued',
    );
    const last = finished.at(-1);
    if (last?.status === 'ready') {
      setAnnouncement(
        t('exports.announce.ready', { dataset: datasetName(last) }),
      );
    } else if (last?.status === 'failed') {
      setAnnouncement(
        t('exports.announce.failed', { dataset: datasetName(last) }),
      );
    }
  }
  const [seenQueued, setSeenQueued] = useState(anyQueued);
  if (anyQueued !== seenQueued) {
    setSeenQueued(anyQueued);
    if (!anyQueued) {
      setTimedOut(false);
    }
  }

  useEffect(() => {
    if (!anyQueued) {
      return;
    }
    const timer = window.setTimeout(() => setTimedOut(true), POLL_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [anyQueued, requests]);

  if (datasets.error instanceof ApiError && datasets.error.status === 403) {
    return <ForbiddenPage />;
  }

  const known: KnownDataset[] = (datasets.data?.items ?? []).filter(
    (dataset): dataset is KnownDataset => isExportDataset(dataset.code),
  );
  const requested = searchParams.get('dataset');
  const initialDataset =
    known.find((dataset) => dataset.code === requested)?.code ?? known[0]?.code;

  function onRequested(job: ExportJob) {
    setAnnouncement(t('exports.form.requested', { dataset: datasetName(job) }));
    setTimedOut(false);
    setRequests((count) => count + 1);
    setPage(1);
    void queryClient.invalidateQueries({ queryKey: EXPORTS_LIST_KEY });
  }

  const historyState: ExportHistoryState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  const live =
    anyQueued && timedOut ? t('exports.announce.stillRunning') : announcement;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('exports.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('exports.intro')}</p>
      </div>
      <p role="status" aria-live="polite" className="empty:hidden">
        {live}
      </p>
      {datasets.isPending ? (
        <LoadingState rows={4} />
      ) : datasets.isError ? (
        <ErrorAlert
          error={datasets.error}
          onRetry={() => void datasets.refetch()}
        />
      ) : initialDataset === undefined ? (
        <p>{t('exports.noDatasets')}</p>
      ) : (
        <>
          <section
            aria-labelledby={ids.newTitle}
            className="flex min-w-0 flex-col gap-4"
          >
            <h2 id={ids.newTitle} className="text-xl font-semibold">
              {t('exports.form.title')}
            </h2>
            <ExportForm
              // A new `?dataset=` (another page's link) starts a new form.
              key={initialDataset}
              datasets={known}
              initialDataset={initialDataset}
              today={todayInZone(timeZone)}
              onRequested={onRequested}
            />
          </section>
          {/* Not a named landmark: the history table names its own
              scroll region after the same heading on a phone. */}
          <section className="flex min-w-0 flex-col gap-4">
            <h2 id={ids.historyTitle} className="text-xl font-semibold">
              {t('exports.history.title')}
            </h2>
            <ExportHistory
              state={historyState}
              labelledBy={ids.historyTitle}
              onPage={setPage}
            />
          </section>
        </>
      )}
    </div>
  );
}

function hasQueued(page: ExportJobPage | undefined): boolean {
  return page?.items.some((job) => job.status === 'queued') ?? false;
}
