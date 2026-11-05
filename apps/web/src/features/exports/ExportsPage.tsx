import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
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
  requestExport,
  type ExportJob,
  type ExportJobPage,
} from './api';
import { ExportForm, type KnownDataset } from './ExportForm';
import { ExportHistory, type ExportHistoryState } from './ExportHistory';
import { valuesFromParameters, type ExportFormValues } from './rules';
import { SavedSetups, type SavedSetupsState } from './SavedSetups';
import type { SetupFields } from './SaveSetupDialog';
import {
  deleteSetup,
  fetchSetups,
  SETUPS_KEY,
  updateSetup,
  type ReportSetup,
} from './setups';

// /exports, "Exportaciones" (M8-07b): the request form, then the caller's
// own exports. The datasets come from `GET /exports/datasets` (only those
// the caller may export, A2); `?dataset=` preselects one (the "Export"
// links of /students, /schedule and /expenses/records, A3), and an unknown
// or forbidden value falls back to the first. A 403 is the standard
// no-access page. E2: while any listed export is queued, the history polls
// every 3 s, for at most 10 minutes ("still in progress; you'll get a
// notification"), and a polite live region announces each export this page
// saw finish ("ready" or "failed").
//
// M8-08 (#125 D): "Configuraciones guardadas" lists the saved setups.
// Ejecutar posts a setup's parameters unchanged through the same request
// call as the form (apiCall's password dialog, the 3-export limit, the
// history update); Cargar en el formulario fills the form with them (a new
// form instance) and moves the focus to it. Saving, updating, running and
// deleting are announced in the polite live region.

export function ExportsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('exports.title'));
  const timeZone = useCenterTimeZone();
  const queryClient = useQueryClient();
  const id = useId();
  const ids = {
    newTitle: `${id}-new`,
    historyTitle: `${id}-history`,
    setupsTitle: `${id}-setups`,
  };
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [timedOut, setTimedOut] = useState(false);
  // Bumped by each request, so the 10-minute limit starts again.
  const [requests, setRequests] = useState(0);
  const [announcement, setAnnouncement] = useState('');
  const formHeading = useRef<HTMLHeadingElement>(null);
  const [loaded, setLoaded] = useState<{
    nonce: number;
    dataset: KnownDataset['code'];
    values: ExportFormValues;
    setupId: string | null;
  } | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [setupNotice, setSetupNotice] = useState<string | null>(null);
  const [setupFailure, setSetupFailure] = useState<unknown>(null);

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
  const setups = useQuery({
    queryKey: SETUPS_KEY,
    queryFn: ({ signal }) => fetchSetups(signal),
    enabled: datasets.isSuccess,
  });

  // "Cargar en el formulario" moves the focus to the new form's heading.
  const loadedNonce = loaded?.nonce;
  useEffect(() => {
    if (loadedNonce !== undefined) {
      formHeading.current?.focus();
    }
  }, [loadedNonce]);

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

  // The loaded setup as the list has it now (its latest version), when it
  // is still the caller's: the form then offers "Actualizar".
  const loadedSetup =
    loaded?.setupId != null
      ? (setups.data?.items.find(
          (setup) => setup.id === loaded.setupId && setup.mine,
        ) ?? null)
      : null;

  function reloadSetups() {
    void queryClient.invalidateQueries({ queryKey: SETUPS_KEY });
  }

  async function runSetup(setup: ReportSetup) {
    setSetupNotice(null);
    setSetupFailure(null);
    setRunningId(setup.id);
    try {
      onRequested(await requestExport(setup.parameters));
    } catch (error) {
      if (error instanceof ApiError && error.code === 'export_limit_reached') {
        const max = error.problem?.details?.max;
        setSetupNotice(
          t('exports.form.limitReached', {
            max: typeof max === 'number' ? max : 3,
          }),
        );
      } else {
        setSetupFailure(error);
      }
    } finally {
      setRunningId(null);
    }
  }

  function loadSetup(setup: ReportSetup) {
    const dataset = known.find((candidate) => candidate.code === setup.dataset);
    if (!dataset) {
      return;
    }
    setLoaded((previous) => ({
      nonce: (previous?.nonce ?? 0) + 1,
      dataset: dataset.code,
      values: valuesFromParameters(
        setup.parameters,
        dataset.columns,
        todayInZone(timeZone),
        t('exports.setups.savedPerson'),
      ),
      setupId: setup.mine ? setup.id : null,
    }));
    setAnnouncement(t('exports.setups.loaded', { name: setup.name }));
  }

  function onSetupSaved(setup: ReportSetup, how: 'created' | 'updated') {
    setSetupNotice(null);
    setLoaded((previous) =>
      previous ? { ...previous, setupId: setup.id } : previous,
    );
    setAnnouncement(t(`exports.setups.${how}`, { name: setup.name }));
    reloadSetups();
  }

  function onSetupsStale() {
    setSetupNotice(t('exports.setups.stale'));
    reloadSetups();
  }

  async function editSetup(setup: ReportSetup, fields: SetupFields) {
    const updated = await updateSetup(setup.id, {
      version: setup.version,
      name: fields.name,
      shared_role: fields.sharedRole,
    });
    setSetupNotice(null);
    setAnnouncement(t('exports.setups.updated', { name: updated.name }));
    reloadSetups();
  }

  async function removeSetup(setup: ReportSetup) {
    setSetupNotice(null);
    setSetupFailure(null);
    try {
      await deleteSetup(setup.id);
      setAnnouncement(t('exports.setups.deleted', { name: setup.name }));
    } catch (error) {
      setSetupFailure(error);
    }
    reloadSetups();
  }

  const setupsState: SavedSetupsState = setups.data
    ? { status: 'ready', items: setups.data.items }
    : setups.isError
      ? {
          status: 'error',
          error: setups.error,
          onRetry: () => void setups.refetch(),
        }
      : { status: 'loading' };

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
            aria-labelledby={ids.setupsTitle}
            className="flex min-w-0 flex-col gap-4"
          >
            <div className="flex flex-col gap-1">
              <h2 id={ids.setupsTitle} className="text-xl font-semibold">
                {t('exports.setups.title')}
              </h2>
              <p className="text-sm text-muted-foreground">
                {t('exports.setups.intro')}
              </p>
            </div>
            <SavedSetups
              state={setupsState}
              runningId={runningId}
              notice={setupNotice}
              failure={setupFailure}
              onRun={(setup) => void runSetup(setup)}
              onLoad={loadSetup}
              onEdit={editSetup}
              onDelete={removeSetup}
              onStale={onSetupsStale}
            />
          </section>
          <section
            aria-labelledby={ids.newTitle}
            className="flex min-w-0 flex-col gap-4"
          >
            <h2
              id={ids.newTitle}
              ref={formHeading}
              tabIndex={-1}
              className="text-xl font-semibold outline-none"
            >
              {t('exports.form.title')}
            </h2>
            <ExportForm
              // A new `?dataset=` (another page's link) or a loaded setup
              // starts a new form.
              key={`${initialDataset}:${loaded?.nonce ?? 0}`}
              datasets={known}
              initialDataset={loaded?.dataset ?? initialDataset}
              initialValues={loaded?.values}
              loadedSetup={loadedSetup}
              today={todayInZone(timeZone)}
              onRequested={onRequested}
              onSetupSaved={onSetupSaved}
              onSetupsStale={onSetupsStale}
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
