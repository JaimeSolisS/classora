import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import {
  commitImport,
  discardImport,
  fetchImport,
  fetchImportRows,
  IMPORTS_LIST_KEY,
  importKey,
  importRowsKey,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
  type ImportJob,
} from './api';
import { ConfirmImportDialog } from './ConfirmImportDialog';
import { ImportStatusBadge } from './ImportBadges';
import { ImportRowsTable, type ImportRowsState } from './ImportRowsTable';
import { isImportKind } from './kinds';
import { isOpen, rowFilters, rowParams, showsOutcome } from './rules';
import { SELECT_CLASS, TEXT_LINK_CLASS } from './styles';

// /imports/:importId (M8-05b C, E3): the summary, the rows with a status
// filter (`?rows=` and `?page=` in the URL), "Import N rows" and "Discard"
// behind confirmations, and the commit's progress: while the job is
// `committing` the page polls it every 3 s, stops on any other status or
// after 10 minutes ("still in progress"), and announces the outcome in a
// polite live region.

type Pending = 'commit' | 'discard' | null;

export function ImportDetailPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('imports.detail.title'));
  const { importId = '' } = useParams();
  const queryClient = useQueryClient();
  const [timedOut, setTimedOut] = useState(false);

  const job = useQuery({
    queryKey: importKey(importId),
    queryFn: ({ signal }) => fetchImport(importId, signal),
    // E3: poll while committing, until another status or the time limit.
    refetchInterval: (query) =>
      query.state.data?.status === 'committing' && !timedOut
        ? POLL_INTERVAL_MS
        : false,
  });
  const status = job.data?.status;

  // The announcement of the commit's outcome (E3), when this page saw the
  // job leave `committing`.
  const [announcement, setAnnouncement] = useState('');
  const [seenStatus, setSeenStatus] = useState(status);
  if (status !== seenStatus) {
    setSeenStatus(status);
    setTimedOut(false);
    if (seenStatus === 'committing' && job.data) {
      if (status === 'committed') {
        setAnnouncement(
          t('imports.detail.doneAnnouncement', {
            created: job.data.counts.created,
            skipped: job.data.counts.skipped,
            failed: job.data.counts.failed,
          }),
        );
      } else if (status === 'failed') {
        setAnnouncement(t('imports.detail.failedAnnouncement'));
      }
    }
  }

  useEffect(() => {
    if (status !== 'committing') {
      return;
    }
    const timer = window.setTimeout(() => setTimedOut(true), POLL_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [status]);

  const live =
    status === 'committing' && timedOut
      ? t('imports.detail.stillRunning')
      : announcement;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <p>
        <Link to="/imports" className={TEXT_LINK_CLASS}>
          {t('imports.detail.back')}
        </Link>
      </p>
      <p role="status" aria-live="polite" className="empty:hidden">
        {live}
      </p>
      {job.data ? (
        <ImportReport
          job={job.data}
          onAnnounce={setAnnouncement}
          onChanged={() => {
            void job.refetch();
            void queryClient.invalidateQueries({ queryKey: IMPORTS_LIST_KEY });
          }}
        />
      ) : job.isError ? (
        <>
          <h1 className="text-2xl font-semibold">
            {t('imports.detail.title')}
          </h1>
          <ErrorAlert error={job.error} onRetry={() => void job.refetch()} />
        </>
      ) : (
        <LoadingState />
      )}
    </div>
  );
}

interface ImportReportProps {
  job: ImportJob;
  onAnnounce: (text: string) => void;
  /** Reloads the job (after a conflict) and the history. */
  onChanged: () => void;
}

function ImportReport({ job, onAnnounce, onChanged }: ImportReportProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const queryClient = useQueryClient();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    rowsTitle: `${id}-rows`,
    filter: `${id}-filter`,
  };
  const heading = useRef<HTMLHeadingElement>(null);
  const commitButton = useRef<HTMLButtonElement>(null);
  const discardButton = useRef<HTMLButtonElement>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [actionError, setActionError] = useState<{
    error: unknown;
    message?: string;
  } | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = rowFilters(job.status);
  const params = rowParams(searchParams, filters);

  const rowsGone = job.status === 'discarded' || job.status === 'expired';
  const rows = useQuery({
    queryKey: importRowsKey(job.id, job.version, params),
    queryFn: ({ signal }) => fetchImportRows(job.id, params, signal),
    placeholderData: keepPreviousData,
    enabled: !rowsGone,
  });
  const rowsState: ImportRowsState = rows.data
    ? { status: 'ready', page: rows.data }
    : rows.isError
      ? {
          status: 'error',
          error: rows.error,
          onRetry: () => void rows.refetch(),
        }
      : { status: 'loading' };

  function setRowFilter(value: string) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) {
        next.set('rows', value);
      } else {
        next.delete('rows');
      }
      next.delete('page');
      return next;
    });
  }

  function goToPage(page: number) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (page > 1) {
        next.set('page', String(page));
      } else {
        next.delete('page');
      }
      return next;
    });
  }

  function closeDialog(confirmed: boolean) {
    const trigger =
      pending === 'commit' ? commitButton.current : discardButton.current;
    setPending(null);
    // After a confirm the buttons may be gone: focus the summary.
    window.setTimeout(() => {
      if (!confirmed && trigger?.isConnected) {
        trigger.focus();
      } else {
        heading.current?.focus();
      }
    }, 0);
  }

  async function run(action: 'commit' | 'discard') {
    setActionError(null);
    try {
      const updated =
        action === 'commit'
          ? await commitImport(job.id, job.version)
          : await discardImport(job.id, job.version);
      queryClient.setQueryData(importKey(job.id), updated);
      void queryClient.invalidateQueries({ queryKey: IMPORTS_LIST_KEY });
      onAnnounce(
        action === 'commit'
          ? t('imports.detail.committingAnnouncement')
          : t('imports.detail.discardedAnnouncement'),
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setActionError({ error, message: t('imports.detail.changed') });
        onChanged();
      } else {
        setActionError({ error });
      }
    }
    closeDialog(true);
  }

  const outcome = showsOutcome(job);
  const kindLabel = isImportKind(job.kind)
    ? t(`imports.kinds.${job.kind}`)
    : job.kind;
  const counts: [string, number][] = [
    [t('imports.counts.total'), job.counts.total],
    [t('imports.counts.ready'), job.counts.ready],
    [t('imports.counts.duplicate'), job.counts.duplicate],
    [t('imports.counts.error'), job.counts.error],
    ...(outcome
      ? ([
          [t('imports.counts.created'), job.counts.created],
          [t('imports.counts.skipped'), job.counts.skipped],
          [t('imports.counts.failed'), job.counts.failed],
        ] as [string, number][])
      : []),
  ];
  const canCommit = isOpen(job) && job.counts.ready > 0;

  return (
    <>
      <section aria-labelledby={ids.title} className="flex flex-col gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          {/* A long file name wraps anywhere: the page never scrolls
              sideways at 320 px (D1). */}
          <h1
            id={ids.title}
            ref={heading}
            tabIndex={-1}
            className="max-w-full min-w-0 text-2xl font-semibold [overflow-wrap:anywhere] outline-none"
          >
            {job.file_name ?? t('imports.history.noFileName')}
          </h1>
          <ImportStatusBadge status={job.status} />
        </div>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm sm:max-w-xl">
          <dt className="font-medium">{t('imports.detail.kind')}</dt>
          <dd>{kindLabel}</dd>
          <dt className="font-medium">{t('imports.detail.createdBy')}</dt>
          <dd>
            {t('imports.detail.byAt', {
              name: job.created_by.display_name,
              at: formatInstant(job.created_at, 'dateTime'),
            })}
          </dd>
          {job.committed_by && job.committed_at ? (
            <>
              <dt className="font-medium">{t('imports.detail.committedBy')}</dt>
              <dd>
                {t('imports.detail.byAt', {
                  name: job.committed_by.display_name,
                  at: formatInstant(job.committed_at, 'dateTime'),
                })}
              </dd>
            </>
          ) : null}
        </dl>
        <dl
          aria-label={t('imports.detail.countsLabel')}
          className="flex flex-wrap gap-3"
        >
          {counts.map(([label, value]) => (
            <div
              key={label}
              className="flex min-w-24 flex-col rounded-md border border-border px-3 py-2"
            >
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
        {job.ignored_columns.length > 0 ? (
          <p className="rounded-md border border-dashed border-border p-3 text-sm">
            <span className="font-medium">
              {t('imports.detail.warningLabel')}
            </span>{' '}
            {t('imports.detail.ignoredColumns', {
              columns: job.ignored_columns.join(', '),
            })}
          </p>
        ) : null}
        {isOpen(job) && job.expires_at ? (
          <p className="text-sm">
            {t('imports.detail.expires', {
              date: formatInstant(job.expires_at, 'date'),
            })}
          </p>
        ) : null}
        {job.status === 'committed' ? (
          <p className="text-sm">{t('imports.detail.valuesCleared')}</p>
        ) : null}
        {actionError ? (
          <ErrorAlert error={actionError.error} message={actionError.message} />
        ) : null}
        {isOpen(job) ? (
          <div className="flex flex-wrap gap-3">
            {canCommit ? (
              <Button
                ref={commitButton}
                type="button"
                className="min-h-11 md:min-h-9"
                onClick={() => setPending('commit')}
              >
                {t('imports.detail.commit', { count: job.counts.ready })}
              </Button>
            ) : null}
            <Button
              ref={discardButton}
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              onClick={() => setPending('discard')}
            >
              {t('imports.detail.discard')}
            </Button>
          </div>
        ) : null}
      </section>

      {/* Not a named landmark: the rows table names its own scroll region
          after the same heading on a phone (axe landmark-unique). */}
      <section className="flex min-w-0 flex-col gap-4">
        <h2 id={ids.rowsTitle} className="text-xl font-semibold">
          {t('imports.rows.title')}
        </h2>
        {rowsGone ? (
          <p>
            {t(
              `imports.rows.gone.${job.status === 'expired' ? 'expired' : 'discarded'}`,
            )}
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-2 sm:max-w-xs">
              <label htmlFor={ids.filter} className="text-sm font-medium">
                {t('imports.rows.filter')}
              </label>
              <select
                id={ids.filter}
                className={cn(SELECT_CLASS, 'min-h-11 md:min-h-9')}
                value={params.status}
                onChange={(event) => setRowFilter(event.target.value)}
              >
                <option value="">{t('imports.rows.filters.all')}</option>
                {filters.map((filter) => (
                  <option key={filter} value={filter}>
                    {t(`imports.rows.filters.${filter}`)}
                  </option>
                ))}
              </select>
            </div>
            <ImportRowsTable
              state={rowsState}
              kind={job.kind}
              labelledBy={ids.rowsTitle}
              onPage={goToPage}
            />
          </>
        )}
      </section>

      <ConfirmImportDialog
        open={pending === 'commit'}
        title={t('imports.detail.commitTitle', { count: job.counts.ready })}
        description={t('imports.detail.commitDescription', {
          count: job.counts.ready,
        })}
        confirmLabel={t('imports.detail.commitConfirm')}
        busyLabel={t('imports.detail.committing')}
        onConfirm={() => run('commit')}
        onCancel={() => closeDialog(false)}
      />
      <ConfirmImportDialog
        open={pending === 'discard'}
        title={t('imports.detail.discardTitle')}
        description={t('imports.detail.discardDescription')}
        confirmLabel={t('imports.detail.discardConfirm')}
        busyLabel={t('imports.detail.discarding')}
        destructive
        onConfirm={() => run('discard')}
        onCancel={() => closeDialog(false)}
      />
    </>
  );
}
